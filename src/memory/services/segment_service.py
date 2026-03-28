"""
Segment Service — Phase 1
==========================
Implements the Hub-and-Spoke segmentation model.

A Segment is a contiguous block of ~20 messages from a conversation.
It holds the verbatim concatenated content of those messages and
serves as the primary unit of vector search (Phase 2).

Segmentation is triggered after every write and creates any Segment
nodes that should exist but don't yet.

Rules:
  - One segment per every SEGMENT_SIZE messages (default: 20)
  - Segments are indexed 0-based (Segment 0, Segment 1, ...)
  - Segment N covers messages [N*20, (N+1)*20 - 1]
  - Consecutive segments are linked via NEXT_SEGMENT
  - Each message is linked to its segment via CONTAINS_MESSAGE
  - Content stored verbatim: "ROLE [timestamp]: content\\n..."

Concurrency safety:
  - _create_segment_tx uses MERGE on (conversationId, segmentIndex) instead
    of CREATE. Two concurrent calls for the same segment index will serialize
    at the MERGE: the second caller matches the already-created node and
    performs no further writes (ON MATCH is absent). This requires the
    composite uniqueness constraint added in setup_schema.py.
  - CONTAINS_MESSAGE links and the NEXT_SEGMENT chain are also MERGE, so
    partial re-runs are safe and idempotent.
  - segmentCount is derived from the actual number of Segment nodes rather
    than an error-prone increment, so it is always consistent.
"""

import logging
import uuid

from neo4j import AsyncSession, AsyncManagedTransaction

logger = logging.getLogger(__name__)

SEGMENT_SIZE = 20   # messages per segment


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

