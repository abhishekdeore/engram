"""
Phase 2 — Embedding pipeline integration tests
================================================
Tests run against the real Neo4j instance.
The OpenAI client is fully mocked: embeddings.create returns a deterministic
unit vector seeded from the SHA-256 of the input text.  This lets us:
  - Verify that embeddings land on the right nodes without calling OpenAI
  - Test the Redis cache hit/miss logic
  - Test head+tail splitting for very long content
  - Test chunk creation for messages above the token threshold

Run with:
    uv run pytest tests/test_embedding_pipeline.py -v
"""

import sys
import hashlib
import math
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from neo4j import GraphDatabase

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from memory.config import settings
from memory.services.embedding_service import (
    EMBEDDING_DIMS,
    CHUNK_THRESHOLD,
    embed_new_content,
    get_query_embedding,
    _split_into_chunks,
    _count_tokens,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

EMBED_USER_ID = "test-user-embedding"


def _fake_embedding(text: str) -> list[float]:
    """
    Deterministic unit vector seeded from the text's SHA-256.
    First non-zero dimension is set to 1.0; all others 0.0.
    Two different texts → orthogonal vectors (cosine similarity = 0).
    Same text → identical vector (cosine similarity = 1).
    """
    h = int(hashlib.sha256(text.encode()).hexdigest(), 16)
    dim = h % EMBEDDING_DIMS
    vec = [0.0] * EMBEDDING_DIMS
    vec[dim] = 1.0
    return vec


def _make_openai_mock(texts_to_embeddings: dict | None = None):
    """
    Build a mock AsyncOpenAI client whose embeddings.create() returns
    deterministic vectors.  Accepts either a single string input or a list.
    """
    async def _create(input, **_kwargs):
        if isinstance(input, str):
            inputs = [input]
        else:
            inputs = input

        mock_response = MagicMock()
        mock_response.data = [
            MagicMock(embedding=(
                texts_to_embeddings.get(t) if texts_to_embeddings else _fake_embedding(t)
            ))
            for t in inputs
        ]
        return mock_response

    client = MagicMock()
    client.embeddings = MagicMock()
    client.embeddings.create = AsyncMock(side_effect=_create)
    return client


def _neo4j_fetch(cypher: str, **params) -> list[dict]:
    driver = GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_username, settings.neo4j_password),
    )
    try:
        with driver.session(database=settings.neo4j_database) as session:
            return session.run(cypher, **params).data()
    finally:
        driver.close()


def _write_test_conversation(conv_id: str, messages: list[dict]) -> None:
    """Directly insert a conversation + messages into Neo4j for embedding tests."""
    driver = GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_username, settings.neo4j_password),
    )
    try:
        with driver.session(database=settings.neo4j_database) as session:
            session.run(
                "MERGE (u:User {userId: $uid}) SET u.createdAt = $now, u.lastActiveAt = $now",
                uid=EMBED_USER_ID, now=datetime.now(timezone.utc),
            )
            session.run(
                """
                MERGE (c:Conversation {conversationId: $cid})
                ON CREATE SET c.userId=$uid, c.provider='chatgpt', c.model='gpt-4o',
                              c.startedAt=$now, c.endedAt=$now, c.messageCount=$mc,
                              c.totalTokens=0, c.segmentCount=0, c.isComplete=false,
                              c.createdAt=$now
                """,
                cid=conv_id, uid=EMBED_USER_ID,
                now=datetime.now(timezone.utc), mc=len(messages),
            )
            for i, msg in enumerate(messages):
                session.run(
                    """
                    MERGE (m:Message {messageId: $mid})
                    ON CREATE SET m.conversationId=$cid, m.userId=$uid, m.role=$role,
                                  m.content=$content, m.provider='chatgpt', m.model='gpt-4o',
                                  m.timestamp=$ts, m.tokenCount=$tc, m.messageIndex=$idx
                    WITH m
                    MATCH (c:Conversation {conversationId: $cid})
                    MERGE (c)-[:HAS_MESSAGE]->(m)
                    """,
                    mid=msg["messageId"], cid=conv_id, uid=EMBED_USER_ID,
                    role=msg["role"], content=msg["content"],
                    ts=datetime.now(timezone.utc), tc=msg["tokenCount"], idx=i,
                )
    finally:
        driver.close()


