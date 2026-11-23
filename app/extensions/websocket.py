import json
import logging
import queue
import threading
import time
from typing import Any, Dict, List, Optional

from flask import Blueprint, Response, request, session, stream_with_context

logger = logging.getLogger(__name__)

try:
    from flask_socketio import SocketIO, emit, join_room, leave_room
    HAS_SOCKETIO = True
except ImportError:
    SocketIO = None  # type: ignore
    HAS_SOCKETIO = False


class RealtimeAttendanceHub:
    """
    <<Observer Pattern>> Real-time event hub for broadcasting live attendance updates
    (student PIN check-ins, manual lecturer overrides, PIN regeneration, session open/close)
    to connected lecturer dashboards via both Socket.IO and Server-Sent Events (SSE).
    """

    def __init__(self) -> None:
        self.socketio: Optional[Any] = (
            SocketIO(cors_allowed_origins="*", async_mode="threading")
            if HAS_SOCKETIO
            else None
        )
        self._subscribers: Dict[str, List[queue.Queue]] = {}
        self._lock = threading.Lock()
        self.bp = Blueprint("realtime", __name__, url_prefix="/realtime")
        self._register_routes()
        if self.socketio is not None:
            self._register_socket_events()

    def init_app(self, app) -> None:
        """Bind Socket.IO and the SSE blueprint to the Flask application."""
        if "realtime" not in app.blueprints:
            app.register_blueprint(self.bp)
        if self.socketio is not None:
            try:
                self.socketio.init_app(app, cors_allowed_origins="*", async_mode="threading")
            except Exception as err:
                logger.warning("SocketIO init_app skipped (%s); SSE stream remains active.", err)

    @staticmethod
    def _room_for_session(session_id: Any) -> str:
        return f"session:{str(session_id).strip()}"

    @staticmethod
    def _room_for_section(section_id: Any) -> str:
        return f"section:{str(section_id).strip()}"

    def subscribe(self, channel: str) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=100)
        with self._lock:
            self._subscribers.setdefault(channel, []).append(q)
        return q

    def unsubscribe(self, channel: str, q: queue.Queue) -> None:
        with self._lock:
            subs = self._subscribers.get(channel, [])
            if q in subs:
                subs.remove(q)
            if not subs and channel in self._subscribers:
                self._subscribers.pop(channel, None)

    def _publish_sse(self, channel: str, event: str, payload: Dict[str, Any]) -> None:
        message = {"event": event, "data": payload, "ts": time.time()}
        with self._lock:
            queues = list(self._subscribers.get(channel, []))
        for q in queues:
            try:
                q.put_nowait(message)
            except queue.Full:
                pass

    def broadcast_attendance_event(
        self,
        event_name: str,
        session_id: Optional[Any] = None,
        section_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Broadcast an attendance change to both Socket.IO rooms and SSE subscribers."""
        data = {
            "event": event_name,
            "session_id": session_id,
            "section_id": section_id,
            **(payload or {}),
        }

        if session_id is not None:
            sess_room = self._room_for_session(session_id)
            self._publish_sse(sess_room, event_name, data)
            if self.socketio is not None:
                try:
                    self.socketio.emit(event_name, data, to=sess_room)
                    self.socketio.emit("attendance_update", data, to=sess_room)
                except Exception as err:
                    logger.debug("SocketIO emit to %s failed: %s", sess_room, err)

        if section_id:
            sec_room = self._room_for_section(section_id)
            self._publish_sse(sec_room, event_name, data)
            if self.socketio is not None:
                try:
                    self.socketio.emit(event_name, data, to=sec_room)
                    self.socketio.emit("attendance_update", data, to=sec_room)
                except Exception as err:
                    logger.debug("SocketIO emit to %s failed: %s", sec_room, err)

    def _register_socket_events(self) -> None:
        if self.socketio is None:
            return

        @self.socketio.on("join_attendance_session")
        def _on_join_session(data):
            data = data or {}
            sess_id = data.get("session_id")
            sec_id = data.get("section_id")
            if sess_id:
                join_room(self._room_for_session(sess_id))
            if sec_id:
                join_room(self._room_for_section(sec_id))
            emit("joined_attendance_room", {"session_id": sess_id, "section_id": sec_id})

        @self.socketio.on("leave_attendance_session")
        def _on_leave_session(data):
            data = data or {}
            sess_id = data.get("session_id")
            sec_id = data.get("section_id")
            if sess_id:
                leave_room(self._room_for_session(sess_id))
            if sec_id:
                leave_room(self._room_for_section(sec_id))

    def _register_routes(self) -> None:
        @self.bp.route("/stream", methods=["GET"])
        def sse_stream():
            """Server-Sent Events stream for instant browser notifications without external JS dependencies."""
            if "user_id" not in session:
                return Response("Unauthorized", status=401)

            session_id = (request.args.get("session_id") or "").strip()
            section_id = (request.args.get("section_id") or "").strip()
            channel = (
                self._room_for_session(session_id)
                if session_id
                else (self._room_for_section(section_id) if section_id else "global")
            )

            client_queue = self.subscribe(channel)

            @stream_with_context
            def _generate():
                try:
                    yield f"event: connected\ndata: {json.dumps({'channel': channel})}\n\n"
                    while True:
                        try:
                            msg = client_queue.get(timeout=15)
                            ev = msg.get("event", "attendance_update")
                            body = json.dumps(msg.get("data", {}))
                            yield f"event: {ev}\ndata: {body}\n\n"
                        except queue.Empty:
                            yield ": heartbeat\n\n"
                except GeneratorExit:
                    pass
                finally:
                    self.unsubscribe(channel, client_queue)

            headers = {
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            }
            return Response(_generate(), headers=headers)


realtime_hub = RealtimeAttendanceHub()
socketio = realtime_hub.socketio
