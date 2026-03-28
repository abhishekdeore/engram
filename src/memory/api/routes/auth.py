"""
Auth route — development token generation ONLY.

POST /auth/token  →  issues a signed JWT for a given userId.

This endpoint exists so developers can generate tokens during Phase 1/2
testing without building a full registration system.

It MUST be disabled or protected in production.
"""

from fastapi import APIRouter, HTTPException, status

from ...auth.jwt_handler import create_access_token
from ...config import settings
from ...models.requests import TokenRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/token",
    response_model=TokenResponse,
    status_code=201,
    summary="[DEV ONLY] Issue a JWT for a userId",
    description=(
        "Development-only endpoint. Generates a signed JWT for the given userId. "
        "Disable this in production and replace with your identity provider."
    ),
)
async def issue_token(body: TokenRequest) -> TokenResponse:
    if settings.is_production:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token self-issuance is disabled in production.",
        )

    token = create_access_token(body.userId)
    return TokenResponse(access_token=token, userId=body.userId)
