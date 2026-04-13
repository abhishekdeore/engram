"""
Query route — Phase 2 / Phase 3 / Phase 6
==========================================
POST /memory/query  — semantic memory retrieval

Phase 6: Added daily query limit enforcement.
"""

import logging

from fastapi import APIRouter, HTTPException, Request, status

from ...config import settings
from ...models.requests import QueryRequest, QueryResponse
from ...services.query_service import query_memory
from ...services.usage_service import check_daily_query_limit, increment_daily_query_count
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
        "The userId in the request body must match the userId in the Bearer token. "
        "Subject to daily query limit (default 100/day, resets at UTC midnight)."
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

    # Phase 6: enforce daily query limit (raises RateLimitError → 429)
    await check_daily_query_limit(current_user_id, redis_client=redis_client)

    logger.info(
        "query_received user=%s query=%r date_filter=%s",
        current_user_id,
        body.query[:60],
        body.effective_date_from or "none",
    )

    result = await query_memory(
        driver=driver,
        openai_client=openai_client,
        redis_client=redis_client,
        request=body,
    )

    # Phase 6: increment counter only on successful query
    await increment_daily_query_count(current_user_id, redis_client=redis_client)

    return result
