"""
Query route — Phase 2 / Phase 3
================================
POST /memory/query  — semantic memory retrieval
"""

import logging

from fastapi import APIRouter, HTTPException, Request, status

from ...config import settings
from ...models.requests import QueryRequest, QueryResponse
from ...services.query_service import query_memory
from ..dependencies import CurrentUserId, Neo4jDriver, OpenAIClient, RedisClient
from ..limiter import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/memory", tags=["memory"])

_QUERY_RATE = f"{settings.rate_limit_query_per_minute}/minute"


@router.post(
    "/query",
    response_model=QueryResponse,
    status_code=status.HTTP_200_OK,
    summary="Semantic memory retrieval",
    description=(
        "Search stored conversations using semantic vector similarity. "
        "Supports date filtering, provider filtering, and token-budget-aware assembly. "
        "Falls back to full-text keyword search when embeddings are not yet available. "
        "The userId in the request body must match the userId in the Bearer token."
    ),
)
@limiter.limit(_QUERY_RATE)
async def query_memory_route(
    request: Request,
    body: QueryRequest,
    current_user_id: CurrentUserId,
    driver: Neo4jDriver,
    openai_client: OpenAIClient,
    redis_client: RedisClient,
) -> QueryResponse:
    # Store userId in request.state so the rate-limit key function can use it
    request.state.authenticated_user_id = current_user_id

    if body.userId != current_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Token userId '{current_user_id}' does not match "
                f"request userId '{body.userId}'."
            ),
        )

    logger.info(
        "query_received user=%s query=%r date_filter=%s",
        current_user_id,
        body.query[:60],
        body.effective_date_from or "none",
    )

    return await query_memory(
        driver=driver,
        openai_client=openai_client,
        redis_client=redis_client,
        request=body,
    )
