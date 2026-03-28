"""
Embedding Service — Phase 2
============================
Computes and stores vector embeddings for Segment, Message, and Chunk nodes.

Design rules:
  - Embeddings are computed with OpenAI text-embedding-3-small (1536 dims).
  - All OpenAI calls are checked against a Redis cache keyed by SHA-256(content).
    Redis is optional: absent or unavailable means no caching, never a crash.
  - Segment content exceeding the model's 8191-token limit is handled via
    head+tail averaging: embed the first MAX_EMBED_TOKENS//2 tokens and the
    last MAX_EMBED_TOKENS//2 tokens, average the two vectors, then L2-normalise.
    This is a deterministic math operation — no generative model involved.
  - All Neo4j writes use db.create.setNodeVectorProperty so the property
    is stored as the correct FloatArray type that vector indexes require.
  - Results are consumed inside transaction functions (Neo4j 6.x rule).
  - If openai_client is None, the entire pipeline is a no-op.
    Verbatim storage (Phase 1) is already complete before this runs.
    Full-text fallback search still works without embeddings.
"""

import hashlib
import logging
import json
from typing import Optional

import tiktoken
from neo4j import AsyncDriver, AsyncManagedTransaction
from openai import AsyncOpenAI

from ..config import settings

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

EMBEDDING_MODEL   = settings.embedding_model
EMBEDDING_DIMS    = settings.embedding_dimensions
MAX_EMBED_TOKENS  = 8191      # hard limit of text-embedding-3-small
CHUNK_THRESHOLD   = 512       # messages above this token count get chunked
CHUNK_SIZE        = 512       # tokens per chunk
CHUNK_OVERLAP     = 100       # overlapping tokens between consecutive chunks
REDIS_TTL         = settings.embedding_cache_ttl_seconds
REDIS_KEY_PREFIX  = "engram:embed:"

# tiktoken encoding used by text-embedding-3-small
_ENCODING = tiktoken.get_encoding("cl100k_base")


# ─────────────────────────────────────────────────────────────────────────────
# Public entry points
# ─────────────────────────────────────────────────────────────────────────────

async def embed_new_content(
    driver: AsyncDriver,
    openai_client: Optional[AsyncOpenAI],
    redis_client,
    conversation_id: str,
) -> None:
    """
    Find all segments and messages in a conversation that do not yet have
    embeddings, compute them, and write them back to Neo4j.

    Called from write_service.py as the final step of the BackgroundTask.
    All failures are logged and swallowed — verbatim storage is already safe.
    """
    if openai_client is None:
        return

    try:
        await _embed_segments(driver, openai_client, redis_client, conversation_id)
        await _embed_messages(driver, openai_client, redis_client, conversation_id)
    except Exception:
        logger.exception(
            "embedding_failed conversation_id=%s", conversation_id
        )


async def get_query_embedding(
    openai_client: AsyncOpenAI,
    redis_client,
    query_text: str,
) -> list[float] | None:
    """
    Compute the embedding for a query string, using Redis cache if available.
    Returns None if the OpenAI call fails.
    """
    try:
        return await _get_or_compute_embedding(openai_client, redis_client, query_text)
    except Exception:
        logger.exception("query_embedding_failed query=%r", query_text[:80])
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Segment embedding
# ─────────────────────────────────────────────────────────────────────────────

async def _embed_segments(
    driver: AsyncDriver,
    openai_client: AsyncOpenAI,
    redis_client,
    conversation_id: str,
) -> None:
    """Fetch all segments without embeddings, compute them, write back."""
    async with driver.session(database=settings.neo4j_database) as session:
        segments = await session.execute_read(
            _get_unembedded_segments_tx, conversation_id
        )

    if not segments:
        return

    logger.debug(
        "embedding_segments conversation_id=%s count=%d",
        conversation_id, len(segments),
    )

    for seg in segments:
        embedding = await _get_or_compute_embedding(
            openai_client, redis_client, seg["content"]
        )
        if embedding is None:
            continue

        async with driver.session(database=settings.neo4j_database) as session:
            await session.execute_write(
                _store_segment_embedding_tx,
                seg["segmentId"],
                embedding,
            )
        logger.debug("segment_embedded segment_id=%s", seg["segmentId"])


