"""
Auth routes.

POST /auth/token   — [DEV ONLY] issues a short-lived JWT for local testing.
                     Blocked in production.

POST /auth/apikey  — issues a long-lived JWT (1 year) for use as a stable
                     API key in third-party integrations (e.g. ChatGPT Custom
                     GPT Actions). Available in both dev and production.
                     The caller is responsible for storing the key securely.
"""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, status
from jose import jwt as jose_jwt

from ...auth.jwt_handler import create_access_token
from ...config import settings
from ...models.requests import ApiKeyRequest, ApiKeyResponse, TokenRequest, TokenResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

_API_KEY_EXPIRE_DAYS = 365


@router.post(
    "/token",
    response_model=TokenResponse,
    status_code=201,
    summary="[DEV ONLY] Issue a short-lived JWT for a userId",
    description=(
        "Development-only endpoint. Generates a signed JWT for the given userId. "
        "Blocked in production. For production use, call POST /auth/apikey instead."
    ),
)
async def issue_token(body: TokenRequest) -> TokenResponse:
    if settings.is_production:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token self-issuance is disabled in production. Use POST /auth/apikey.",
        )

    token = create_access_token(body.userId)
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
        "Store the returned api_key securely — it grants full memory access as the "
        "given userId for its lifetime."
    ),
)
async def issue_api_key(body: ApiKeyRequest) -> ApiKeyResponse:
    expire = datetime.now(timezone.utc) + timedelta(days=_API_KEY_EXPIRE_DAYS)
    payload = {
        "sub":  body.userId,
        "iat":  datetime.now(timezone.utc),
        "exp":  expire,
        "type": "apikey",      # distinguishes from short-lived session tokens
    }
    api_key = jose_jwt.encode(
        payload,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
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
