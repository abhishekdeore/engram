"""
JWT creation and verification.

Uses python-jose (already installed). FastAPI's official docs now recommend
PyJWT for new projects, but python-jose is fully functional here.
To switch to PyJWT later: swap the imports and exception type only.
"""

from datetime import datetime, timedelta, timezone

from jose import jwt, JWTError

from ..config import settings


def create_access_token(user_id: str) -> str:
    """
    Create a signed JWT for the given userId.
    The token is valid for JWT_ACCESS_TOKEN_EXPIRE_MINUTES (default: 7 days).
    """
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.jwt_access_token_expire_minutes)

    payload = {
        "sub": user_id,          # subject — the userId
        "iat": now,              # issued at
        "exp": expire,           # expiry
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
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError as exc:
        raise ValueError(f"Invalid or expired token: {exc}") from exc

    user_id: str | None = payload.get("sub")
    if not user_id:
        raise ValueError("Token missing 'sub' claim")

    return user_id