def _write_test_segment(conv_id: str, seg_id: str, content: str, token_count: int, message_ids: list[str]) -> None:
    driver = GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_username, settings.neo4j_password),
    )
    try:
        with driver.session(database=settings.neo4j_database) as session:
            session.run(
                """
                MERGE (s:Segment {conversationId: $cid, segmentIndex: 0})
                ON CREATE SET s.segmentId=$sid, s.userId=$uid, s.provider='chatgpt',
                              s.content=$content, s.tokenCount=$tc, s.messageCount=$mc,
                              s.startMessageIndex=0, s.endMessageIndex=$mc-1,
                              s.startTimestamp=$now, s.endTimestamp=$now
                WITH s
                MATCH (c:Conversation {conversationId: $cid})
                MERGE (c)-[:HAS_SEGMENT]->(s)
                """,
                cid=conv_id, sid=seg_id, uid=EMBED_USER_ID,
                content=content, tc=token_count, mc=len(message_ids),
                now=datetime.now(timezone.utc),
            )
            for order, mid in enumerate(message_ids):
                session.run(
                    """
                    MATCH (s:Segment {segmentId: $sid})
                    MATCH (m:Message {messageId: $mid})
                    MERGE (s)-[:CONTAINS_MESSAGE {order: $order}]->(m)
                    """,
                    sid=seg_id, mid=mid, order=order,
                )
    finally:
        driver.close()


@pytest.fixture(scope="module", autouse=True)
def cleanup_embedding_test_data():
    yield
    driver = GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_username, settings.neo4j_password),
    )
    try:
        with driver.session(database=settings.neo4j_database) as session:
            session.run(
                """
                MATCH (u:User {userId: $uid})
                OPTIONAL MATCH (u)-[:HAS_CONVERSATION]->(c:Conversation)
                OPTIONAL MATCH (c)-[:HAS_MESSAGE]->(m:Message)
                OPTIONAL MATCH (m)-[:HAS_CHUNK]->(ch:Chunk)
                OPTIONAL MATCH (c)-[:HAS_SEGMENT]->(s:Segment)
                DETACH DELETE u, c, m, ch, s
                """,
                uid=EMBED_USER_ID,
            )
    finally:
        driver.close()


# ── Token utilities ───────────────────────────────────────────────────────────

class TestTokenUtilities:
    def test_count_tokens_short_text(self):
        tokens = _count_tokens("Hello world")
        assert tokens > 0
        assert tokens < 10

    def test_count_tokens_empty_string(self):
        assert _count_tokens("") == 0

    def test_split_into_chunks_short_text(self):
        """Text under CHUNK_THRESHOLD produces exactly one chunk."""
        text = "This is a short sentence."
        chunks = _split_into_chunks(text)
        assert len(chunks) == 1
        assert text in chunks[0]

    def test_split_into_chunks_produces_overlap(self):
        """Long text is split into overlapping windows."""
        # ~600-token text (each "memory " ≈ 1 token in cl100k_base)
        word = "memory " * 600
        chunks = _split_into_chunks(word)
        assert len(chunks) >= 2
        # Each chunk should be <= CHUNK_SIZE tokens
        from memory.services.embedding_service import CHUNK_SIZE
        for chunk in chunks:
            assert _count_tokens(chunk) <= CHUNK_SIZE


# ── Embedding computation (mocked OpenAI) ────────────────────────────────────

