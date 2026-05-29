import secrets
import hmac
import time
from typing import Optional
from backend.utils import get_logger

logger = get_logger("auth")

TOKEN_TTL = 86400  # 24 hours

_sessions: dict[str, float] = {}


def create_token(password: str, stored_password: str) -> Optional[str]:
    if not stored_password:
        return None
    if not hmac.compare_digest(password, stored_password):
        return None
    token = secrets.token_urlsafe(32)
    _sessions[token] = time.time() + TOKEN_TTL
    logger.info("Dashboard login successful")
    return token


def verify_token(token: Optional[str]) -> bool:
    if not token:
        return False
    expiry = _sessions.get(token)
    if expiry is None:
        return False
    if time.time() > expiry:
        _sessions.pop(token, None)
        return False
    return True


def revoke_token(token: Optional[str]) -> None:
    if token:
        _sessions.pop(token, None)