async def _get_unembedded_segments_tx(
    tx: AsyncManagedTransaction,
    conversation_id: str,
) -> list[dict]:
    result = await tx.run(
        """
        MATCH (c:Conversation {conversationId: $convId})-[:HAS_SEGMENT]->(s:Segment)
        WHERE s.embedding IS NULL
        RETURN s.segmentId   AS segmentId,
               s.content     AS content,
               s.tokenCount  AS tokenCount
        """,
        convId=conversation_id,
    )
    return await result.data()


async def _store_segment_embedding_tx(
    tx: AsyncManagedTransaction,
    segment_id: str,
    embedding: list[float],
) -> None:
    await tx.run(
        """
        MATCH (s:Segment {segmentId: $segmentId})
        CALL db.create.setNodeVectorProperty(s, 'embedding', $embedding)
        """,
        segmentId=segment_id,
        embedding=embedding,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Message embedding  (short → direct, long → chunk)
# ─────────────────────────────────────────────────────────────────────────────

async def _embed_messages(
    driver: AsyncDriver,
    openai_client: AsyncOpenAI,
    redis_client,
    conversation_id: str,
) -> None:
    """
    For each unembedded message in the conversation:
      - tokenCount ≤ CHUNK_THRESHOLD → embed content directly
      - tokenCount >  CHUNK_THRESHOLD → chunk, embed each chunk, create Chunk nodes
    """
    async with driver.session(database=settings.neo4j_database) as session:
        messages = await session.execute_read(
            _get_unembedded_messages_tx, conversation_id
        )

    if not messages:
        return

    logger.debug(
        "embedding_messages conversation_id=%s count=%d",
        conversation_id, len(messages),
    )

    for msg in messages:
        token_count = msg["tokenCount"] or 0

        if token_count <= CHUNK_THRESHOLD:
            # Short message: embed directly on Message node
            embedding = await _get_or_compute_embedding(
                openai_client, redis_client, msg["content"]
            )
            if embedding is None:
                continue
            async with driver.session(database=settings.neo4j_database) as session:
                await session.execute_write(
                    _store_message_embedding_tx,
                    msg["messageId"],
                    embedding,
                )
        else:
            # Long message: chunk, embed each, store Chunk nodes
            chunks = _split_into_chunks(msg["content"])
            for chunk_index, chunk_text in enumerate(chunks):
                embedding = await _get_or_compute_embedding(
                    openai_client, redis_client, chunk_text
                )
                if embedding is None:
                    continue
                async with driver.session(database=settings.neo4j_database) as session:
                    await session.execute_write(
                        _create_chunk_tx,
                        message_id=msg["messageId"],
                        conversation_id=conversation_id,
                        user_id=msg["userId"],
                        segment_id=msg["segmentId"],
                        chunk_index=chunk_index,
                        content=chunk_text,
                        token_count=_count_tokens(chunk_text),
                        embedding=embedding,
                    )


async def _get_unembedded_messages_tx(
    tx: AsyncManagedTransaction,
    conversation_id: str,
) -> list[dict]:
    result = await tx.run(
        """
        MATCH (c:Conversation {conversationId: $convId})-[:HAS_MESSAGE]->(m:Message)
        WHERE m.embedding IS NULL
          AND NOT EXISTS { MATCH (m)-[:HAS_CHUNK]->(:Chunk) }
        OPTIONAL MATCH (seg:Segment)-[:CONTAINS_MESSAGE]->(m)
        RETURN m.messageId      AS messageId,
               m.content        AS content,
               m.tokenCount     AS tokenCount,
               m.userId         AS userId,
               seg.segmentId    AS segmentId
        """,
        convId=conversation_id,
    )
    return await result.data()


async def _store_message_embedding_tx(
    tx: AsyncManagedTransaction,
    message_id: str,
    embedding: list[float],
) -> None:
    await tx.run(
        """
        MATCH (m:Message {messageId: $messageId})
        CALL db.create.setNodeVectorProperty(m, 'embedding', $embedding)
        """,
        messageId=message_id,
        embedding=embedding,
    )


async def _create_chunk_tx(
    tx: AsyncManagedTransaction,
    *,
    message_id: str,
    conversation_id: str,
    user_id: str,
    segment_id: str | None,
    chunk_index: int,
    content: str,
    token_count: int,
    embedding: list[float],
) -> None:
    """
    MERGE a Chunk node (idempotent on messageId+chunkIndex),
    set its embedding, and link to the parent Message.
    """
    import uuid as _uuid
    chunk_id = str(_uuid.uuid4())
    await tx.run(
        """
        MERGE (ch:Chunk {messageId: $messageId, chunkIndex: $chunkIndex})
        ON CREATE SET
            ch.chunkId        = $chunkId,
            ch.conversationId = $conversationId,
            ch.userId         = $userId,
            ch.segmentId      = $segmentId,
            ch.content        = $content,
            ch.tokenCount     = $tokenCount
        WITH ch
        CALL db.create.setNodeVectorProperty(ch, 'embedding', $embedding)
        WITH ch
        MATCH (m:Message {messageId: $messageId})
        MERGE (m)-[:HAS_CHUNK {index: $chunkIndex}]->(ch)
        """,
        chunkId=chunk_id,
        messageId=message_id,
        conversationId=conversation_id,
        userId=user_id,
        segmentId=segment_id,
        chunkIndex=chunk_index,
        content=content,
        tokenCount=token_count,
        embedding=embedding,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Core embedding computation
# ─────────────────────────────────────────────────────────────────────────────

async def _get_or_compute_embedding(
    openai_client: AsyncOpenAI,
    redis_client,
    text: str,
) -> list[float] | None:
    """
    Return cached embedding if available, otherwise compute via OpenAI and cache.
    Redis errors are silenced — fall through to OpenAI.
    """
    cache_key = REDIS_KEY_PREFIX + hashlib.sha256(text.encode()).hexdigest()

    # ── Cache read ────────────────────────────────────────────────────────────
    if redis_client is not None:
        try:
            cached = await redis_client.get(cache_key)
            if cached is not None:
                return json.loads(cached)
        except Exception:
            logger.debug("redis_cache_read_failed key=%s", cache_key[:32])

    # ── OpenAI call ───────────────────────────────────────────────────────────
    embedding = await _call_openai_embedding(openai_client, text)
    if embedding is None:
        return None

    # ── Cache write ───────────────────────────────────────────────────────────
    if redis_client is not None:
        try:
            await redis_client.set(cache_key, json.dumps(embedding), ex=REDIS_TTL)
        except Exception:
            logger.debug("redis_cache_write_failed key=%s", cache_key[:32])

    return embedding


async def _call_openai_embedding(
    openai_client: AsyncOpenAI,
    text: str,
) -> list[float] | None:
    """
    Call the OpenAI embeddings API for a single text.
    Handles the >8191-token case with head+tail averaging.
    """
    token_count = _count_tokens(text)

    if token_count <= MAX_EMBED_TOKENS:
        try:
            response = await openai_client.embeddings.create(
                input=text,
                model=EMBEDDING_MODEL,
            )
            return response.data[0].embedding
        except Exception:
            logger.exception("openai_embedding_failed token_count=%d", token_count)
            return None
    else:
        return await _head_tail_embedding(openai_client, text)


async def _head_tail_embedding(
    openai_client: AsyncOpenAI,
    text: str,
) -> list[float] | None:
    """
    For content exceeding MAX_EMBED_TOKENS:
      Embed first half-window + last half-window, then average + L2-normalise.
    This is deterministic arithmetic — no generative model involved.
    """
    half = MAX_EMBED_TOKENS // 2
    tokens = _ENCODING.encode(text)
    head_text = _ENCODING.decode(tokens[:half])
    tail_text  = _ENCODING.decode(tokens[-half:])

    try:
        response = await openai_client.embeddings.create(
            input=[head_text, tail_text],
            model=EMBEDDING_MODEL,
        )
        head_vec = response.data[0].embedding
        tail_vec = response.data[1].embedding
    except Exception:
        logger.exception("openai_head_tail_embedding_failed")
        return None

    # Average and L2-normalise
    averaged = [(h + t) / 2.0 for h, t in zip(head_vec, tail_vec)]
    norm = sum(x * x for x in averaged) ** 0.5
    if norm == 0:
        return averaged
    return [x / norm for x in averaged]


# ─────────────────────────────────────────────────────────────────────────────
# Token utilities
# ─────────────────────────────────────────────────────────────────────────────

def _count_tokens(text: str) -> int:
    return len(_ENCODING.encode(text))


def _split_into_chunks(text: str) -> list[str]:
    """
    Split text into CHUNK_SIZE-token windows with CHUNK_OVERLAP overlap.
    Returns the decoded string for each window.
    """
    tokens = _ENCODING.encode(text)
    chunks: list[str] = []
    start = 0
    while start < len(tokens):
        end = min(start + CHUNK_SIZE, len(tokens))
        chunks.append(_ENCODING.decode(tokens[start:end]))
        if end >= len(tokens):
            break
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks
