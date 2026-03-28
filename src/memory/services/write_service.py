"""
Write Service — Phase 1 / Phase 4
===================================
All Neo4j write logic for storing conversations verbatim.

Design rules:
  - Every write uses session.execute_write() — auto-retry on transient errors.
  - Every read  uses session.execute_read()  — auto-retry, read routing.
  - Results are ALWAYS consumed inside the transaction function (Neo4j 6.x rule).
  - Nothing is generated, interpreted, or modified. Verbatim only.
  - Called as a BackgroundTask — failures are logged, never propagated to client.

Concurrency safety:
  - _write_messages_tx acquires an early write lock on the Conversation node
    (via SET c.updatedAt) before reading the message count. This serialises
    concurrent writes to the same conversation, preventing messageIndex
    collisions (TOCTOU fix).
  - messageCount is incremented only by the count of *actually new* messages,
    tracked with an _isNew sentinel set in ON CREATE SET and cleaned up within
    the same transaction. Idempotent resends therefore never inflate the count.

Retry / durability (Phase 4):
  - The Neo4j write phase is retried up to _MAX_WRITE_ATTEMPTS times on known
    transient errors (ServiceUnavailable, SessionExpired, TransientError, OSError).
  - Exponential backoff: 1 s → 2 s between attempts.
  - Non-retryable errors (ConstraintError, ClientError, ValueError, etc.) are
    logged and returned immediately — retrying them would never succeed.
  - The embedding phase is NOT retried here; embed_new_content already handles
    its own soft failures, and embeddings are re-attempted on the next write
    (WHERE embedding IS NULL queries are idempotent).
  - After all retries are exhausted the failure is logged at CRITICAL level so
    alerting systems can catch silent data loss.
"""

import asyncio
import logging
from datetime import datetime, timezone

from neo4j import AsyncDriver, AsyncManagedTransaction
from neo4j.exceptions import (
    ConstraintError,
    ClientError,
    ServiceUnavailable,
    SessionExpired,
    TransientError,
)

from ..config import settings
from ..models.requests import WriteRequest
from .segment_service import check_and_create_segments
from .embedding_service import embed_new_content

logger = logging.getLogger(__name__)

# ── Retry configuration ───────────────────────────────────────────────────────

_MAX_WRITE_ATTEMPTS  = 3
_RETRY_BASE_DELAY    = 1.0   # seconds before attempt 2
_RETRY_BACKOFF       = 2.0   # multiplier per subsequent attempt  →  1 s, 2 s

# Errors that indicate a transient infrastructure problem — safe to retry.
_RETRYABLE_EXCEPTIONS = (ServiceUnavailable, SessionExpired, TransientError, OSError)

# Errors that indicate a permanent data or programming problem — do not retry.
_NON_RETRYABLE_EXCEPTIONS = (ConstraintError, ClientError, ValueError, TypeError)


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

