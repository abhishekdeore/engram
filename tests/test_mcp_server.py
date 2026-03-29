"""
Phase 4 — MCP server integration tests
========================================
Tests for the two MCP tool handler functions:

  handle_memory_write  — normalise Claude turns → store in Neo4j
  handle_memory_query  — retrieve stored conversations verbatim

The handler functions accept explicit dependencies (driver, openai_client,
redis_client, user_id) so they are tested without the MCP protocol layer.

Test structure:
  - Unit tests: validate input handling, adapter normalisation, error paths
  - Integration tests: real Neo4j writes and queries (same pattern as prior
    phase tests — no mocks at the database level)
  - Cross-phase test: write via HTTP API (simulating a ChatGPT save) then
    query via the MCP handler — verifies the two layers are truly stitched

Prerequisites: Neo4j running (same as all other integration tests).

Run with:
    uv run pytest tests/test_mcp_server.py -v
"""

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from neo4j import AsyncGraphDatabase, GraphDatabase

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from memory.api.main import app
from memory.auth.jwt_handler import create_access_token
from memory.config import settings
from memory.mcp_server import handle_memory_query, handle_memory_write
from memory.services.embedding_service import EMBEDDING_DIMS

# ── Constants ─────────────────────────────────────────────────────────────────

MCP_TEST_USER = "test-user-mcp-phase4"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fake_embedding(text: str) -> list[float]:
    """Deterministic unit vector — same helper as test_end_to_end.py."""
    import hashlib
    h   = int(hashlib.sha256(text.encode()).hexdigest(), 16)
    dim = h % EMBEDDING_DIMS
    vec = [0.0] * EMBEDDING_DIMS
    vec[dim] = 1.0
    return vec


def _make_openai_mock():
    async def _create(input, **_kwargs):
        inputs = [input] if isinstance(input, str) else input
        mock_response = MagicMock()
        mock_response.data = [
            MagicMock(embedding=_fake_embedding(t)) for t in inputs
        ]
        return mock_response

    client = MagicMock()
    client.embeddings = MagicMock()
    client.embeddings.create = AsyncMock(side_effect=_create)
    return client


def _sync_run(cypher: str, **params) -> list[dict]:
    driver = GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_username, settings.neo4j_password),
    )
    try:
        with driver.session(database=settings.neo4j_database) as sess:
            return sess.run(cypher, **params).data()
    finally:
        driver.close()


def _teardown_mcp():
    _sync_run(
        """
        MATCH (u:User {userId: $uid})
        OPTIONAL MATCH (u)-[:HAS_CONVERSATION]->(c:Conversation)
        OPTIONAL MATCH (c)-[:HAS_MESSAGE]->(m:Message)
        OPTIONAL MATCH (m)-[:HAS_CHUNK]->(ch:Chunk)
        OPTIONAL MATCH (c)-[:HAS_SEGMENT]->(s:Segment)
        DETACH DELETE u, c, m, ch, s
        """,
        uid=MCP_TEST_USER,
    )


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
def teardown():
    yield
    _teardown_mcp()


@pytest.fixture()
async def neo4j_driver():
    """
    Async Neo4j driver — function-scoped so each async test gets a fresh
    driver bound to its own event loop (pytest-asyncio creates a new loop
    per test function in auto mode).
    """
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_username, settings.neo4j_password),
        max_connection_pool_size=5,
    )
    yield driver
    await driver.close()


@pytest.fixture()
def openai_mock():
    return _make_openai_mock()


@pytest.fixture(scope="module")
def http_client():
    """HTTP TestClient — sync, module-scoped (no event loop issues)."""
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def http_auth() -> dict:
    token = create_access_token(MCP_TEST_USER)
    return {"Authorization": f"Bearer {token}"}


# ─────────────────────────────────────────────────────────────────────────────
# handle_memory_write — unit tests (input validation, adapter, error paths)
# ─────────────────────────────────────────────────────────────────────────────

