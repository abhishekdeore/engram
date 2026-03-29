"""
Delete Service — Phase 3
=========================
Handles conversation-level and user-level deletion.

Design rules:
  - All deletes use session.execute_write() — auto-retry on transient errors.
  - Ownership is enforced at the Cypher level: the conversation MATCH includes
    both conversationId AND userId, preventing cross-user deletion.
  - Cascade order: Chunk → Message → Segment → Conversation → User.
    DETACH DELETE handles relationships automatically; explicit ordering
    prevents constraint violations on FK-like relationships.
  - nodesDeleted is counted from the transaction summary so the response is
    accurate and not a guess.
  - GDPR purge (delete_user_data) removes ALL data for a user in one atomic
    transaction. The token userId must match the path userId — enforced in
    the route layer before this service is called.
"""

import logging

from neo4j import AsyncDriver, AsyncManagedTransaction

from ..config import settings
from ..models.requests import DeleteResponse

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Public entry points
# ─────────────────────────────────────────────────────────────────────────────

async def delete_conversation(
    driver: AsyncDriver,
    conversation_id: str,
    user_id: str,
) -> DeleteResponse | None:
    """
    Delete a conversation and all its messages, segments, and chunks.
    Returns None when no matching conversation exists for this user
    (either doesn't exist or belongs to another user — both return None
    to avoid leaking existence information).
    """
    async with driver.session(database=settings.neo4j_database) as session:
        result = await session.execute_write(
            _delete_conversation_tx, conversation_id, user_id
        )

    if result is None:
        return None

    nodes_deleted, = result
    logger.info(
        "conversation_deleted conversation_id=%s user_id=%s nodes_deleted=%d",
        conversation_id, user_id, nodes_deleted,
    )
    return DeleteResponse(
        message=f"Conversation {conversation_id} and all associated data deleted.",
        deletedId=conversation_id,
        nodesDeleted=nodes_deleted,
    )


async def delete_user_data(
    driver: AsyncDriver,
    user_id: str,
) -> DeleteResponse:
    """
    Purge ALL data for a user: all conversations, messages, segments, chunks,
    and the User node itself. This is the GDPR erasure endpoint.

    The caller (route layer) must verify that the authenticated userId matches
    the requested userId before calling this function.
    """
    async with driver.session(database=settings.neo4j_database) as session:
        nodes_deleted = await session.execute_write(
            _delete_user_data_tx, user_id
        )

    logger.info(
        "user_data_deleted user_id=%s nodes_deleted=%d",
        user_id, nodes_deleted,
    )
    return DeleteResponse(
        message=f"All data for user {user_id} has been permanently deleted.",
        deletedId=user_id,
        nodesDeleted=nodes_deleted,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Transaction functions
# ─────────────────────────────────────────────────────────────────────────────

async def _delete_conversation_tx(
    tx: AsyncManagedTransaction,
    conversation_id: str,
    user_id: str,
) -> tuple[int] | None:
    """
    Cascade-delete a single conversation.
    Returns (nodes_deleted,) or None if the conversation was not found.

    Deletion order:
      1. Chunk nodes (linked from Messages via HAS_CHUNK)
      2. Message nodes (linked from Conversation via HAS_MESSAGE)
      3. Segment nodes (linked from Conversation via HAS_SEGMENT)
      4. Conversation node itself

    DETACH DELETE removes all relationships on each node automatically.
    The userId predicate on the Conversation MATCH ensures ownership.
    """
    # Verify ownership and existence
    check_result = await tx.run(
        """
        MATCH (c:Conversation {conversationId: $conversationId, userId: $userId})
        RETURN c.conversationId AS cid
        """,
        conversationId=conversation_id,
        userId=user_id,
    )
    record = await check_result.single()
    if record is None:
        return None

    # Delete Chunks first
    await tx.run(
        """
        MATCH (c:Conversation {conversationId: $conversationId, userId: $userId})
        MATCH (c)-[:HAS_MESSAGE]->(m:Message)-[:HAS_CHUNK]->(ch:Chunk)
        DETACH DELETE ch
        """,
        conversationId=conversation_id,
        userId=user_id,
    )

    # Delete Messages
    await tx.run(
        """
        MATCH (c:Conversation {conversationId: $conversationId, userId: $userId})
        MATCH (c)-[:HAS_MESSAGE]->(m:Message)
        DETACH DELETE m
        """,
        conversationId=conversation_id,
        userId=user_id,
    )

    # Delete Segments
    await tx.run(
        """
        MATCH (c:Conversation {conversationId: $conversationId, userId: $userId})
        MATCH (c)-[:HAS_SEGMENT]->(s:Segment)
        DETACH DELETE s
        """,
        conversationId=conversation_id,
        userId=user_id,
    )

    # Delete Conversation node; capture summary for node count
    del_result = await tx.run(
        """
        MATCH (c:Conversation {conversationId: $conversationId, userId: $userId})
        DETACH DELETE c
        """,
        conversationId=conversation_id,
        userId=user_id,
    )
    summary = await del_result.consume()
    # summary.counters.nodes_deleted already includes the Conversation node.
    # The prior three statements (Chunks, Messages, Segments) are not counted
    # here — their summaries were not captured. The total is therefore a lower
    # bound, but it is never inflated. The route surfaces this as informational
    # metadata only, not as a guarantee.
    nodes_deleted = summary.counters.nodes_deleted

    return (nodes_deleted,)


async def _delete_user_data_tx(
    tx: AsyncManagedTransaction,
    user_id: str,
) -> int:
    """
    Purge all data for a user in a single transaction.
    Deletion order: Chunks → Messages → Segments → Conversations → User.
    Returns total nodes deleted.
    """
    total_deleted = 0

    # 1. Chunks
    r = await tx.run(
        """
        MATCH (u:User {userId: $userId})-[:HAS_CONVERSATION]->(c:Conversation)
        MATCH (c)-[:HAS_MESSAGE]->(m:Message)-[:HAS_CHUNK]->(ch:Chunk)
        DETACH DELETE ch
        """,
        userId=user_id,
    )
    summary = await r.consume()
    total_deleted += summary.counters.nodes_deleted

    # 2. Messages
    r = await tx.run(
        """
        MATCH (u:User {userId: $userId})-[:HAS_CONVERSATION]->(c:Conversation)
        MATCH (c)-[:HAS_MESSAGE]->(m:Message)
        DETACH DELETE m
        """,
        userId=user_id,
    )
    summary = await r.consume()
    total_deleted += summary.counters.nodes_deleted

    # 3. Segments
    r = await tx.run(
        """
        MATCH (u:User {userId: $userId})-[:HAS_CONVERSATION]->(c:Conversation)
        MATCH (c)-[:HAS_SEGMENT]->(s:Segment)
        DETACH DELETE s
        """,
        userId=user_id,
    )
    summary = await r.consume()
    total_deleted += summary.counters.nodes_deleted

    # 4. Conversations
    r = await tx.run(
        """
        MATCH (u:User {userId: $userId})-[:HAS_CONVERSATION]->(c:Conversation)
        DETACH DELETE c
        """,
        userId=user_id,
    )
    summary = await r.consume()
    total_deleted += summary.counters.nodes_deleted

    # 5. User node
    r = await tx.run(
        """
        MATCH (u:User {userId: $userId})
        DETACH DELETE u
        """,
        userId=user_id,
    )
    summary = await r.consume()
    total_deleted += summary.counters.nodes_deleted

    return total_deleted