async def write_conversation_to_graph(
    driver: AsyncDriver,
    request: WriteRequest,
    openai_client=None,
    redis_client=None,
) -> None:
    """
    Main write pipeline. Called as a FastAPI BackgroundTask (Phase 1) or
    directly from the MCP server (Phase 4).

    Structure:
      Phase A — Neo4j write   (retried on transient failures, up to
                               _MAX_WRITE_ATTEMPTS attempts with exponential
                               backoff). This is the core durability guarantee.
      Phase B — Embeddings    (soft failure — already has its own try/except;
                               not retried here because embed_new_content is
                               idempotent and will re-run on the next write).

    If all Neo4j retry attempts fail the failure is logged at CRITICAL level
    and the function returns (202 was already sent to the client or the MCP
    tool will report the error).
    """
    neo4j_write_succeeded = False

    for attempt in range(1, _MAX_WRITE_ATTEMPTS + 1):
        try:
            actual_new_count, actual_new_tokens = await _write_to_neo4j(
                driver, request
            )
            neo4j_write_succeeded = True
            break  # success — exit retry loop

        except _NON_RETRYABLE_EXCEPTIONS:
            logger.exception(
                "write_failed_permanent conversation_id=%s user_id=%s "
                "provider=%s (non-retryable error, no retry)",
                request.conversationId,
                request.userId,
                request.provider,
            )
            return  # do not retry

        except Exception as exc:
            if attempt < _MAX_WRITE_ATTEMPTS:
                delay = _RETRY_BASE_DELAY * (_RETRY_BACKOFF ** (attempt - 1))
                logger.warning(
                    "write_retry attempt=%d/%d delay=%.1fs "
                    "reason=%s conversation_id=%s user_id=%s",
                    attempt,
                    _MAX_WRITE_ATTEMPTS,
                    delay,
                    type(exc).__name__,
                    request.conversationId,
                    request.userId,
                )
                await asyncio.sleep(delay)
            else:
                logger.critical(
                    "write_failed_all_retries conversation_id=%s user_id=%s "
                    "provider=%s model=%s message_count=%d attempts=%d",
                    request.conversationId,
                    request.userId,
                    request.provider,
                    request.model,
                    len(request.messages),
                    _MAX_WRITE_ATTEMPTS,
                    exc_info=True,
                )

    if not neo4j_write_succeeded:
        return

    # Phase B: Embeddings — runs outside the session block so it can open
    # its own sessions per segment/message (avoids holding one session open
    # for the full duration of OpenAI API calls).
    # No-op when openai_client is None; failures are logged and swallowed
    # inside embed_new_content — verbatim storage is already safe.
    await embed_new_content(
        driver=driver,
        openai_client=openai_client,
        redis_client=redis_client,
        conversation_id=request.conversationId,
    )

    logger.info(
        "write_complete conversation_id=%s messages_received=%d "
        "messages_new=%d provider=%s",
        request.conversationId,
        len(request.messages),
        actual_new_count,
        request.provider,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Neo4j write pipeline (extracted for retry isolation and testability)
# ─────────────────────────────────────────────────────────────────────────────

async def _write_to_neo4j(
    driver: AsyncDriver,
    request: WriteRequest,
) -> tuple[int, int]:
    """
    Execute the full Neo4j write pipeline in a single session.
    Returns (actual_new_count, actual_new_tokens).

    Raises on any failure — the caller handles retry / logging.

    Steps:
      1. MERGE User node
      2. MERGE Conversation node + link to User
      3. Atomic message write with write-lock serialisation
      4. Bump Conversation counters by actual-new deltas only
      5. Segmentation check — create Segment nodes if threshold hit
    """
    now = datetime.now(timezone.utc)

    async with driver.session(database=settings.neo4j_database) as session:

        # Step 1 + 2: User and Conversation
        await session.execute_write(_merge_user_tx, request.userId, now)
        await session.execute_write(_merge_conversation_tx, request, now)

        # Step 3: Atomic message write
        actual_new_count, actual_new_tokens = await session.execute_write(
            _write_messages_tx,
            request,
            now,
        )

        # Step 4: Increment conversation counters by actual new messages only
        if actual_new_count > 0:
            await session.execute_write(
                _bump_conversation_count_tx,
                request.conversationId,
                actual_new_count,
                actual_new_tokens,
                now,
            )

        # Step 5: Segmentation
        await check_and_create_segments(
            session,
            conversation_id=request.conversationId,
            user_id=request.userId,
            provider=request.provider,
        )

    return actual_new_count, actual_new_tokens


# ─────────────────────────────────────────────────────────────────────────────
# Transaction functions — all consume results inside (Neo4j 6.x requirement)
# ─────────────────────────────────────────────────────────────────────────────

async def _merge_user_tx(
    tx: AsyncManagedTransaction,
    user_id: str,
    now: datetime,
) -> None:
    """MERGE User node. Sets createdAt on first write, updates lastActiveAt always."""
    await tx.run(
        """
        MERGE (u:User {userId: $userId})
        ON CREATE SET u.createdAt    = $now,
                      u.lastActiveAt = $now
        ON MATCH  SET u.lastActiveAt = $now
        """,
        userId=user_id,
        now=now,
    )


async def _merge_conversation_tx(
    tx: AsyncManagedTransaction,
    request: WriteRequest,
    now: datetime,
) -> None:
    """
    MERGE Conversation node.
    ON CREATE: initialise all fields. messageCount starts at 0; it will be
               incremented by _bump_conversation_count_tx after the actual
               write so the count always reflects real stored messages.
    ON MATCH:  update endedAt and updatedAt only — do NOT touch messageCount
               here (would inflate on duplicate sends).
    Then MERGE the User→Conversation relationship.
    """
    await tx.run(
        """
        MERGE (c:Conversation {conversationId: $conversationId})
        ON CREATE SET
            c.userId        = $userId,
            c.provider      = $provider,
            c.model         = $model,
            c.startedAt     = $firstTimestamp,
            c.endedAt       = $lastTimestamp,
            c.messageCount  = 0,
            c.totalTokens   = 0,
            c.segmentCount  = 0,
            c.isComplete    = false,
            c.createdAt     = $now
        ON MATCH SET
            c.endedAt   = $lastTimestamp,
            c.updatedAt = $now
        WITH c
        MATCH (u:User {userId: $userId})
        MERGE (u)-[:HAS_CONVERSATION]->(c)
        """,
        conversationId=request.conversationId,
        userId=request.userId,
        provider=request.provider,
        model=request.model,
        firstTimestamp=request.first_timestamp,
        lastTimestamp=request.last_timestamp,
        now=now,
    )


async def _write_messages_tx(
    tx: AsyncManagedTransaction,
    request: WriteRequest,
    now: datetime,
) -> tuple[int, int]:
    """
    Atomically write all messages in the batch.
    Returns (actual_new_count, actual_new_tokens).

    Concurrency contract
    --------------------
    The first SET on the Conversation node acquires an exclusive write lock,
    serialising any concurrent _write_messages_tx calls for the same conversation.
    The message count is then read *within the same transaction*, so base_offset
    is always accurate at commit time.

    Sentinel pattern
    ----------------
    ON CREATE SET m._isNew = true marks messages created in this batch.
    We read _isNew back from the UNWIND results to determine actual_new_count
    and whether the chain connection to the prior batch is needed, then
    REMOVE m._isNew from all affected messages in the same transaction.
    """
    messages = request.messages

    # ── Step A: Acquire write lock + read base offset ─────────────────────────
    lock_result = await tx.run(
        """
        MATCH (c:Conversation {conversationId: $convId})
        SET c.updatedAt = $now
        WITH c
        OPTIONAL MATCH (c)-[:HAS_MESSAGE]->(m:Message)
        RETURN count(m) AS total
        """,
        convId=request.conversationId,
        now=now,
    )
    lock_record = await lock_result.single()
    base_offset: int = lock_record["total"] if lock_record else 0

    # ── Step B: Build message data with monotonically assigned indices ─────────
    msg_data = [
        {
            "messageId":    msg.messageId,
            "role":         msg.role,
            "content":      msg.content,
            "timestamp":    msg.timestamp,
            "tokenCount":   msg.tokenCount,
            "messageIndex": base_offset + i,
        }
        for i, msg in enumerate(messages)
    ]

    # ── Step C: Batch MERGE all messages ──────────────────────────────────────
    write_result = await tx.run(
        """
        UNWIND $messages AS msg
        MERGE (m:Message {messageId: msg.messageId})
        ON CREATE SET
            m.conversationId = $conversationId,
            m.userId         = $userId,
            m.role           = msg.role,
            m.content        = msg.content,
            m.provider       = $provider,
            m.model          = $model,
            m.timestamp      = msg.timestamp,
            m.tokenCount     = msg.tokenCount,
            m.messageIndex   = msg.messageIndex,
            m._isNew         = true
        WITH m
        MATCH (c:Conversation {conversationId: $conversationId})
        MERGE (c)-[:HAS_MESSAGE]->(m)
        RETURN m.messageId AS mid, m._isNew AS isNew
        """,
        messages=msg_data,
        conversationId=request.conversationId,
        userId=request.userId,
        provider=request.provider,
        model=request.model,
    )
    records = await write_result.data()

    new_ids: set[str] = {r["mid"] for r in records if r["isNew"] is True}
    actual_new_count = len(new_ids)
    actual_new_tokens: int = sum(
        msg.tokenCount for msg in messages if msg.messageId in new_ids
    )

    # ── Step D: NEXT_MESSAGE chain within this batch ───────────────────────────
    if len(messages) > 1:
        pairs = [
            {"fromId": messages[i].messageId, "toId": messages[i + 1].messageId}
            for i in range(len(messages) - 1)
        ]
        await tx.run(
            """
            UNWIND $pairs AS pair
            MATCH (a:Message {messageId: pair.fromId})
            MATCH (b:Message {messageId: pair.toId})
            MERGE (a)-[:NEXT_MESSAGE]->(b)
            """,
            pairs=pairs,
        )

    # ── Step E: Connect first new message to last existing message ────────────
    first_message_is_new = messages[0].messageId in new_ids if messages else False
    if first_message_is_new and base_offset > 0:
        await tx.run(
            """
            MATCH (prev:Message {conversationId: $convId})
            WHERE prev.messageIndex = $prevIndex
            MATCH (first:Message {messageId: $firstId})
            MERGE (prev)-[:NEXT_MESSAGE]->(first)
            """,
            convId=request.conversationId,
            prevIndex=base_offset - 1,
            firstId=messages[0].messageId,
        )

    # ── Step F: Clean up _isNew sentinel ──────────────────────────────────────
    if new_ids:
        await tx.run(
            """
            UNWIND $newIds AS mid
            MATCH (m:Message {messageId: mid})
            REMOVE m._isNew
            """,
            newIds=list(new_ids),
        )

    return actual_new_count, actual_new_tokens


async def _bump_conversation_count_tx(
    tx: AsyncManagedTransaction,
    conversation_id: str,
    actual_new_count: int,
    actual_new_tokens: int,
    now: datetime,
) -> None:
    """
    Increment Conversation.messageCount and totalTokens by the counts of
    messages and tokens actually created in this write.
    Called only when actual_new_count > 0.
    Both counters are accurate: duplicate sends contribute 0.
    """
    await tx.run(
        """
        MATCH (c:Conversation {conversationId: $conversationId})
        SET c.messageCount = c.messageCount + $deltaMessages,
            c.totalTokens  = c.totalTokens  + $deltaTokens,
            c.updatedAt    = $now
        """,
        conversationId=conversation_id,
        deltaMessages=actual_new_count,
        deltaTokens=actual_new_tokens,
        now=now,
    )