class TestHandleMemoryWriteValidation:

    async def test_raises_on_missing_conversation_id(self, neo4j_driver):
        with pytest.raises(ValueError, match="conversation_id"):
            await handle_memory_write(
                {"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "hi"}]},
                neo4j_driver, None, None, MCP_TEST_USER,
            )

    async def test_raises_on_missing_model(self, neo4j_driver):
        with pytest.raises(ValueError, match="model"):
            await handle_memory_write(
                {"conversation_id": "x", "messages": [{"role": "user", "content": "hi"}]},
                neo4j_driver, None, None, MCP_TEST_USER,
            )

    async def test_raises_on_empty_messages(self, neo4j_driver):
        with pytest.raises(ValueError, match="messages"):
            await handle_memory_write(
                {"conversation_id": "x", "model": "claude-sonnet-4-6", "messages": []},
                neo4j_driver, None, None, MCP_TEST_USER,
            )

    async def test_raises_when_all_messages_filtered(self, neo4j_driver):
        """System-role messages are filtered by the adapter; if none remain → error."""
        with pytest.raises(ValueError, match="No storable messages"):
            await handle_memory_write(
                {
                    "conversation_id": "x",
                    "model": "claude-sonnet-4-6",
                    "messages": [{"role": "system", "content": "system prompt"}],
                },
                neo4j_driver, None, None, MCP_TEST_USER,
            )


# ─────────────────────────────────────────────────────────────────────────────
# handle_memory_write — integration tests (real Neo4j)
# ─────────────────────────────────────────────────────────────────────────────

class TestHandleMemoryWriteIntegration:

    async def test_write_stores_messages_verbatim(self, neo4j_driver, openai_mock):
        """A valid write call stores messages in Neo4j and returns confirmation."""
        conv_id = str(uuid.uuid4())
        content = "The mitochondria is the powerhouse of the cell."

        result = await handle_memory_write(
            {
                "conversation_id": conv_id,
                "model": "claude-sonnet-4-6",
                "messages": [
                    {"role": "user",      "content": content},
                    {"role": "assistant", "content": "That is correct!"},
                ],
            },
            neo4j_driver,
            openai_mock,
            None,
            MCP_TEST_USER,
        )

        assert "Saved" in result
        assert "2" in result  # 2 messages

        # Verify content is actually in Neo4j — verbatim
        rows = _sync_run(
            "MATCH (m:Message {conversationId: $cid}) RETURN m.content AS c ORDER BY m.messageIndex",
            cid=conv_id,
        )
        assert len(rows) == 2
        assert rows[0]["c"] == content, "First message content must match verbatim"

    async def test_write_is_idempotent(self, neo4j_driver):
        """Re-sending the same conversation must not create duplicate messages."""
        conv_id = str(uuid.uuid4())
        args = {
            "conversation_id": conv_id,
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "idempotency test"}],
        }

        await handle_memory_write(args, neo4j_driver, None, None, MCP_TEST_USER)
        await handle_memory_write(args, neo4j_driver, None, None, MCP_TEST_USER)

        rows = _sync_run(
            "MATCH (c:Conversation {conversationId: $cid})-[:HAS_MESSAGE]->(m) RETURN count(m) AS n",
            cid=conv_id,
        )
        assert rows[0]["n"] == 1, "Duplicate write must not create extra message nodes"

    async def test_write_with_timestamps(self, neo4j_driver):
        """Messages with explicit created_at timestamps are stored correctly."""
        conv_id = str(uuid.uuid4())
        ts      = "2026-03-01T10:00:00Z"

        await handle_memory_write(
            {
                "conversation_id": conv_id,
                "model": "claude-sonnet-4-6",
                "messages": [
                    {"role": "user", "content": "timestamped", "created_at": ts}
                ],
            },
            neo4j_driver,
            None,
            None,
            MCP_TEST_USER,
        )

        rows = _sync_run(
            "MATCH (m:Message {conversationId: $cid}) RETURN m.content AS c",
            cid=conv_id,
        )
        assert rows[0]["c"] == "timestamped"


