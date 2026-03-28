"""
Read Service — Phase 3
=======================
Verbatim conversation retrieval: read a single conversation in full, or list
all conversations for a user with optional filtering and pagination.

Design rules:
  - All reads use session.execute_read() — auto-retry, read routing.
  - Results are consumed inside the transaction function (Neo4j 6.x rule).
  - No data is modified, generated, or interpreted. Verbatim only.
  - Ownership is enforced at the Cypher level (userId predicate) — a user
    can never read another user's conversations.
"""

import logging

from neo4j import AsyncDriver, AsyncManagedTransaction

from ..config import settings
from ..models.requests import (
    ConversationReadResponse,
    ConversationSummary,
    ListConversationsResponse,
    MessageResult,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Public entry points
# ─────────────────────────────────────────────────────────────────────────────

async def get_conversation(
    driver: AsyncDriver,
    conversation_id: str,
    user_id: str,
) -> ConversationReadResponse | None:
    """
    Return the full verbatim conversation with all messages in chronological
    order, or None if no matching conversation exists for this user.

    Ownership is enforced: the Cypher MATCH includes both conversationId AND
    userId, so a user cannot read another user's conversation by guessing an ID.
    """
    async with driver.session(database=settings.neo4j_database) as session:
        return await session.execute_read(
            _get_conversation_tx, conversation_id, user_id
        )


async def list_conversations(
    driver: AsyncDriver,
    user_id: str,
    provider: str | None,
    limit: int,
    offset: int,
    date_from: str | None,
    date_to: str | None,
) -> ListConversationsResponse:
    """
    Return a paginated list of conversation summaries for a user.

    Optional filters:
      - provider: restrict to one provider
      - date_from / date_to: conversations whose startedAt date falls in range
    """
    async with driver.session(database=settings.neo4j_database) as session:
        rows, total = await session.execute_read(
            _list_conversations_tx,
            user_id,
            provider,
            limit,
            offset,
            date_from,
            date_to,
        )

    conversations = [
        ConversationSummary(
            conversationId=r["conversationId"],
            provider=r["provider"],
            model=r["model"],
            messageCount=r["messageCount"] or 0,
            totalTokens=r["totalTokens"] or 0,
            segmentCount=r["segmentCount"] or 0,
            startedAt=str(r["startedAt"]) if r["startedAt"] else "",
            endedAt=str(r["endedAt"]) if r["endedAt"] else "",
            isComplete=bool(r["isComplete"]),
        )
        for r in rows
    ]

    return ListConversationsResponse(
        conversations=conversations,
        total=total,
        limit=limit,
        offset=offset,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Transaction functions
# ─────────────────────────────────────────────────────────────────────────────

async def _get_conversation_tx(
    tx: AsyncManagedTransaction,
    conversation_id: str,
    user_id: str,
) -> ConversationReadResponse | None:
    """
    Fetch conversation metadata and all messages in messageIndex order.
    Returns None when the conversation does not exist or belongs to another user.
    """
    result = await tx.run(
        """
        MATCH (c:Conversation {conversationId: $conversationId, userId: $userId})
        OPTIONAL MATCH (c)-[:HAS_MESSAGE]->(m:Message)
        WITH c,
             m
        ORDER BY m.messageIndex ASC
        WITH c,
             collect(m) AS messages
        RETURN c.conversationId  AS conversationId,
               c.userId          AS userId,
               c.provider        AS provider,
               c.model           AS model,
               c.messageCount    AS messageCount,
               c.totalTokens     AS totalTokens,
               c.startedAt       AS startedAt,
               c.endedAt         AS endedAt,
               messages          AS messages
        """,
        conversationId=conversation_id,
        userId=user_id,
    )
    record = await result.single()
    if record is None:
        return None

    raw_messages = record["messages"] or []
    message_results = [
        MessageResult(
            messageId=m["messageId"],
            role=m["role"],
            content=m["content"],
            timestamp=str(m["timestamp"]) if m["timestamp"] else "",
            tokenCount=m["tokenCount"] or 0,
        )
        for m in raw_messages
        if m is not None
    ]

    return ConversationReadResponse(
        conversationId=record["conversationId"],
        userId=record["userId"],
        provider=record["provider"] or "",
        model=record["model"] or "",
        messageCount=record["messageCount"] or 0,
        totalTokens=record["totalTokens"] or 0,
        startedAt=str(record["startedAt"]) if record["startedAt"] else "",
        endedAt=str(record["endedAt"]) if record["endedAt"] else "",
        messages=message_results,
    )


async def _list_conversations_tx(
    tx: AsyncManagedTransaction,
    user_id: str,
    provider: str | None,
    limit: int,
    offset: int,
    date_from: str | None,
    date_to: str | None,
) -> tuple[list[dict], int]:
    """
    List conversations with optional provider and date filters.
    Returns (rows, total_count) where total_count is the unfiltered total
    matching the filter predicate (before SKIP/LIMIT).
    """
    provider_clause = "AND c.provider = $provider" if provider else ""
    date_from_clause = "AND date(c.startedAt) >= date($dateFrom)" if date_from else ""
    date_to_clause   = "AND date(c.startedAt) <= date($dateTo)"   if date_to   else ""

    # Total count query
    count_result = await tx.run(
        f"""
        MATCH (c:Conversation {{userId: $userId}})
        WHERE true
          {provider_clause}
          {date_from_clause}
          {date_to_clause}
        RETURN count(c) AS total
        """,
        userId=user_id,
        provider=provider,
        dateFrom=date_from,
        dateTo=date_to,
    )
    count_record = await count_result.single()
    total = count_record["total"] if count_record else 0

    # Paginated rows
    rows_result = await tx.run(
        f"""
        MATCH (c:Conversation {{userId: $userId}})
        WHERE true
          {provider_clause}
          {date_from_clause}
          {date_to_clause}
        RETURN c.conversationId  AS conversationId,
               c.provider        AS provider,
               c.model           AS model,
               c.messageCount    AS messageCount,
               c.totalTokens     AS totalTokens,
               c.segmentCount    AS segmentCount,
               c.startedAt       AS startedAt,
               c.endedAt         AS endedAt,
               c.isComplete      AS isComplete
        ORDER BY c.startedAt DESC
        SKIP $offset
        LIMIT $limit
        """,
        userId=user_id,
        provider=provider,
        dateFrom=date_from,
        dateTo=date_to,
        offset=offset,
        limit=limit,
    )
    rows = await rows_result.data()
    return rows, total