class TestEmbeddingComputation:
    @pytest.mark.asyncio
    async def test_embed_new_content_noop_without_openai(self):
        """embed_new_content is a no-op when openai_client is None."""
        from neo4j import AsyncGraphDatabase
        async_driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_username, settings.neo4j_password),
        )
        try:
            # Should not raise; returns silently
            await embed_new_content(
                driver=async_driver,
                openai_client=None,
                redis_client=None,
                conversation_id="nonexistent-conv",
            )
        finally:
            await async_driver.close()

    @pytest.mark.asyncio
    async def test_segment_embedding_stored_in_neo4j(self):
        """After embed_new_content, Segment.embedding is populated."""
        conv_id = str(uuid.uuid4())
        seg_id  = str(uuid.uuid4())
        msg_ids = [str(uuid.uuid4()), str(uuid.uuid4())]

        messages = [
            {"messageId": msg_ids[0], "role": "user",
             "content": "What is quantum entanglement?", "tokenCount": 5},
            {"messageId": msg_ids[1], "role": "assistant",
             "content": "Quantum entanglement is a physical phenomenon.", "tokenCount": 8},
        ]
        _write_test_conversation(conv_id, messages)
        segment_content = "\n".join(
            f"{m['role'].upper()}: {m['content']}" for m in messages
        )
        _write_test_segment(conv_id, seg_id, segment_content, 13, msg_ids)

        from neo4j import AsyncGraphDatabase
        async_driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_username, settings.neo4j_password),
        )
        try:
            openai_mock = _make_openai_mock()
            await embed_new_content(
                driver=async_driver,
                openai_client=openai_mock,
                redis_client=None,
                conversation_id=conv_id,
            )
        finally:
            await async_driver.close()

        rows = _neo4j_fetch(
            "MATCH (s:Segment {segmentId: $sid}) RETURN s.embedding AS emb",
            sid=seg_id,
        )
        assert len(rows) == 1
        assert rows[0]["emb"] is not None
        assert len(rows[0]["emb"]) == EMBEDDING_DIMS

    @pytest.mark.asyncio
    async def test_short_message_embedding_stored_directly(self):
        """Messages with tokenCount ≤ CHUNK_THRESHOLD get embedding on Message node."""
        conv_id = str(uuid.uuid4())
        msg_id  = str(uuid.uuid4())
        messages = [
            {"messageId": msg_id, "role": "user",
             "content": "Tell me about black holes.", "tokenCount": 6},
        ]
        _write_test_conversation(conv_id, messages)

        from neo4j import AsyncGraphDatabase
        async_driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_username, settings.neo4j_password),
        )
        try:
            await embed_new_content(
                driver=async_driver,
                openai_client=_make_openai_mock(),
                redis_client=None,
                conversation_id=conv_id,
            )
        finally:
            await async_driver.close()

        rows = _neo4j_fetch(
            "MATCH (m:Message {messageId: $mid}) RETURN m.embedding AS emb",
            mid=msg_id,
        )
        assert rows[0]["emb"] is not None
        assert len(rows[0]["emb"]) == EMBEDDING_DIMS

    @pytest.mark.asyncio
    async def test_long_message_produces_chunk_nodes(self):
        """Messages with tokenCount > CHUNK_THRESHOLD produce Chunk nodes, not Message.embedding."""
        conv_id = str(uuid.uuid4())
        msg_id  = str(uuid.uuid4())

        # Build content that genuinely exceeds CHUNK_THRESHOLD tokens
        long_content = ("The history of computing spans many decades. " * 80).strip()
        actual_tokens = _count_tokens(long_content)
        # Ensure our test content actually exceeds the threshold
        assert actual_tokens > CHUNK_THRESHOLD, (
            f"Test setup error: content has {actual_tokens} tokens, "
            f"need > {CHUNK_THRESHOLD}"
        )

        messages = [
            {"messageId": msg_id, "role": "assistant",
             "content": long_content, "tokenCount": actual_tokens},
        ]
        _write_test_conversation(conv_id, messages)

        from neo4j import AsyncGraphDatabase
        async_driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_username, settings.neo4j_password),
        )
        try:
            await embed_new_content(
                driver=async_driver,
                openai_client=_make_openai_mock(),
                redis_client=None,
                conversation_id=conv_id,
            )
        finally:
            await async_driver.close()

        # Message.embedding should remain NULL (long messages use chunks)
        msg_rows = _neo4j_fetch(
            "MATCH (m:Message {messageId: $mid}) RETURN m.embedding AS emb",
            mid=msg_id,
        )
        assert msg_rows[0]["emb"] is None, "Long message should NOT have embedding on Message node"

        # Chunk nodes should exist with embeddings
        chunk_rows = _neo4j_fetch(
            """
            MATCH (m:Message {messageId: $mid})-[:HAS_CHUNK]->(ch:Chunk)
            RETURN count(ch) AS n,
                   count(ch.embedding) = count(ch) AS allEmbedded
            """,
            mid=msg_id,
        )
        assert chunk_rows[0]["n"] >= 1
        assert chunk_rows[0]["allEmbedded"] is True

    @pytest.mark.asyncio
    async def test_redis_cache_prevents_duplicate_openai_calls(self):
        """Second embed_new_content call for same content hits Redis and skips OpenAI."""
        import redis.asyncio as aioredis

        # Use a real in-memory Redis if available; otherwise skip
        try:
            redis_client = aioredis.from_url(
                settings.redis_url or "redis://localhost:6379",
                encoding="utf-8",
                decode_responses=True,
            )
            await redis_client.ping()
        except Exception:
            pytest.skip("Redis not available")

        conv_id = str(uuid.uuid4())
        msg_id  = str(uuid.uuid4())
        content = "Unique content for cache test " + conv_id
        messages = [
            {"messageId": msg_id, "role": "user", "content": content, "tokenCount": 10},
        ]
        _write_test_conversation(conv_id, messages)

        from neo4j import AsyncGraphDatabase
        async_driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_username, settings.neo4j_password),
        )
        openai_mock = _make_openai_mock()

        try:
            # First call: should hit OpenAI
            await embed_new_content(
                driver=async_driver,
                openai_client=openai_mock,
                redis_client=redis_client,
                conversation_id=conv_id,
            )
            first_call_count = openai_mock.embeddings.create.call_count

            # Reset Message.embedding so the second call re-embeds
            driver_sync = GraphDatabase.driver(
                settings.neo4j_uri,
                auth=(settings.neo4j_username, settings.neo4j_password),
            )
            with driver_sync.session(database=settings.neo4j_database) as sess:
                sess.run(
                    "MATCH (m:Message {messageId: $mid}) REMOVE m.embedding",
                    mid=msg_id,
                )
            driver_sync.close()

            # Second call: same content → cache hit → no OpenAI call
            await embed_new_content(
                driver=async_driver,
                openai_client=openai_mock,
                redis_client=redis_client,
                conversation_id=conv_id,
            )
            second_call_count = openai_mock.embeddings.create.call_count
        finally:
            await async_driver.close()
            await redis_client.aclose()

        assert second_call_count == first_call_count, (
            "Redis cache hit should have prevented a second OpenAI call"
        )

    @pytest.mark.asyncio
    async def test_get_query_embedding_returns_correct_dims(self):
        """get_query_embedding returns a vector of EMBEDDING_DIMS dimensions."""
        openai_mock = _make_openai_mock()
        result = await get_query_embedding(openai_mock, None, "test query")
        assert result is not None
        assert len(result) == EMBEDDING_DIMS

    @pytest.mark.asyncio
    async def test_get_query_embedding_returns_none_on_openai_error(self):
        """get_query_embedding returns None (never raises) when OpenAI fails."""
        client = MagicMock()
        client.embeddings = MagicMock()
        client.embeddings.create = AsyncMock(side_effect=Exception("API down"))
        result = await get_query_embedding(client, None, "test query")
        assert result is None

    @pytest.mark.asyncio
    async def test_embed_new_content_idempotent(self):
        """Calling embed_new_content twice does not re-embed already-embedded nodes."""
        conv_id = str(uuid.uuid4())
        msg_id  = str(uuid.uuid4())
        messages = [
            {"messageId": msg_id, "role": "user",
             "content": "Idempotency test content.", "tokenCount": 4},
        ]
        _write_test_conversation(conv_id, messages)

        from neo4j import AsyncGraphDatabase
        async_driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_username, settings.neo4j_password),
        )
        openai_mock = _make_openai_mock()
        try:
            await embed_new_content(
                driver=async_driver,
                openai_client=openai_mock,
                redis_client=None,
                conversation_id=conv_id,
            )
            first_count = openai_mock.embeddings.create.call_count

            # Second call: message already has embedding → no new API calls
            await embed_new_content(
                driver=async_driver,
                openai_client=openai_mock,
                redis_client=None,
                conversation_id=conv_id,
            )
            second_count = openai_mock.embeddings.create.call_count
        finally:
            await async_driver.close()

        assert second_count == first_count, (
            "Already-embedded nodes must not trigger new OpenAI calls"
        )