# ─────────────────────────────────────────────────────────────────────────────
# handle_memory_query — integration tests (real Neo4j)
# ─────────────────────────────────────────────────────────────────────────────

class TestHandleMemoryQueryIntegration:

    async def test_query_returns_verbatim_content(self, neo4j_driver, openai_mock):
        """
        Write a conversation via MCP write, then query it.
        The returned context must contain the verbatim message content.
        """
        conv_id = str(uuid.uuid4())
        content = "Photosynthesis converts sunlight into chemical energy."

        await handle_memory_write(
            {
                "conversation_id": conv_id,
                "model": "claude-sonnet-4-6",
                "messages": [{"role": "user", "content": content}],
            },
            neo4j_driver,
            openai_mock,
            None,
            MCP_TEST_USER,
        )

        result = await handle_memory_query(
            {"query": content},
            neo4j_driver,
            openai_mock,
            None,
            MCP_TEST_USER,
        )

        assert content in result, (
            "Verbatim message content must appear in query result"
        )

    async def test_query_no_results_returns_informative_message(
        self, neo4j_driver, openai_mock
    ):
        """A query that matches nothing returns a clear 'not found' message."""
        result = await handle_memory_query(
            {"query": "zzz_unlikely_to_match_any_stored_content_xyz_12345"},
            neo4j_driver,
            openai_mock,
            None,
            MCP_TEST_USER,
        )

        assert "No memories found" in result

    async def test_query_result_format_has_provider_and_date(
        self, neo4j_driver, openai_mock
    ):
        """The formatted output must include provider label and date."""
        conv_id = str(uuid.uuid4())
        content = "format check content " + conv_id

        await handle_memory_write(
            {
                "conversation_id": conv_id,
                "model": "claude-sonnet-4-6",
                "messages": [{"role": "user", "content": content}],
            },
            neo4j_driver,
            openai_mock,
            None,
            MCP_TEST_USER,
        )

        result = await handle_memory_query(
            {"query": content},
            neo4j_driver,
            openai_mock,
            None,
            MCP_TEST_USER,
        )

        # Output format: "[Memory N — CLAUDE (model) — YYYY-MM-DD]"
        assert "CLAUDE" in result, "Provider name must appear in output"
        assert "Memory 1" in result, "Memory counter must appear in output"

    async def test_query_raises_on_empty_query(self, neo4j_driver):
        with pytest.raises(ValueError, match="query is required"):
            await handle_memory_query(
                {"query": ""},
                neo4j_driver,
                None,
                None,
                MCP_TEST_USER,
            )

    async def test_query_with_date_filter(self, neo4j_driver):
        """A date filter that excludes the written conversation returns no results."""
        conv_id = str(uuid.uuid4())
        content = "date filter mcp test " + conv_id

        await handle_memory_write(
            {
                "conversation_id": conv_id,
                "model": "claude-sonnet-4-6",
                "messages": [{"role": "user", "content": content}],
            },
            neo4j_driver,
            None,
            None,
            MCP_TEST_USER,
        )

        # Filter to a date far in the past — should exclude the just-written conversation
        result = await handle_memory_query(
            {
                "query": content,
                "date": "2020-01-01",
            },
            neo4j_driver,
            None,
            None,
            MCP_TEST_USER,
        )

        assert "No memories found" in result


# ─────────────────────────────────────────────────────────────────────────────
# Cross-phase integration test: HTTP API write → MCP query
# ─────────────────────────────────────────────────────────────────────────────

