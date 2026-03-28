"""
Memory routes — Phase 1
========================
POST /memory/write   — store a conversation verbatim (202 Accepted, async write)
"""

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, status

from ...models.requests import WriteRequest, WriteResponse
from ...services.write_service import write_conversation_to_graph
from ..dependencies import CurrentUserId, Neo4jDriver

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/memory", tags=["memory"])


@router.post(
    "/write",
    response_model=WriteResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Store a conversation verbatim",
    description=(
        "Accepts one or more conversation turns in Canonical Message Format (CMF) "
        "and queues them for verbatim storage in Neo4j. "
        "Returns 202 immediately — storage happens asynchronously in the background. "
        "The userId in the request body must match the userId in the Bearer token."
    ),
)
async def write_memory(
    body: WriteRequest,
    background_tasks: BackgroundTasks,
    current_user_id: CurrentUserId,
    driver: Neo4jDriver,
) -> WriteResponse:
    """
    Phase 1 write endpoint.

    Flow:
      1. JWT validated by CurrentUserId dependency
      2. userId in token must match userId in request body
      3. Return 202 immediately
      4. Background task writes everything to Neo4j verbatim
    """

    # ── userId must match the authenticated token ──────────────────────────────
    if body.userId != current_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Token userId '{current_user_id}' does not match "
                f"request userId '{body.userId}'."
            ),
        )

    # ── Queue the write — returns immediately ──────────────────────────────────
    background_tasks.add_task(
        write_conversation_to_graph,
        driver,
        body,
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
