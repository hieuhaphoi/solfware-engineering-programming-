/**
 * Attendly - Real-Time Attendance Observer Client (Socket.IO + SSE Live Stream)
 * Instantly updates the lecturer's roster when a student submits a valid PIN,
 * when a lecturer manually edits a status, or when a PIN is regenerated/closed.
 */
document.addEventListener("DOMContentLoaded", () => {
    const root = document.getElementById("pinSessionRoot");
    if (!root) return;

    const sessionId = (root.dataset.sessionId || "").trim();
    const sectionId = (root.dataset.sectionId || "").trim();
    if (!sessionId && !sectionId) return;

    const countPresent = document.getElementById("countPresent");
    const countLate = document.getElementById("countLate");
    const countExcused = document.getElementById("countExcused");
    const countAbsent = document.getElementById("countAbsent");
    const checkedInRatio = document.getElementById("checkedInRatio");
    const pinDisplay = document.getElementById("activePinCodeDisplay");
    const projectorPin = document.getElementById("projectorPinCode");
    const statusBadge = document.getElementById("sessionStatusBadge");

    function formatTimeShort(isoStr) {
        if (!isoStr) return "—";
        if (isoStr.length >= 16) return isoStr.slice(11, 16);
        return isoStr;
    }

    function applyStatusClass(selectEl, status) {
        const normalized = (status || "ABSENT").toLowerCase();
        selectEl.classList.remove("status-present", "status-late", "status-excused", "status-absent");
        selectEl.classList.add(`status-${normalized}`);
    }

    function highlightRow(row) {
        if (!row) return;
        row.style.transition = "background-color 0.35s ease";
        row.style.backgroundColor = "#E8F6EE";
        setTimeout(() => {
            row.style.backgroundColor = "";
        }, 1600);
    }

    function recalculateCountersFromDOM() {
        const rows = document.querySelectorAll(".roster-row");
        const counts = { PRESENT: 0, LATE: 0, EXCUSED: 0, ABSENT: 0 };
        rows.forEach((row) => {
            const st = (row.dataset.status || "ABSENT").toUpperCase();
            if (counts[st] !== undefined) counts[st] += 1;
        });
        const total = rows.length;
        const checkedIn = counts.PRESENT + counts.LATE;

        if (countPresent) countPresent.textContent = counts.PRESENT;
        if (countLate) countLate.textContent = counts.LATE;
        if (countExcused) countExcused.textContent = counts.EXCUSED;
        if (countAbsent) countAbsent.textContent = counts.ABSENT;
        if (checkedInRatio) checkedInRatio.textContent = `${checkedIn} / ${total}`;
    }

    function handleAttendancePayload(payload) {
        if (!payload || typeof payload !== "object") return;

        // 1. Update individual student record row immediately
        if (payload.record) {
            const rec = payload.record;
            const row =
                document.querySelector(`.roster-row[data-record-id="${rec.record_id}"]`) ||
                document.querySelector(`.roster-row[data-student-id="${String(rec.student_id || "").toLowerCase()}"]`);

            if (row) {
                const st = (rec.status || "ABSENT").toUpperCase();
                row.dataset.status = st;

                const selectEl = row.querySelector(".status-select");
                if (selectEl) {
                    selectEl.value = st;
                    applyStatusClass(selectEl, st);
                }

                const timeCell = row.querySelector(".cell-check-time");
                if (timeCell) {
                    timeCell.textContent = rec.check_time ? formatTimeShort(rec.check_time) : "—";
                }

                const sourceCell = row.querySelector(".cell-source");
                if (sourceCell) {
                    if (payload.source) {
                        sourceCell.textContent = payload.source;
                    } else if (st === "PRESENT") {
                        sourceCell.textContent = "PIN check-in";
                    } else if (st === "LATE" || st === "EXCUSED") {
                        sourceCell.textContent = "Lecturer updated";
                    } else {
                        sourceCell.textContent = "No check-in";
                    }
                }

                highlightRow(row);
                recalculateCountersFromDOM();
            }
        }

        // 2. Update summary counters if sent explicitly
        if (payload.summary) {
            const s = payload.summary;
            if (countPresent && s.present !== undefined) countPresent.textContent = s.present;
            if (countLate && s.late !== undefined) countLate.textContent = s.late;
            if (countExcused && s.excused !== undefined) countExcused.textContent = s.excused;
            if (countAbsent && s.absent !== undefined) countAbsent.textContent = s.absent;
            if (checkedInRatio && s.total !== undefined) {
                const checkedIn = (s.present || 0) + (s.late || 0);
                checkedInRatio.textContent = `${checkedIn} / ${s.total}`;
            }
        }

        // 3. Update PIN display if PIN was regenerated
        if (payload.pin && payload.pin.pin_code) {
            if (pinDisplay) pinDisplay.textContent = payload.pin.pin_code;
            if (projectorPin) projectorPin.textContent = payload.pin.pin_code;
        }

        // 4. Handle session closed event
        if (payload.event === "session_closed") {
            root.dataset.isOpen = "false";
            root.dataset.remainingSeconds = "0";
            if (statusBadge) {
                statusBadge.className = "badge badge-warning";
                statusBadge.textContent = "Session closed";
            }
        }
    }

    // Connect via Server-Sent Events (SSE) for zero-dependency real-time updates
    if (typeof window.EventSource !== "undefined") {
        const query = sessionId
            ? `session_id=${encodeURIComponent(sessionId)}`
            : `section_id=${encodeURIComponent(sectionId)}`;
        const es = new EventSource(`/realtime/stream?${query}`);

        const eventTypes = [
            "attendance_update",
            "student_checked_in",
            "record_status_updated",
            "pin_regenerated",
            "session_closed",
        ];

        eventTypes.forEach((evName) => {
            es.addEventListener(evName, (evt) => {
                try {
                    const data = JSON.parse(evt.data);
                    handleAttendancePayload(data);
                } catch {
                    // Ignore malformed event data
                }
            });
        });

        window.addEventListener("beforeunload", () => {
            es.close();
        });
    }

    // Also bind Socket.IO client if loaded on the page
    if (typeof window.io === "function") {
        try {
            const socket = window.io();
            socket.on("connect", () => {
                socket.emit("join_attendance_session", {
                    session_id: sessionId,
                    section_id: sectionId,
                });
            });
            socket.on("attendance_update", handleAttendancePayload);
            socket.on("student_checked_in", handleAttendancePayload);
            socket.on("record_status_updated", handleAttendancePayload);
            socket.on("pin_regenerated", handleAttendancePayload);
            socket.on("session_closed", handleAttendancePayload);
        } catch {
            // SSE handles stream fallback
        }
    }
});
