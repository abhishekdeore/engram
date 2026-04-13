"""
FastAPI dependencies — reusable, injectable building blocks.

Injected via Depends() into route handlers.
"""

import logging
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..auth.jwt_handler import decode_access_token_full
from ..auth.revocation import is_token_revoked

logger = logging.getLogger(__name__)

# ── Bearer token extractor ────────────────────────────────────────────────────

_bearer = HTTPBearer(auto_error=True)


async def get_current_user_id(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
) -> str:
    """
    Extract and verify the Bearer JWT from the Authorization header.
    Returns the userId (sub claim) on success.
    Raises HTTP 401 on any auth failure, including revoked tokens.
    """
    try:
        payload = decode_access_token_full(credentials.credentials)
    except ValueError:
        # Phase 6: generic message — never leak JWT decode details
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Phase 7: Check token deny-list if jti is present
    jti = payload.get("jti")
    if jti:
        redis_client = getattr(request.app.state, "redis_client", None)
        if await is_token_revoked(redis_client, jti):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has been revoked",
                headers={"WWW-Authenticate": "Bearer"},
            )

    return payload["sub"]


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


# ── OpenAI client accessor ────────────────────────────────────────────────────

def get_openai_client(request: Request):
    """
    Retrieve the AsyncOpenAI client from app.state.
    Returns None if OPENAI_API_KEY was not configured — callers must handle
    the None case gracefully (fall back to full-text search, skip embedding).
    """
    return getattr(request.app.state, "openai_client", None)


# ── Redis client accessor ─────────────────────────────────────────────────────

def get_redis_client(request: Request):
    """
    Retrieve the async Redis client from app.state.
    Returns None if Redis was not configured or failed to connect on startup.
    Callers must treat None as "cache unavailable" and proceed without it.
    """
    return getattr(request.app.state, "redis_client", None)


# ── Type aliases for use in route signatures ──────────────────────────────────

CurrentUserId = Annotated[str, Depends(get_current_user_id)]
Neo4jDriver   = Annotated[object, Depends(get_neo4j_driver)]
OpenAIClient  = Annotated[object, Depends(get_openai_client)]
RedisClient   = Annotated[object, Depends(get_redis_client)]
