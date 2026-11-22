import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Union

from app.config import Config
from app.extensions.redis_client import RedisClientSingleton, redis_client


SessionId = Union[int, str]


class PINError(Exception):
    """Exception raised when a submitted PIN code is invalid or expired."""

    def __init__(self, code: str, message: str, remaining_seconds: int = 0) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.remaining_seconds = remaining_seconds


class SessionManager:
    MIN_PIN_LENGTH = 4
    MAX_PIN_LENGTH = 6
    DEFAULT_PIN_LENGTH = int(getattr(Config, "PIN_LENGTH", 6))

    MIN_TTL_SECONDS = 180
    MAX_TTL_SECONDS = 300
    DEFAULT_TTL_SECONDS = int(getattr(Config, "PIN_TTL_SECONDS", 300))

    KEY_PREFIX = "attendance:pin"

    def __init__(
        self,
        cache_client: Optional[RedisClientSingleton] = None,
    ) -> None:
        self._cache = cache_client or redis_client

    @classmethod
    def _build_key(cls, session_id: SessionId) -> str:
        if session_id is None or str(session_id).strip() == "":
            raise ValueError("session_id cannot be empty.")
        return f"{cls.KEY_PREFIX}:{str(session_id).strip()}"

    @classmethod
    def _validate_pin_length(cls, length: int) -> None:
        if not isinstance(length, int) or isinstance(length, bool):
            raise ValueError("pin_length must be an integer.")
        if not (cls.MIN_PIN_LENGTH <= length <= cls.MAX_PIN_LENGTH):
            raise ValueError(
                f"pin_length must be between {cls.MIN_PIN_LENGTH} "
                f"and {cls.MAX_PIN_LENGTH} digits."
            )

    @classmethod
    def _validate_ttl(cls, ttl_seconds: int) -> None:
        if not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool):
            raise ValueError("ttl_seconds must be an integer.")
        if not (cls.MIN_TTL_SECONDS <= ttl_seconds <= cls.MAX_TTL_SECONDS):
            raise ValueError(
                f"ttl_seconds must be between {cls.MIN_TTL_SECONDS} "
                f"and {cls.MAX_TTL_SECONDS} seconds (3-5 minutes)."
            )

    @classmethod
    def normalize_ttl(cls, raw_ttl: Any) -> int:
        """Clamp or fall back a raw TTL value into the allowed 180..300s window."""
        try:
            val = int(raw_ttl)
        except (TypeError, ValueError):
            return cls.DEFAULT_TTL_SECONDS
        return max(cls.MIN_TTL_SECONDS, min(cls.MAX_TTL_SECONDS, val))

    def generate_pin(self, length: int = DEFAULT_PIN_LENGTH) -> str:
        self._validate_pin_length(length)
        return "".join(secrets.choice(string.digits) for _ in range(length))

    def create_pin_session(
        self,
        session_id: SessionId,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        pin_length: int = DEFAULT_PIN_LENGTH,
        section_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        key = self._build_key(session_id)
        self._validate_ttl(ttl_seconds)
        pin_code = self.generate_pin(pin_length)

        self._cache.set_with_ttl(key, pin_code, ttl_seconds)

        now_utc = datetime.now(timezone.utc)
        expires_utc = now_utc + timedelta(seconds=ttl_seconds)
        expires_naive = datetime.now() + timedelta(seconds=ttl_seconds)

        result: Dict[str, Any] = {
            "session_id": str(session_id).strip(),
            "pin_code": pin_code,
            "ttl_seconds": ttl_seconds,
            "remaining_seconds": ttl_seconds,
            "created_at": now_utc.isoformat(),
            "expires_at": expires_utc.isoformat(),
            "expires_at_dt": expires_naive,
        }
        if section_id is not None:
            result["section_id"] = str(section_id).strip()
        return result

    def verify_pin(
        self,
        session_id: SessionId,
        submitted_pin: Any,
        raise_on_error: bool = False,
    ) -> Dict[str, Any]:
        key = self._build_key(session_id)

        if not isinstance(submitted_pin, str):
            res = {
                "valid": False,
                "reason": "INVALID_FORMAT",
                "remaining_seconds": self.get_remaining_ttl(session_id),
            }
            if raise_on_error:
                raise PINError("INVALID_FORMAT", "PIN must be a 4-6 digit numeric code.", res["remaining_seconds"])
            return res

        normalized_pin = submitted_pin.strip()
        if not normalized_pin.isdigit() or not (
            self.MIN_PIN_LENGTH <= len(normalized_pin) <= self.MAX_PIN_LENGTH
        ):
            res = {
                "valid": False,
                "reason": "INVALID_FORMAT",
                "remaining_seconds": self.get_remaining_ttl(session_id),
            }
            if raise_on_error:
                raise PINError("INVALID_FORMAT", "PIN must be a 4-6 digit numeric code.", res["remaining_seconds"])
            return res

        stored_pin = self._cache.get(key)
        if stored_pin is None:
            res = {
                "valid": False,
                "reason": "PIN_EXPIRED_OR_NOT_FOUND",
                "remaining_seconds": 0,
            }
            if raise_on_error:
                raise PINError("PIN_EXPIRED", "This PIN has expired or the session is no longer active.", 0)
            return res

        remaining = max(self._cache.get_ttl(key), 0)
        if secrets.compare_digest(stored_pin, normalized_pin):
            return {
                "valid": True,
                "reason": "OK",
                "remaining_seconds": remaining,
            }

        res = {
            "valid": False,
            "reason": "INVALID_PIN",
            "remaining_seconds": remaining,
        }
        if raise_on_error:
            raise PINError("INVALID_PIN", "Incorrect PIN code. Please check with your lecturer.", remaining)
        return res

    def get_active_pin(self, session_id: SessionId) -> Optional[str]:
        key = self._build_key(session_id)
        return self._cache.get(key)

    def get_remaining_ttl(self, session_id: SessionId) -> int:
        key = self._build_key(session_id)
        ttl = self._cache.get_ttl(key)
        return ttl if ttl > 0 else 0

    def refresh_pin(
        self,
        session_id: SessionId,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        pin_length: int = DEFAULT_PIN_LENGTH,
    ) -> Dict[str, Any]:
        return self.create_pin_session(
            session_id=session_id,
            ttl_seconds=ttl_seconds,
            pin_length=pin_length,
        )

    def invalidate_pin(self, session_id: SessionId) -> bool:
        key = self._build_key(session_id)
        return self._cache.delete(key)

    # --- Convenience class-level methods used by controllers ---

    @classmethod
    def issue_pin(
        cls,
        session_id: SessionId,
        section_id: Optional[str] = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        digits: int = DEFAULT_PIN_LENGTH,
    ) -> Dict[str, Any]:
        normalized_ttl = cls.normalize_ttl(ttl_seconds)
        return session_manager.create_pin_session(
            session_id=session_id,
            ttl_seconds=normalized_ttl,
            pin_length=digits,
            section_id=section_id,
        )

    @classmethod
    def get_active_pin_info(cls, session_id: SessionId) -> Optional[Dict[str, Any]]:
        pin = session_manager.get_active_pin(session_id)
        if not pin:
            return None
        rem = session_manager.get_remaining_ttl(session_id)
        if rem <= 0:
            return None
        return {
            "session_id": str(session_id).strip(),
            "pin_code": pin,
            "remaining_seconds": rem,
        }

    @classmethod
    def revoke_pin(cls, session_id: SessionId) -> bool:
        return session_manager.invalidate_pin(session_id)


session_manager = SessionManager()
