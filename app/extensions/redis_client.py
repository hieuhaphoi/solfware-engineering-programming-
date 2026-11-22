import logging
import threading
import time
from typing import Dict, Optional, Tuple

import redis
from app.config import Config

logger = logging.getLogger(__name__)


class _MemoryTTLStore:
    def __init__(self) -> None:
        self._store: Dict[str, Tuple[str, float]] = {}
        self._lock = threading.Lock()

    def _purge_if_expired(self, key: str) -> None:
        entry = self._store.get(key)
        if entry is not None and time.monotonic() >= entry[1]:
            self._store.pop(key, None)

    def setex(self, key: str, ttl_seconds: int, value: str) -> bool:
        with self._lock:
            expires_at = time.monotonic() + ttl_seconds
            self._store[key] = (value, expires_at)
            return True

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            self._purge_if_expired(key)
            entry = self._store.get(key)
            return entry[0] if entry is not None else None

    def ttl(self, key: str) -> int:
        with self._lock:
            self._purge_if_expired(key)
            entry = self._store.get(key)
            if entry is None:
                return -2
            remaining = int(entry[1] - time.monotonic())
            return max(remaining, 0)

    def delete(self, key: str) -> int:
        with self._lock:
            return 1 if self._store.pop(key, None) is not None else 0

    def exists(self, key: str) -> int:
        with self._lock:
            self._purge_if_expired(key)
            return 1 if key in self._store else 0

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


class RedisClientSingleton:
    _instance: Optional["RedisClientSingleton"] = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> "RedisClientSingleton":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._client: Optional[redis.Redis] = None
        self._memory_fallback = _MemoryTTLStore()
        self._redis_uri: str = Config.REDIS_URI
        self._allow_fallback: bool = True
        self._connect(self._redis_uri)
        self._initialized = True

    def init_app(self, app) -> None:
        uri = app.config.get("REDIS_URI", Config.REDIS_URI)
        self._allow_fallback = app.config.get("REDIS_ALLOW_FALLBACK", True)
        self._redis_uri = uri
        self._connect(uri)

    def _connect(self, uri: str) -> None:
        try:
            pool = redis.ConnectionPool.from_url(
                uri,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
                max_connections=50,
            )
            client = redis.Redis(connection_pool=pool)
            client.ping()
            self._client = client
            logger.info("Connected to Redis successfully at %s", uri)
        except redis.RedisError as exc:
            self._client = None
            if not self._allow_fallback:
                raise ConnectionError(f"Cannot connect to Redis at {uri}") from exc
            logger.warning(
                "Redis unavailable (%s). Falling back to in-memory TTL store.",
                exc,
            )

    def is_redis_available(self) -> bool:
        if self._client is None:
            return False
        try:
            return bool(self._client.ping())
        except redis.RedisError:
            self._client = None
            return False

    def set_with_ttl(self, key: str, value: str, ttl_seconds: int) -> bool:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be a positive integer.")

        if self._client is not None:
            try:
                return bool(self._client.setex(key, ttl_seconds, value))
            except redis.RedisError as exc:
                if not self._allow_fallback:
                    raise
                logger.warning("Redis SETEX failed (%s). Using memory fallback.", exc)
                self._client = None

        return self._memory_fallback.setex(key, ttl_seconds, value)

    def get(self, key: str) -> Optional[str]:
        if self._client is not None:
            try:
                return self._client.get(key)
            except redis.RedisError as exc:
                if not self._allow_fallback:
                    raise
                logger.warning("Redis GET failed (%s). Using memory fallback.", exc)
                self._client = None

        return self._memory_fallback.get(key)

    def get_ttl(self, key: str) -> int:
        if self._client is not None:
            try:
                return int(self._client.ttl(key))
            except redis.RedisError as exc:
                if not self._allow_fallback:
                    raise
                logger.warning("Redis TTL failed (%s). Using memory fallback.", exc)
                self._client = None

        return self._memory_fallback.ttl(key)

    def delete(self, key: str) -> bool:
        if self._client is not None:
            try:
                return bool(self._client.delete(key))
            except redis.RedisError as exc:
                if not self._allow_fallback:
                    raise
                logger.warning("Redis DELETE failed (%s). Using memory fallback.", exc)
                self._client = None

        return bool(self._memory_fallback.delete(key))

    def exists(self, key: str) -> bool:
        if self._client is not None:
            try:
                return bool(self._client.exists(key))
            except redis.RedisError as exc:
                if not self._allow_fallback:
                    raise
                logger.warning("Redis EXISTS failed (%s). Using memory fallback.", exc)
                self._client = None

        return bool(self._memory_fallback.exists(key))


redis_client = RedisClientSingleton()