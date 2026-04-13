"""
Memory routes — Phase 1 / Phase 2 / Phase 3 / Phase 6
=======================================================
POST   /memory/write                       — store a conversation verbatim (202, async)
GET    /memory/usage                       — current usage vs limits (Phase 6)
GET    /memory/conversations               — list user's conversations (paginated)
GET    /memory/conversation/{id}           — read a full conversation verbatim
DELETE /memory/conversation/{id}           — delete a conversation + cascade
DELETE /memory/user/{userId}               — GDPR purge of all user data
"""

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, status

from ...config import settings
from ...models.requests import (
    ConversationReadResponse,
    DeleteResponse,
    ListConversationsResponse,
    WriteRequest,
    WriteResponse,
)
from ...services.delete_service import delete_conversation, delete_user_data
from ...services.read_service import get_conversation, list_conversations
from ...services.usage_service import get_usage_summary
from ...services.write_service import check_storage_cap, write_conversation_to_graph
from ..dependencies import CurrentUserId, Neo4jDriver, OpenAIClient, RedisClient
from ..limiter import limiter

_WRITE_RATE = f"{settings.rate_limit_write_per_minute}/minute"

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/memory", tags=["memory"])


# ── Write ─────────────────────────────────────────────────────────────────────

@router.post(
    "/write",
    response_model=WriteResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Store a conversation verbatim",
    description=(
        "Accepts one or more conversation turns in Canonical Message Format (CMF) "
        "and queues them for verbatim storage in Neo4j. "
        "Returns 202 immediately — storage and embedding happen asynchronously. "
        "The userId in the request body must match the userId in the Bearer token."
    ),
)
@limiter.limit(_WRITE_RATE)
async def write_memory(
    request: Request,
    body: WriteRequest,
    background_tasks: BackgroundTasks,
    current_user_id: CurrentUserId,
    driver: Neo4jDriver,
    openai_client: OpenAIClient,
    redis_client: RedisClient,
) -> WriteResponse:
    request.state.authenticated_user_id = current_user_id
    if body.userId != current_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Token userId '{current_user_id}' does not match "
                f"request userId '{body.userId}'."
            ),
        )

    # Phase 6: enforce storage cap BEFORE queuing background task
    # Raises StorageCapError (→ 403) if limit reached
    await check_storage_cap(driver, current_user_id)

    background_tasks.add_task(
        write_conversation_to_graph,
        driver,
        body,
        openai_client,
        redis_client,
    )

    logger.info(
        "write_queued conversation_id=%s messages=%d provider=%s user=%s",
        body.conversationId,
        len(body.messages),
        body.provider,
        current_user_id,
    )

    return WriteResponse(
        conversationId=body.conversationId,
        messageCount=len(body.messages),
    )


# ── Usage (Phase 6) ──────────────────────────────────────────────────────────

@router.get(
    "/usage",
    status_code=status.HTTP_200_OK,
    summary="Current usage vs limits",
    description=(
        "Returns the authenticated user's current storage count, daily query "
        "count, and their respective limits. Useful for LLM clients to check "
        "remaining capacity before issuing writes or queries."
    ),
)
async def get_memory_usage(
    current_user_id: CurrentUserId,
    driver: Neo4jDriver,
    redis_client: RedisClient,
) -> dict:
    return await get_usage_summary(driver, current_user_id, redis_client=redis_client)


# ── List conversations ────────────────────────────────────────────────────────

@router.get(
    "/conversations",
    response_model=ListConversationsResponse,
    status_code=status.HTTP_200_OK,
    summary="List stored conversations",
    description=(
        "Returns a paginated list of conversation summaries for the authenticated user. "
        "Supports optional filtering by provider and date range. "
        "Results are ordered most-recent first."
    ),
)
async def list_memory_conversations(
    current_user_id: CurrentUserId,
    driver: Neo4jDriver,
    provider: str | None = Query(
        default=None,
        description="Filter by provider (chatgpt, claude, gemini, grok, copilot, custom).",
    ),
    dateFrom: str | None = Query(
        default=None,
        description="YYYY-MM-DD — conversations started on or after this date.",
    ),
    dateTo: str | None = Query(
        default=None,
        description="YYYY-MM-DD — conversations started on or before this date.",
    ),
    limit: int = Query(default=20, ge=1, le=100, description="Page size."),
    offset: int = Query(default=0, ge=0, description="Number of records to skip."),
) -> ListConversationsResponse:
    if dateTo is not None and dateFrom is None:
        raise HTTPException(
            status_code=422,
            detail="`dateTo` requires `dateFrom`.",
        )

    return await list_conversations(
        driver=driver,
        user_id=current_user_id,
        provider=provider,
        limit=limit,
        offset=offset,
        date_from=dateFrom,
        date_to=dateTo,
    )


# ── Read conversation ─────────────────────────────────────────────────────────

@router.get(
    "/conversation/{conversationId}",
    response_model=ConversationReadResponse,
    status_code=status.HTTP_200_OK,
    summary="Read a full conversation verbatim",
    description=(
        "Returns the complete verbatim conversation — all messages in "
        "chronological order. Only accessible by the conversation owner. "
        "Returns 404 if the conversation does not exist or belongs to another user."
    ),
)
async def read_conversation(
    conversationId: str,
    current_user_id: CurrentUserId,
    driver: Neo4jDriver,
) -> ConversationReadResponse:
    result = await get_conversation(
        driver=driver,
        conversation_id=conversationId,
        user_id=current_user_id,
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation '{conversationId}' not found.",
        )
    return result


# ── Delete conversation ───────────────────────────────────────────────────────

@router.delete(
    "/conversation/{conversationId}",
    response_model=DeleteResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete a conversation and all associated data",
    description=(
        "Permanently deletes the conversation and cascades to all linked "
        "Message, Segment, and Chunk nodes. "
        "Only the conversation owner can delete it. "
        "Returns 404 if the conversation does not exist or belongs to another user."
    ),
)
async def delete_memory_conversation(
    conversationId: str,
    current_user_id: CurrentUserId,
    driver: Neo4jDriver,
) -> DeleteResponse:
    result = await delete_conversation(
        driver=driver,
        conversation_id=conversationId,
        user_id=current_user_id,
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation '{conversationId}' not found.",
        )

    logger.info(
        "delete_conversation_complete conversation_id=%s user=%s",
        conversationId, current_user_id,
    )
    return result


# ── GDPR purge ────────────────────────────────────────────────────────────────

@router.delete(
    "/user/{userId}",
    response_model=DeleteResponse,
    status_code=status.HTTP_200_OK,
    summary="Permanently delete all data for a user (GDPR erasure)",
    description=(
        "Purges ALL stored data for the user: all conversations, messages, "
        "segments, chunks, and the User node itself. This action is irreversible. "
        "The userId in the path must match the authenticated user's token — "
        "users can only erase their own data."
    ),
)
async def delete_user_memory(
    userId: str,
    current_user_id: CurrentUserId,
    driver: Neo4jDriver,
) -> DeleteResponse:
    if userId != current_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Token userId '{current_user_id}' does not match "
                f"path userId '{userId}'. Users can only erase their own data."
            ),
        )

    result = await delete_user_data(driver=driver, user_id=current_user_id)

    logger.info(
        "gdpr_purge_complete user_id=%s nodes_deleted=%d",
        current_user_id, result.nodesDeleted,
    )
    return result
