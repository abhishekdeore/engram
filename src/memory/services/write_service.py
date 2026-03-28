"""
Write Service — Phase 1
========================
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
"""

import logging
from datetime import datetime, timezone

from neo4j import AsyncDriver, AsyncManagedTransaction

from ..config import settings
from ..models.requests import WriteRequest
from .segment_service import check_and_create_segments
from .embedding_service import embed_new_content

logger = logging.getLogger(__name__)


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
    Main write pipeline. Called as a FastAPI BackgroundTask.

    Steps:
      1. MERGE User node
      2. MERGE Conversation node + link to User (messageCount initialised to 0)
      3. Atomic message write:
           a. Acquire write lock on Conversation (serialises concurrent writes)
           b. Read current message count as base offset
           c. UNWIND-MERGE all Message nodes (ON CREATE only — verbatim, no overwrite)
           d. Link each Message to Conversation via HAS_MESSAGE
           e. Track actually-new messages via _isNew sentinel; return actual_new_count
      4. Increment Conversation.messageCount by actual_new_count
      5. Build NEXT_MESSAGE chain within batch + connect first new msg to prior chain
      6. Check segmentation threshold → create Segment nodes if needed
      7. Compute and store embeddings for new Segments and Messages (Phase 2)
         Skipped if openai_client is None — verbatim storage is already safe.

    If any step fails, logs full context and returns (202 already sent to client).
    """
    started_at = datetime.now(timezone.utc)
    try:
        async with driver.session(database=settings.neo4j_database) as session:

            now = datetime.now(timezone.utc)

            # Step 1 + 2: User and Conversation
            await session.execute_write(_merge_user_tx, request.userId, now)
            await session.execute_write(_merge_conversation_tx, request, now)

            # Step 3: Atomic message write — returns count and tokens of genuinely new messages
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

            # Step 6: Segmentation
            await check_and_create_segments(
                session,
                conversation_id=request.conversationId,
                user_id=request.userId,
                provider=request.provider,
            )

        # Step 7: Embeddings — runs outside the session block so it can open
        # its own sessions per segment/message (avoids holding one session open
        # for the full duration of OpenAI API calls).
        # No-op when openai_client is None.
        await embed_new_content(
            driver=driver,
            openai_client=openai_client,
            redis_client=redis_client,
            conversation_id=request.conversationId,
        )

        logger.info(
            "write_complete conversation_id=%s messages_received=%d "
            "messages_new=%d provider=%s duration_ms=%.1f",
            request.conversationId,
            len(request.messages),
            actual_new_count,
            request.provider,
            (datetime.now(timezone.utc) - started_at).total_seconds() * 1000,
        )

    except Exception:
        logger.exception(
            "write_failed conversation_id=%s user_id=%s provider=%s model=%s "
            "message_count=%d failed_at=%s",
            request.conversationId,
            request.userId,
            request.provider,
            request.model,
            len(request.messages),
            started_at.isoformat(),
        )


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
) -> int:
    """
    Atomically write all messages in the batch. Returns the count of messages
    that were genuinely created (0 on a fully idempotent resend).

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
    # SET c.updatedAt forces an exclusive write lock on the Conversation node.
    # Concurrent transactions will block here until this one commits/aborts,
    # eliminating the TOCTOU window on message index assignment.
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

    # ── Step C: Batch MERGE all messages (single round-trip via UNWIND) ───────
    # ON CREATE only — an existing message node is never overwritten.
    # _isNew sentinel is set on creation to distinguish new vs existing.
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

    # ── Step D: NEXT_MESSAGE chain within this batch (single round-trip) ──────
    # MERGE is idempotent — safe on duplicate sends.
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
    # Only create this link when messages[0] is actually new (not a resend)
    # AND there is a prior message to link from.
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
