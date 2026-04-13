"""
JWT creation and verification.

Uses python-jose (already installed). FastAPI's official docs now recommend
PyJWT for new projects, but python-jose is fully functional here.
To switch to PyJWT later: swap the imports and exception type only.

Phase 6: Consolidated into a single encoding path. All tokens now carry:
  - sub  : userId
  - iat  : issued at
  - exp  : expiry
  - jti  : unique token ID (for revocation tracking)
  - type : "session" or "apikey"
"""

import secrets
from datetime import datetime, timedelta, timezone

from jose import jwt, JWTError

from ..config import settings


def create_access_token(
    user_id: str,
    *,
    token_type: str = "session",
    expire_minutes: int | None = None,
) -> str:
    """
    Create a signed JWT for the given userId.

    Args:
        user_id: The userId to encode in the 'sub' claim.
        token_type: "session" (short-lived) or "apikey" (long-lived).
        expire_minutes: Override expiry. Defaults to settings value for sessions.

    Returns:
        Encoded JWT string.
    """
    now = datetime.now(timezone.utc)
    if expire_minutes is not None:
        expire = now + timedelta(minutes=expire_minutes)
    else:
        expire = now + timedelta(minutes=settings.jwt_access_token_expire_minutes)

    payload = {
        "sub":  user_id,
        "iat":  now,
        "exp":  expire,
        "jti":  secrets.token_hex(16),
        "type": token_type,
    }

    return jwt.encode(
        payload,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )


def decode_access_token(token: str) -> str:
    """
    Decode and verify a JWT. Returns the userId (sub claim).
    Raises ValueError on any verification failure.

    Accepts both legacy tokens (without jti/type) and Phase 6+ tokens.
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError:
        raise ValueError("Invalid or expired token")

    user_id: str | None = payload.get("sub")
    if not user_id:
        raise ValueError("Invalid or expired token")

    return user_id


def decode_access_token_full(token: str) -> dict:
    """
    Decode and verify a JWT. Returns the full payload dict.
    Raises ValueError on any verification failure.

    The returned dict includes all claims: sub, iat, exp, jti, type.
    Legacy tokens (pre-Phase 6) may lack jti and type.
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError:
        raise ValueError("Invalid or expired token")

    user_id: str | None = payload.get("sub")
    if not user_id:
        raise ValueError("Invalid or expired token")

    return payload