# ── Head+tail path ────────────────────────────────────────────────────────────

class TestHeadTailEmbedding:
    @pytest.mark.asyncio
    async def test_head_tail_called_for_oversized_segment(self):
        """
        Segment content exceeding MAX_EMBED_TOKENS triggers two OpenAI calls
        (head + tail) instead of one.
        """
        from memory.services.embedding_service import MAX_EMBED_TOKENS, _call_openai_embedding

        # Build text longer than MAX_EMBED_TOKENS tokens
        long_text = ("word " * (MAX_EMBED_TOKENS + 500)).strip()

        call_log = []

        async def _spy_create(input, **_):
            if isinstance(input, str):
                call_log.append(input)
                inputs = [input]
            else:
                call_log.extend(input)
                inputs = input
            mock_response = MagicMock()
            mock_response.data = [
                MagicMock(embedding=_fake_embedding(t)) for t in inputs
            ]
            return mock_response

        client = MagicMock()
        client.embeddings = MagicMock()
        client.embeddings.create = AsyncMock(side_effect=_spy_create)

        embedding = await _call_openai_embedding(client, long_text)

        assert embedding is not None
        assert len(embedding) == EMBEDDING_DIMS
        # head+tail passes both texts in one batched call (list of 2)
        assert client.embeddings.create.call_count == 1
        # Both head and tail were sent in that one call
        assert len(call_log) == 2

    @pytest.mark.asyncio
    async def test_head_tail_result_is_normalised(self):
        """Averaged head+tail vector has L2 norm ≈ 1.0."""
        from memory.services.embedding_service import MAX_EMBED_TOKENS, _head_tail_embedding

        long_text = ("word " * (MAX_EMBED_TOKENS + 200)).strip()
        openai_mock = _make_openai_mock()
        result = await _head_tail_embedding(openai_mock, long_text)

        assert result is not None
        norm = math.sqrt(sum(x * x for x in result))
        assert abs(norm - 1.0) < 1e-6, f"Expected unit norm, got {norm}"
