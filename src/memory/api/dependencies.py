"""
FastAPI dependencies — reusable, injectable building blocks.

Injected via Depends() into route handlers.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..auth.jwt_handler import decode_access_token

# ── Bearer token extractor ────────────────────────────────────────────────────

_bearer = HTTPBearer(auto_error=True)


async def get_current_user_id(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
) -> str:
    """
    Extract and verify the Bearer JWT from the Authorization header.
    Returns the userId (sub claim) on success.
    Raises HTTP 401 on any auth failure.
    """
    try:
        user_id = decode_access_token(credentials.credentials)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    return user_id


# ── Neo4j driver accessor ─────────────────────────────────────────────────────

def get_neo4j_driver(request: Request):
    """
    Retrieve the async Neo4j driver stored in app.state during lifespan startup.
    Raises HTTP 503 if the driver is not initialised.
    """
    driver = getattr(request.app.state, "neo4j_driver", None)
    if driver is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database connection not available",
        )
    return driver


# ── Type aliases for use in route signatures ──────────────────────────────────

CurrentUserId = Annotated[str, Depends(get_current_user_id)]
Neo4jDriver   = Annotated[object, Depends(get_neo4j_driver)]
