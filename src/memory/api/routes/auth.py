"""
Auth routes.

POST /auth/token   — [DEV ONLY] issues a short-lived JWT for local testing.
                     Hard-blocked in production (defense in depth).

POST /auth/apikey  — issues a long-lived JWT (1 year) for use as a stable
                     API key in third-party integrations (e.g. ChatGPT Custom
                     GPT Actions). Rate-limited. Available in both dev and
                     production. The caller stores the key securely.

Phase 6: Consolidated JWT encoding — both endpoints now use
create_access_token() with token_type and jti claims.
"""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, status

from ...auth.jwt_handler import create_access_token, decode_access_token_full
from ...auth.revocation import revoke_token
from ...config import settings
from ...models.requests import (
    ApiKeyRequest,
    ApiKeyResponse,
    RevokeTokenRequest,
    RevokeTokenResponse,
    TokenRequest,
    TokenResponse,
)
from ..dependencies import CurrentUserId
from ..limiter import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

_API_KEY_EXPIRE_DAYS = 365
_API_KEY_EXPIRE_MINUTES = _API_KEY_EXPIRE_DAYS * 24 * 60

# Phase 6: rate limit API key issuance to prevent abuse
_APIKEY_RATE = "5/minute"


@router.post(
    "/token",
    response_model=TokenResponse,
    status_code=201,
    summary="[DEV ONLY] Issue a short-lived JWT for a userId",
    description=(
        "Development-only endpoint. Generates a signed JWT for the given userId. "
        "Hard-blocked in production. For production use, call POST /auth/apikey."
    ),
)
async def issue_token(body: TokenRequest) -> TokenResponse:
    # Phase 6: defense in depth — reject even if is_production flag is wrong
    if settings.is_production or settings.app_env != "development":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token self-issuance is disabled in production. Use POST /auth/apikey.",
        )

    token = create_access_token(body.userId, token_type="session")
    return TokenResponse(access_token=token, userId=body.userId)


@router.post(
    "/apikey",
    response_model=ApiKeyResponse,
    status_code=201,
    summary="Issue a long-lived API key for third-party integrations",
    description=(
        "Generates a signed JWT valid for 1 year, intended as a stable API key "
        "for use in Custom GPT Actions, external scripts, or any integration that "
        "needs a durable credential. Available in both development and production. "
        "Rate-limited to 5 requests per minute. "
        "Store the returned api_key securely — it grants full memory access as the "
        "given userId for its lifetime."
    ),
)
@limiter.limit(_APIKEY_RATE)
async def issue_api_key(
    request: Request,
    body: ApiKeyRequest,
    current_user_id: CurrentUserId,
) -> ApiKeyResponse:
    if body.userId != current_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Cannot create API keys for other users. "
                   f"Authenticated as '{current_user_id}', requested '{body.userId}'.",
        )

    expire = datetime.now(timezone.utc) + timedelta(days=_API_KEY_EXPIRE_DAYS)

    # Phase 6: use consolidated create_access_token (includes jti claim)
    api_key = create_access_token(
        body.userId,
        token_type="apikey",
        expire_minutes=_API_KEY_EXPIRE_MINUTES,
    )

    logger.info(
        "api_key_issued user_id=%s expires_at=%s",
        body.userId,
        expire.isoformat(),
    )

    return ApiKeyResponse(
        api_key=api_key,
        userId=body.userId,
        expires_at=expire.isoformat(),
    )


@router.post(
    "/revoke",
    response_model=RevokeTokenResponse,
    status_code=200,
    summary="Revoke a token by adding its jti to the deny-list",
    description=(
        "Revokes a JWT token so it can no longer be used for authentication. "
        "The token's jti is added to a Redis-backed deny-list with a TTL matching "
        "the token's remaining lifetime. Requires authentication — only the token's "
        "own user can revoke it. Legacy tokens without a jti claim cannot be revoked."
    ),
)
async def revoke_token_endpoint(
    request: Request,
    body: RevokeTokenRequest,
    current_user_id: CurrentUserId,
) -> RevokeTokenResponse:
    # Decode the target token to get its full payload
    try:
        target_payload = decode_access_token_full(body.token)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The token to revoke is invalid or already expired.",
        )

    # Verify the target token belongs to the authenticated user
    target_user_id = target_payload.get("sub")
    if target_user_id != current_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot revoke tokens belonging to other users.",
        )

    # Legacy tokens (pre-Phase 6) have no jti — cannot be individually revoked
    jti = target_payload.get("jti")
    if not jti:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This token has no jti claim (legacy token). "
                   "It cannot be individually revoked. "
                   "Rotate JWT_SECRET_KEY to invalidate all legacy tokens.",
        )

    # Calculate expiry from the exp claim
    exp_timestamp = target_payload.get("exp")
    if exp_timestamp is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token has no exp claim and cannot be revoked.",
        )
    expires_at = datetime.fromtimestamp(exp_timestamp, tz=timezone.utc)

    # Get Redis client and revoke
    redis_client = getattr(request.app.state, "redis_client", None)
    success = await revoke_token(redis_client, jti, expires_at)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Token revocation failed — Redis is unavailable. "
                   "The token remains valid. Retry when Redis is restored.",
        )

    logger.info(
        "token_revoked_by_user user_id=%s jti=%s token_type=%s",
        current_user_id,
        jti,
        target_payload.get("type", "unknown"),
    )

    return RevokeTokenResponse(
        status="revoked",
        message=f"Token {jti} has been revoked and added to the deny-list.",
        jti=jti,
    )