async def check_and_create_segments(
    session: AsyncSession,
    conversation_id: str,
    user_id: str,
    provider: str,
) -> None:
    """
    Check whether any new Segment nodes need to be created for this conversation
    and create them if so.

    Called after each write batch. Safe to call multiple times (idempotent):
    _create_segment_tx uses MERGE so duplicate calls for the same segmentIndex
    are no-ops on the second call.
    """
    total_messages = await session.execute_read(
        _get_message_count_tx, conversation_id
    )
    existing_segments = await session.execute_read(
        _get_segment_count_tx, conversation_id
    )

    complete_segments_needed = total_messages // SEGMENT_SIZE

    if complete_segments_needed <= existing_segments:
        return   # nothing to do

    logger.info(
        "segmentation conversation_id=%s total_messages=%d "
        "existing_segments=%d segments_needed=%d",
        conversation_id, total_messages, existing_segments, complete_segments_needed,
    )

    for seg_index in range(existing_segments, complete_segments_needed):
        start_idx = seg_index * SEGMENT_SIZE
        end_idx   = start_idx + SEGMENT_SIZE - 1

        messages = await session.execute_read(
            _get_messages_for_range_tx,
            conversation_id,
            start_idx,
            end_idx,
        )

        if not messages:
            logger.warning(
                "segmentation_gap conversation_id=%s seg_index=%d "
                "expected messages %d-%d but none found",
                conversation_id, seg_index, start_idx, end_idx,
            )
            continue

        segment_content = _build_segment_content(messages)
        total_tokens    = sum(m["tokenCount"] or 0 for m in messages)
        segment_id      = str(uuid.uuid4())

        await session.execute_write(
            _create_segment_tx,
            segment_id=segment_id,
            conversation_id=conversation_id,
            user_id=user_id,
            provider=provider,
            seg_index=seg_index,
            content=segment_content,
            token_count=total_tokens,
            message_count=len(messages),
            start_message_index=start_idx,
            end_message_index=end_idx,
            start_timestamp=messages[0]["timestamp"],
            end_timestamp=messages[-1]["timestamp"],
            message_ids=[m["messageId"] for m in messages],
            prev_seg_index=seg_index - 1 if seg_index > 0 else None,
            conversation_id_for_prev=conversation_id,
        )

        logger.info(
            "segment_created conversation_id=%s segment_id=%s seg_index=%d",
            conversation_id, segment_id, seg_index,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Transaction functions
# ─────────────────────────────────────────────────────────────────────────────

async def _get_message_count_tx(
    tx: AsyncManagedTransaction,
    conversation_id: str,
) -> int:
    result = await tx.run(
        """
        OPTIONAL MATCH (c:Conversation {conversationId: $convId})-[:HAS_MESSAGE]->(m:Message)
        RETURN count(m) AS total
        """,
        convId=conversation_id,
    )
    record = await result.single()
    return record["total"] if record else 0


async def _get_segment_count_tx(
    tx: AsyncManagedTransaction,
    conversation_id: str,
) -> int:
    result = await tx.run(
        """
        OPTIONAL MATCH (c:Conversation {conversationId: $convId})-[:HAS_SEGMENT]->(s:Segment)
        RETURN count(s) AS total
        """,
        convId=conversation_id,
    )
    record = await result.single()
    return record["total"] if record else 0


async def _get_messages_for_range_tx(
    tx: AsyncManagedTransaction,
    conversation_id: str,
    start_idx: int,
    end_idx: int,
) -> list[dict]:
    """Fetch messages in a specific index range, ordered by messageIndex."""
    result = await tx.run(
        """
        MATCH (c:Conversation {conversationId: $convId})-[:HAS_MESSAGE]->(m:Message)
        WHERE m.messageIndex >= $startIdx AND m.messageIndex <= $endIdx
        RETURN m.messageId   AS messageId,
               m.role        AS role,
               m.content     AS content,
               m.timestamp   AS timestamp,
               m.tokenCount  AS tokenCount
        ORDER BY m.messageIndex
        """,
        convId=conversation_id,
        startIdx=start_idx,
        endIdx=end_idx,
    )
    # Must consume inside transaction function (Neo4j 6.x)
    return await result.data()


async def _create_segment_tx(
    tx: AsyncManagedTransaction,
    *,
    segment_id: str,
    conversation_id: str,
    user_id: str,
    provider: str,
    seg_index: int,
    content: str,
    token_count: int,
    message_count: int,
    start_message_index: int,
    end_message_index: int,
    start_timestamp,
    end_timestamp,
    message_ids: list[str],
    prev_seg_index: int | None,
    conversation_id_for_prev: str,
) -> None:
    """
    Create a Segment node, link it to the Conversation, link each Message to it,
    and chain it to the previous Segment if one exists.

    Uses MERGE on (conversationId, segmentIndex) instead of CREATE so that
    concurrent calls for the same segment index are safe: the second call
    matches the already-created node and exits without duplication.
    The composite uniqueness constraint on (Segment.conversationId,
    Segment.segmentIndex) enforces this at the database level.

    All sub-operations (CONTAINS_MESSAGE links, NEXT_SEGMENT chain,
    segmentCount update) are in the same transaction, so they commit or
    roll back together.
    """

    # ── Create Segment node + link to Conversation ────────────────────────────
    # MERGE on (conversationId, segmentIndex) serialises concurrent writes.
    # ON CREATE SET runs only on the first write; a second concurrent call
    # will MERGE into the existing node and do nothing further.
    await tx.run(
        """
        MERGE (s:Segment {conversationId: $conversationId, segmentIndex: $segIndex})
        ON CREATE SET
            s.segmentId         = $segmentId,
            s.userId            = $userId,
            s.provider          = $provider,
            s.content           = $content,
            s.tokenCount        = $tokenCount,
            s.messageCount      = $messageCount,
            s.startMessageIndex = $startMessageIndex,
            s.endMessageIndex   = $endMessageIndex,
            s.startTimestamp    = $startTimestamp,
            s.endTimestamp      = $endTimestamp
        WITH s
        MATCH (c:Conversation {conversationId: $conversationId})
        MERGE (c)-[:HAS_SEGMENT]->(s)
        """,
        conversationId=conversation_id,
        segIndex=seg_index,
        segmentId=segment_id,
        userId=user_id,
        provider=provider,
        content=content,
        tokenCount=token_count,
        messageCount=message_count,
        startMessageIndex=start_message_index,
        endMessageIndex=end_message_index,
        startTimestamp=start_timestamp,
        endTimestamp=end_timestamp,
    )

    # ── Link all Messages to this Segment (single round-trip via UNWIND) ──────
    # MERGE is idempotent — safe if this tx is retried.
    message_items = [
        {"messageId": mid, "order": order}
        for order, mid in enumerate(message_ids)
    ]
    await tx.run(
        """
        UNWIND $messageItems AS item
        MATCH (s:Segment {conversationId: $conversationId, segmentIndex: $segIndex})
        MATCH (m:Message {messageId: item.messageId})
        MERGE (s)-[:CONTAINS_MESSAGE {order: item.order}]->(m)
        """,
        messageItems=message_items,
        conversationId=conversation_id,
        segIndex=seg_index,
    )

    # ── Update Conversation.segmentCount to match actual node count ───────────
    # Counted from the graph rather than incremented, so it is always correct
    # even if a prior partial write left the counter in an inconsistent state.
    await tx.run(
        """
        MATCH (c:Conversation {conversationId: $convId})
        OPTIONAL MATCH (c)-[:HAS_SEGMENT]->(s:Segment)
        WITH c, count(s) AS actualCount
        SET c.segmentCount = actualCount
        """,
        convId=conversation_id,
    )

    # ── Link to previous Segment via NEXT_SEGMENT ─────────────────────────────
    if prev_seg_index is not None:
        await tx.run(
            """
            MATCH (prev:Segment {conversationId: $convId, segmentIndex: $prevIdx})
            MATCH (curr:Segment {conversationId: $convId, segmentIndex: $currIdx})
            MERGE (prev)-[:NEXT_SEGMENT]->(curr)
            """,
            convId=conversation_id_for_prev,
            prevIdx=prev_seg_index,
            currIdx=seg_index,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_segment_content(messages: list[dict]) -> str:
    """
    Concatenate messages into the verbatim segment content string.

    Format:
        USER [2026-03-05T14:32:00+00:00]: message content here
        ASSISTANT [2026-03-05T14:33:00+00:00]: full response here
        ...
    """
    parts = []
    for msg in messages:
        role      = msg["role"].upper()
        timestamp = str(msg["timestamp"])
        content   = msg["content"]
        parts.append(f"{role} [{timestamp}]: {content}")
    return "\n".join(parts)
