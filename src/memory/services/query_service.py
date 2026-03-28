"""
Query Service — Phase 2
========================
Implements the 8-step retrieval pipeline from the system architecture.

Pipeline:
  Step 1: Date pre-filter  ┐ Run in parallel via asyncio.gather
  Step 2: Embed query      ┘
  Step 3: Three parallel vector searches (Segment, Message, Chunk)
          Falls back to full-text search if no embeddings exist or
          all vector scores are below the similarity threshold (0.70).
  Step 4: Deduplicate and merge results by messageId (keep highest score)
  Step 5: Expand context — fetch up to 2 neighboring messages per match
  Step 6: Rank (two modes):
            No date filter → 0.7×semantic + 0.2×recency + 0.1×context_bonus
            Date filter    → 0.8×semantic + 0.1×date_match + 0.1×context_bonus
  Step 7: Token-budget-aware assembly — skip messages that exceed budget,
          never truncate mid-message, group by conversation
  Step 8: Return QueryResponse

Parallelism:
  Steps 1+2 run via asyncio.gather (one Neo4j session + one OpenAI call).
  Step 3 opens THREE separate Neo4j sessions so the searches genuinely
  run concurrently on separate connection-pool slots.

Concurrency safety:
  All reads use session.execute_read().  No writes.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone

from neo4j import AsyncDriver, AsyncManagedTransaction
from openai import AsyncOpenAI

from ..config import settings
from ..models.requests import QueryRequest, QueryResponse, ConversationResult, MessageResult
from .embedding_service import get_query_embedding

logger = logging.getLogger(__name__)

# ── Tunables ──────────────────────────────────────────────────────────────────

_VECTOR_SEARCH_MULTIPLIER = 10   # over-fetch before userId filter
_SIMILARITY_THRESHOLD     = 0.70  # minimum cosine score to include a result
_FULLTEXT_MIN_SCORE       = 0.0   # lucene scores; any positive result is included


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

async def query_memory(
    driver: AsyncDriver,
    openai_client: AsyncOpenAI | None,
    redis_client,
    request: QueryRequest,
) -> QueryResponse:
    """Execute the full retrieval pipeline and return a QueryResponse."""
    t0 = time.perf_counter()

    # ── Steps 1 + 2: date pre-filter and query embedding in parallel ──────────
    async with driver.session(database=settings.neo4j_database) as date_session:
        candidate_ids_task = date_session.execute_read(
            _date_prefilter_tx, request
        )
        embed_task = get_query_embedding(openai_client, redis_client, request.query) \
            if openai_client is not None else _null_coroutine()

        candidate_ids, query_embedding = await asyncio.gather(
            candidate_ids_task, embed_task
        )

    # If a date filter was requested but matched zero conversations, short-circuit.
    if request.has_date_filter and len(candidate_ids) == 0:
        return QueryResponse(
            results=[],
            totalResults=0,
            tokenCount=0,
            queryLatencyMs=round((time.perf_counter() - t0) * 1000, 1),
            dateFilterApplied=True,
            searchMode="empty",
        )

    # ── Step 3: three parallel vector searches ────────────────────────────────
    raw_hits: list[dict] = []
    search_mode = "empty"

    if query_embedding is not None:
        async with (
            driver.session(database=settings.neo4j_database) as seg_session,
            driver.session(database=settings.neo4j_database) as msg_session,
            driver.session(database=settings.neo4j_database) as chunk_session,
        ):
            seg_hits, msg_hits, chunk_hits = await asyncio.gather(
                seg_session.execute_read(
                    _search_segments_tx, request, query_embedding, candidate_ids
                ),
                msg_session.execute_read(
                    _search_messages_tx, request, query_embedding, candidate_ids
                ),
                chunk_session.execute_read(
                    _search_chunks_tx, request, query_embedding, candidate_ids
                ),
            )

        all_hits = seg_hits + msg_hits + chunk_hits
        raw_hits = [h for h in all_hits if h["score"] >= _SIMILARITY_THRESHOLD]

        if raw_hits:
            search_mode = "vector"

    # ── Full-text fallback ────────────────────────────────────────────────────
    if not raw_hits:
        async with driver.session(database=settings.neo4j_database) as ft_session:
            raw_hits = await ft_session.execute_read(
                _fulltext_search_tx, request, candidate_ids
            )
        search_mode = "fulltext" if raw_hits else "empty"

    if not raw_hits:
        return QueryResponse(
            results=[],
            totalResults=0,
            tokenCount=0,
            queryLatencyMs=(time.perf_counter() - t0) * 1000,
            dateFilterApplied=request.has_date_filter,
            searchMode="empty",
        )

    # ── Step 4: deduplicate by messageId (keep highest score) ─────────────────
    deduped: dict[str, dict] = {}
    for hit in raw_hits:
        mid = hit["messageId"]
        if mid not in deduped or hit["score"] > deduped[mid]["score"]:
            deduped[mid] = hit

    # ── Step 5: context expansion — fetch ±2 neighbours ──────────────────────
    async with driver.session(database=settings.neo4j_database) as ctx_session:
        expanded = await ctx_session.execute_read(
            _expand_context_tx, list(deduped.keys()), request.userId
        )

    # Merge neighbours into deduped dict with lower score
    for neighbor in expanded:
        mid = neighbor["messageId"]
        if mid not in deduped:
            deduped[mid] = neighbor

    # ── Step 6: rank all messages ─────────────────────────────────────────────
    ranked = _rank_messages(list(deduped.values()), request)

    # ── Step 7: token-budget-aware assembly ───────────────────────────────────
    assembled, total_tokens = _assemble_results(ranked, request)

    latency_ms = (time.perf_counter() - t0) * 1000
    logger.info(
        "query_complete user=%s mode=%s results=%d tokens=%d latency_ms=%.1f",
        request.userId, search_mode, len(assembled), total_tokens, latency_ms,
    )

    return QueryResponse(
        results=assembled,
        totalResults=len(assembled),
        tokenCount=total_tokens,
        queryLatencyMs=round(latency_ms, 1),
        dateFilterApplied=request.has_date_filter,
        searchMode=search_mode,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Step 1: Date pre-filter
# ─────────────────────────────────────────────────────────────────────────────

async def _date_prefilter_tx(
    tx: AsyncManagedTransaction,
    request: QueryRequest,
) -> list[str]:
    """
    Return the set of conversationIds that fall within the requested date range.
    Empty list means no filter (all conversations are candidates).
    Uses the composite index (Conversation.userId, Conversation.startedAt).
    """
    if not request.has_date_filter:
        return []

    result = await tx.run(
        """
        MATCH (c:Conversation {userId: $userId})
        WHERE date(c.startedAt) >= date($dateFrom)
          AND date(c.startedAt) <= date($dateTo)
        RETURN c.conversationId AS conversationId
        """,
        userId=request.userId,
        dateFrom=request.effective_date_from,
        dateTo=request.effective_date_to,
    )
    records = await result.data()
    return [r["conversationId"] for r in records]


# ─────────────────────────────────────────────────────────────────────────────
# Step 3: Vector searches
# ─────────────────────────────────────────────────────────────────────────────

def _candidate_filter_clause(candidate_ids: list[str]) -> str:
    """Returns a Cypher WHERE fragment for the candidate filter."""
    return (
        "AND node.conversationId IN $candidateIds"
        if candidate_ids else ""
    )


async def _search_segments_tx(
    tx: AsyncManagedTransaction,
    request: QueryRequest,
    query_embedding: list[float],
    candidate_ids: list[str],
) -> list[dict]:
    """
    Vector search on Segment.embedding.
    Returns matched messages within those segments (not the segment itself)
    so that all three search paths return the same message-level result shape.
    """
    candidate_clause = _candidate_filter_clause(candidate_ids)
    fetch_count = request.topK * _VECTOR_SEARCH_MULTIPLIER

    result = await tx.run(
        f"""
        CALL db.index.vector.queryNodes('index_vector_segment', $fetchCount, $queryEmbedding)
        YIELD node, score
        WHERE node.userId = $userId
          {candidate_clause}
        WITH node AS seg, score
        MATCH (seg)-[:CONTAINS_MESSAGE]->(m:Message)
        MATCH (c:Conversation {{conversationId: seg.conversationId}})
        RETURN m.messageId        AS messageId,
               m.role             AS role,
               m.content          AS content,
               m.timestamp        AS timestamp,
               m.tokenCount       AS tokenCount,
               m.conversationId   AS conversationId,
               c.provider         AS provider,
               c.model            AS model,
               c.startedAt        AS conversationStartedAt,
               score              AS score,
               false              AS isNeighbor
        ORDER BY score DESC
        LIMIT $topK
        """,
        fetchCount=fetch_count,
        queryEmbedding=query_embedding,
        userId=request.userId,
        candidateIds=candidate_ids,
        topK=request.topK,
    )
    return await result.data()


async def _search_messages_tx(
    tx: AsyncManagedTransaction,
    request: QueryRequest,
    query_embedding: list[float],
    candidate_ids: list[str],
) -> list[dict]:
    """Vector search on Message.embedding (short messages only — ≤512 tokens)."""
    candidate_clause = _candidate_filter_clause(candidate_ids)
    fetch_count = request.topK * _VECTOR_SEARCH_MULTIPLIER

    result = await tx.run(
        f"""
        CALL db.index.vector.queryNodes('index_vector_message', $fetchCount, $queryEmbedding)
        YIELD node, score
        WHERE node.userId = $userId
          {candidate_clause}
        WITH node AS m, score
        MATCH (c:Conversation {{conversationId: m.conversationId}})
        RETURN m.messageId        AS messageId,
               m.role             AS role,
               m.content          AS content,
               m.timestamp        AS timestamp,
               m.tokenCount       AS tokenCount,
               m.conversationId   AS conversationId,
               c.provider         AS provider,
               c.model            AS model,
               c.startedAt        AS conversationStartedAt,
               score              AS score,
               false              AS isNeighbor
        ORDER BY score DESC
        LIMIT $topK
        """,
        fetchCount=fetch_count,
        queryEmbedding=query_embedding,
        userId=request.userId,
        candidateIds=candidate_ids,
        topK=request.topK,
    )
    return await result.data()


async def _search_chunks_tx(
    tx: AsyncManagedTransaction,
    request: QueryRequest,
    query_embedding: list[float],
    candidate_ids: list[str],
) -> list[dict]:
    """
    Vector search on Chunk.embedding.
    Always returns the PARENT Message, not the chunk itself.
    Chunks are index artifacts only — never exposed to the LLM client.
    """
    candidate_clause = _candidate_filter_clause(candidate_ids)
    fetch_count = request.topK * _VECTOR_SEARCH_MULTIPLIER

    result = await tx.run(
        f"""
        CALL db.index.vector.queryNodes('index_vector_chunk', $fetchCount, $queryEmbedding)
        YIELD node, score
        WHERE node.userId = $userId
          {candidate_clause}
        WITH node AS ch, score
        MATCH (m:Message {{messageId: ch.messageId}})
        MATCH (c:Conversation {{conversationId: m.conversationId}})
        RETURN m.messageId        AS messageId,
               m.role             AS role,
               m.content          AS content,
               m.timestamp        AS timestamp,
               m.tokenCount       AS tokenCount,
               m.conversationId   AS conversationId,
               c.provider         AS provider,
               c.model            AS model,
               c.startedAt        AS conversationStartedAt,
               score              AS score,
               false              AS isNeighbor
        ORDER BY score DESC
        LIMIT $topK
        """,
        fetchCount=fetch_count,
        queryEmbedding=query_embedding,
        userId=request.userId,
        candidateIds=candidate_ids,
        topK=request.topK,
    )
    return await result.data()


async def _fulltext_search_tx(
    tx: AsyncManagedTransaction,
    request: QueryRequest,
    candidate_ids: list[str],
) -> list[dict]:
    """
    Full-text keyword fallback using index_fulltext_message.
    Used when vector search returns nothing (embeddings not yet computed
    or all scores below threshold).  Date pre-filter is applied here too.
    """
    candidate_clause = (
        "AND m.conversationId IN $candidateIds"
        if candidate_ids else ""
    )
    result = await tx.run(
        f"""
        CALL db.index.fulltext.queryNodes('index_fulltext_message', $queryText)
        YIELD node AS m, score
        WHERE m.userId = $userId
          {candidate_clause}
        MATCH (c:Conversation {{conversationId: m.conversationId}})
        RETURN m.messageId        AS messageId,
               m.role             AS role,
               m.content          AS content,
               m.timestamp        AS timestamp,
               m.tokenCount       AS tokenCount,
               m.conversationId   AS conversationId,
               c.provider         AS provider,
               c.model            AS model,
               c.startedAt        AS conversationStartedAt,
               score              AS score,
               false              AS isNeighbor
        ORDER BY score DESC
        LIMIT $topK
        """,
        queryText=request.query,
        userId=request.userId,
        candidateIds=candidate_ids,
        topK=request.topK,
    )
    return await result.data()


# ─────────────────────────────────────────────────────────────────────────────
# Step 5: Context expansion
# ─────────────────────────────────────────────────────────────────────────────

async def _expand_context_tx(
    tx: AsyncManagedTransaction,
    message_ids: list[str],
    user_id: str,
) -> list[dict]:
    """
    For each matched messageId, fetch up to 2 messages before and 2 after
    in the NEXT_MESSAGE chain.  Returns neighbor messages with score=0
    and isNeighbor=true so the ranker can apply the context_bonus.
    """
    result = await tx.run(
        """
        UNWIND $messageIds AS mid
        MATCH (anchor:Message {messageId: mid})
        MATCH (neighbor:Message)
        WHERE neighbor.conversationId = anchor.conversationId
          AND neighbor.userId         = $userId
          AND neighbor.messageIndex >= anchor.messageIndex - 2
          AND neighbor.messageIndex <= anchor.messageIndex + 2
          AND NOT (neighbor.messageId IN $messageIds)
        MATCH (c:Conversation {conversationId: neighbor.conversationId})
        RETURN DISTINCT
               neighbor.messageId      AS messageId,
               neighbor.role           AS role,
               neighbor.content        AS content,
               neighbor.timestamp      AS timestamp,
               neighbor.tokenCount     AS tokenCount,
               neighbor.conversationId AS conversationId,
               c.provider              AS provider,
               c.model                 AS model,
               c.startedAt             AS conversationStartedAt,
               0.0                     AS score,
               true                    AS isNeighbor
        """,
        messageIds=message_ids,
        userId=user_id,
    )
    return await result.data()


# ─────────────────────────────────────────────────────────────────────────────
# Step 6: Ranking
# ─────────────────────────────────────────────────────────────────────────────

def _recency_factor(conversation_started_at) -> float:
    """
    Decay factor based on age of the conversation.
    Piecewise linear between the architecture's defined anchor points.
    """
    if conversation_started_at is None:
        return 0.5

    # Neo4j returns datetime objects; handle both datetime and string
    if isinstance(conversation_started_at, str):
        try:
            ts = datetime.fromisoformat(conversation_started_at)
        except ValueError:
            return 0.5
    else:
        # Neo4j neo4j.time.DateTime — convert to Python datetime
        try:
            ts = datetime(
                conversation_started_at.year,
                conversation_started_at.month,
                conversation_started_at.day,
                tzinfo=timezone.utc,
            )
        except Exception:
            return 0.5

    now = datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    age_days = (now - ts).days

    # Anchor points from architecture doc
    anchors = [(0, 1.0), (7, 0.9), (30, 0.7), (90, 0.5), (365, 0.2)]
    for i in range(len(anchors) - 1):
        d0, f0 = anchors[i]
        d1, f1 = anchors[i + 1]
        if age_days <= d1:
            t = (age_days - d0) / (d1 - d0)
            return f0 + t * (f1 - f0)
    return 0.2


def _rank_messages(messages: list[dict], request: QueryRequest) -> list[dict]:
    """
    Compute final_score for each message using two ranking modes.

    Mode A (no date filter):
      final_score = 0.7×cosine + 0.2×recency + 0.1×context_bonus

    Mode B (date filter applied):
      final_score = 0.8×cosine + 0.1×date_match_bonus + 0.1×context_bonus
    """
    has_date = request.has_date_filter

    for msg in messages:
        cosine     = float(msg.get("score") or 0.0)
        is_neighbor = bool(msg.get("isNeighbor", False))

        context_bonus = 0.05 if is_neighbor else 0.0

        if has_date:
            date_match_bonus = 1.0   # all candidates passed the pre-filter
            msg["finalScore"] = (
                0.8 * cosine
                + 0.1 * date_match_bonus
                + 0.1 * context_bonus
            )
        else:
            recency = _recency_factor(msg.get("conversationStartedAt"))
            msg["finalScore"] = (
                0.7 * cosine
                + 0.2 * recency
                + 0.1 * context_bonus
            )

    return sorted(messages, key=lambda m: m["finalScore"], reverse=True)


# ─────────────────────────────────────────────────────────────────────────────
# Step 7: Token-budget assembly
# ─────────────────────────────────────────────────────────────────────────────

def _assemble_results(
    ranked: list[dict],
    request: QueryRequest,
) -> tuple[list[ConversationResult], int]:
    """
    Walk ranked messages, skip any that would exceed the token budget,
    group by conversation, and return ConversationResult objects.
    Never truncates a message mid-content.
    """
    # Group selected messages by conversationId
    selected: dict[str, list[dict]] = {}
    total_tokens = 0

    for msg in ranked:
        token_count = msg.get("tokenCount") or 0
        if total_tokens + token_count > request.tokenBudget:
            continue
        conv_id = msg["conversationId"]
        selected.setdefault(conv_id, []).append(msg)
        total_tokens += token_count

    if not selected:
        return [], 0

    # Build ConversationResult list, ordered by best score in each conversation
    results: list[ConversationResult] = []
    for conv_id, msgs in selected.items():
        best_score = max(m["finalScore"] for m in msgs)

        # Sort messages within conversation by timestamp
        msgs_sorted = sorted(msgs, key=lambda m: str(m.get("timestamp") or ""))

        # Derive conversation date from startedAt of first message
        started_at = msgs_sorted[0].get("conversationStartedAt")
        conv_date = _to_date_str(started_at)
        started_at_str = str(started_at) if started_at else ""

        results.append(ConversationResult(
            score=round(best_score, 4),
            conversationId=conv_id,
            provider=msgs_sorted[0].get("provider") or "",
            model=msgs_sorted[0].get("model") or "",
            conversationDate=conv_date,
            conversationStartedAt=started_at_str,
            messages=[
                MessageResult(
                    messageId=m["messageId"],
                    role=m["role"],
                    content=m["content"],
                    timestamp=str(m.get("timestamp") or ""),
                    tokenCount=m.get("tokenCount") or 0,
                )
                for m in msgs_sorted
            ],
        ))

    results.sort(key=lambda r: r.score, reverse=True)
    return results, total_tokens


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _to_date_str(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value[:10]
    try:
        return f"{value.year:04d}-{value.month:02d}-{value.day:02d}"
    except Exception:
        return str(value)[:10]


async def _null_coroutine():
    """Placeholder coroutine that returns None — used when OpenAI is absent."""
    return None