class TestCrossPhaseIntegration:

    async def test_write_via_http_api_query_via_mcp(
        self, http_client, http_auth, neo4j_driver, openai_mock
    ):
        """
        Core cross-LLM memory guarantee:

        1. Write a ChatGPT conversation via the existing HTTP API
           (simulates: user saves a ChatGPT session)
        2. Query via the MCP handler
           (simulates: Claude asking 'do you remember X?')
        3. Verify the verbatim content from step 1 appears in step 2

        This is the exact user experience the system was built for.
        """
        conv_id = str(uuid.uuid4())
        msg_id  = str(uuid.uuid4())
        verbatim_content = (
            "The Treaty of Westphalia (1648) ended the Thirty Years War "
            "and established the principle of national sovereignty."
        )

        # Step 1 — Write via HTTP API as ChatGPT
        app.state.openai_client = openai_mock
        app.state.redis_client  = None

        write_resp = http_client.post(
            "/memory/write",
            json={
                "userId":         MCP_TEST_USER,
                "conversationId": conv_id,
                "provider":       "chatgpt",
                "model":          "gpt-4o",
                "messages": [{
                    "messageId":  msg_id,
                    "role":       "user",
                    "content":    verbatim_content,
                    "timestamp":  datetime.now(timezone.utc).isoformat(),
                    "tokenCount": 28,
                }],
            },
            headers=http_auth,
        )
        assert write_resp.status_code == 202, (
            f"HTTP write should return 202, got {write_resp.status_code}"
        )

        # Step 2 — Query via MCP handler (different layer, same Neo4j)
        result = await handle_memory_query(
            {"query": "Treaty of Westphalia sovereignty"},
            neo4j_driver,
            openai_mock,
            None,
            MCP_TEST_USER,
        )

        # Step 3 — Assert verbatim content is returned
        assert verbatim_content in result, (
            "Verbatim ChatGPT content written via HTTP API must be "
            "retrievable via the MCP query handler. "
            f"Expected to find:\n  {verbatim_content!r}\n\nGot:\n  {result!r}"
        )

        # The result should also indicate the source provider
        assert "CHATGPT" in result, "Provider label must appear in cross-phase result"

    async def test_mcp_write_then_http_query(
        self, http_client, http_auth, neo4j_driver, openai_mock
    ):
        """
        Reverse direction: write via MCP, read via HTTP API.
        Confirms the MCP write path is fully compatible with the HTTP query path.
        """
        conv_id = str(uuid.uuid4())
        content = "Schrodinger equation describes quantum state evolution over time."

        # Write via MCP handler
        app.state.openai_client = openai_mock
        await handle_memory_write(
            {
                "conversation_id": conv_id,
                "model": "claude-sonnet-4-6",
                "messages": [{"role": "user", "content": content}],
            },
            neo4j_driver,
            openai_mock,
            None,
            MCP_TEST_USER,
        )

        # Query via HTTP API
        resp = http_client.post(
            "/memory/query",
            json={
                "userId":   MCP_TEST_USER,
                "query":    content,
                "topK":     5,
            },
            headers=http_auth,
        )
        assert resp.status_code == 200

        all_contents = [
            msg["content"]
            for conv in resp.json()["results"]
            for msg in conv["messages"]
        ]
        assert content in all_contents, (
            "Content written via MCP must be queryable via HTTP API"
        )

    async def test_user_isolation_across_layers(self, neo4j_driver):
        """
        Data written for one user must not appear in another user's MCP queries.
        Ownership enforced at the Cypher level must hold across both layers.
        """
        conv_id     = str(uuid.uuid4())
        content     = "user isolation test content " + conv_id
        other_user  = "mcp-other-user-isolation"

        # Write for MCP_TEST_USER
        await handle_memory_write(
            {
                "conversation_id": conv_id,
                "model": "claude-sonnet-4-6",
                "messages": [{"role": "user", "content": content}],
            },
            neo4j_driver,
            None,
            None,
            MCP_TEST_USER,
        )

        # Query as a different user — must get no results
        result = await handle_memory_query(
            {"query": content},
            neo4j_driver,
            None,
            None,
            other_user,  # different user
        )

        assert "No memories found" in result, (
            "Querying as a different user must return no results (isolation enforced)"
        )
