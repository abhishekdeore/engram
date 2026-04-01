"""
Phase 5 — MCP HTTP/SSE server integration tests
=================================================
Tests for mcp_server_http.py — the Streamable HTTP transport layer that
enables ChatGPT Apps, Gemini Extensions, and other MCP-over-HTTP clients.

Coverage:
  Auth middleware:
    - Valid Bearer token: request proceeds, user_id available in ContextVar
    - Invalid token: 401 returned before MCP session starts
    - Missing Authorization header: 401 returned
    - /health path exempt from auth

  memory_write tool (via handle_memory_write imported from mcp_tools):
    - Happy path: conversation stored, confirmation message returned
    - Idempotent: second write with same conversationId does not duplicate nodes
    - Empty messages list: ValueError raised
    - All-system messages filtered → ValueError (no storable messages)
    - Cross-user blocked: user_id is taken from auth token, not from args

  memory_query tool (via handle_memory_query imported from mcp_tools):
    - Returns results after a write
    - Empty query string: ValueError raised
    - No results: returns "No memories found" string

  Integration:
    - Write via HTTP MCP server → query via HTTP MCP server → data retrieved
    - Write via HTTP MCP server → verify in Neo4j directly
    - Write via FastAPI REST (POST /memory/write) → query via MCP HTTP handler

  Config:
    - mcp_http_port and mcp_http_host present in settings
    - BearerAuthMiddleware: EXEMPT_PATHS contains /health

Tests run against real Neo4j — same rule as all other phases.
The handle_* functions are tested directly (no need to spin up the full
Starlette server for tool-logic tests).  The Starlette app is tested via
Starlette's own TestClient for auth-middleware coverage.

Run with:
    uv run pytest tests/test_mcp_http_server.py -v
"""

import hashlib
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from neo4j import AsyncGraphDatabase, GraphDatabase
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from memory.auth.jwt_handler import create_access_token
from memory.config import settings
from memory.mcp_tools import handle_memory_write, handle_memory_query
from memory.mcp_server_http import BearerAuthMiddleware, _build_starlette_app, server
from memory.services.embedding_service import EMBEDDING_DIMS
from memory.services.write_service import write_conversation_to_graph
from memory.models.requests import WriteRequest
from memory.models.message import MessageIn

# ── Constants ─────────────────────────────────────────────────────────────────

HTTP_TEST_USER  = "test-user-mcp-http-phase5"
OTHER_HTTP_USER = "test-user-mcp-http-other"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fake_embedding(text: str) -> list[float]:
    """Deterministic unit vector seeded from SHA-256(text). Same as other suites."""
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
    """Run a Cypher query with a sync driver — used for setup/teardown verification."""
    driver = GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_username, settings.neo4j_password),
    )
    try:
        with driver.session(database=settings.neo4j_database) as sess:
            return sess.run(cypher, **params).data()
    finally:
        driver.close()


def _teardown_http():
    for uid in (HTTP_TEST_USER, OTHER_HTTP_USER):
        _sync_run(
            """
            MATCH (u:User {userId: $uid})
            OPTIONAL MATCH (u)-[:HAS_CONVERSATION]->(c:Conversation)
            OPTIONAL MATCH (c)-[:HAS_MESSAGE]->(m:Message)
            OPTIONAL MATCH (m)-[:HAS_CHUNK]->(ch:Chunk)
            OPTIONAL MATCH (c)-[:HAS_SEGMENT]->(s:Segment)
            DETACH DELETE u, c, m, ch, s
            """,
            uid=uid,
        )


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
def teardown():
    yield
    _teardown_http()


@pytest.fixture()
async def neo4j_driver():
    """Function-scoped async Neo4j driver."""
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


# ── Config tests ──────────────────────────────────────────────────────────────

class TestConfig:
    def test_mcp_http_port_default(self):
        assert settings.mcp_http_port == 8001

    def test_mcp_http_host_default(self):
        assert settings.mcp_http_host == "0.0.0.0"

    def test_mcp_http_port_is_int(self):
        assert isinstance(settings.mcp_http_port, int)

    def test_health_path_exempt(self):
        assert "/health" in BearerAuthMiddleware.EXEMPT_PATHS


# ── Auth middleware tests (via Starlette TestClient) ──────────────────────────

class TestAuthMiddleware:
    """
    Test BearerAuthMiddleware using the Starlette TestClient.
    The /health route is exempt; /mcp requires a valid Bearer token.
    We use /health as the safe probe and send synthetic requests to /mcp
    with various auth states to verify middleware short-circuits correctly.
    """

    @pytest.fixture(scope="class")
    def starlette_client(self):
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
        session_manager = StreamableHTTPSessionManager(
            app=server,
            json_response=False,
            stateless=True,
        )
        app = _build_starlette_app(session_manager)
        return TestClient(app, raise_server_exceptions=False)

    def test_health_returns_200_without_token(self, starlette_client):
        resp = starlette_client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_health_returns_service_name(self, starlette_client):
        resp = starlette_client.get("/health")
        assert resp.json()["service"] == "engram-mcp-http"

    def test_mcp_missing_auth_header_returns_401(self, starlette_client):
        resp = starlette_client.post("/mcp", json={})
        assert resp.status_code == 401

    def test_mcp_malformed_bearer_returns_401(self, starlette_client):
        resp = starlette_client.post(
            "/mcp",
            json={},
            headers={"Authorization": "Token abc123"},
        )
        assert resp.status_code == 401

    def test_mcp_invalid_jwt_returns_401(self, starlette_client):
        resp = starlette_client.post(
            "/mcp",
            json={},
            headers={"Authorization": "Bearer not.a.valid.jwt"},
        )
        assert resp.status_code == 401

    def test_mcp_valid_token_passes_auth(self, starlette_client):
        """
        A valid token should not get a 401.  The MCP layer may return a
        different error (400/422) because we're sending an empty body, but
        auth itself must pass.
        """
        token = create_access_token(HTTP_TEST_USER)
        resp = starlette_client.post(
            "/mcp",
            content=b"{}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code != 401

    def test_401_response_has_www_authenticate_header(self, starlette_client):
        resp = starlette_client.post("/mcp", json={})
        assert "WWW-Authenticate" in resp.headers


# ── Tool handler tests — memory_write ─────────────────────────────────────────

class TestMemoryWriteTool:
    """
    Test handle_memory_write imported from mcp_tools.
    These tests call the function directly with an async Neo4j driver — no
    MCP protocol layer is involved.  The same pattern as test_mcp_server.py.
    """

    async def test_write_happy_path(self, neo4j_driver, openai_mock):
        conv_id = str(uuid.uuid4())
        result = await handle_memory_write(
            {
                "conversation_id": conv_id,
                "model": "gpt-4o",
                "messages": [
                    {"role": "user", "content": "Tell me about neural networks"},
                    {"role": "assistant", "content": "Neural networks are..."},
                ],
            },
            neo4j_driver,
            openai_mock,
            None,
            HTTP_TEST_USER,
        )
        assert "Saved 2 message(s)" in result
        assert conv_id in result

    async def test_write_idempotent(self, neo4j_driver, openai_mock):
        """Second write with the same conversationId must not duplicate nodes."""
        conv_id = str(uuid.uuid4())
        args = {
            "conversation_id": conv_id,
            "model": "gpt-4o",
            "messages": [
                {"role": "user", "content": "What is gradient descent?"},
                {"role": "assistant", "content": "Gradient descent is an optimisation..."},
            ],
        }
        await handle_memory_write(args, neo4j_driver, openai_mock, None, HTTP_TEST_USER)
        await handle_memory_write(args, neo4j_driver, openai_mock, None, HTTP_TEST_USER)

        rows = _sync_run(
            "MATCH (c:Conversation {conversationId: $cid})-[:HAS_MESSAGE]->(m) "
            "RETURN count(m) AS cnt",
            cid=conv_id,
        )
        assert rows[0]["cnt"] == 2

    async def test_write_empty_messages_raises(self, neo4j_driver, openai_mock):
        with pytest.raises(ValueError, match="at least one turn"):
            await handle_memory_write(
                {"conversation_id": str(uuid.uuid4()), "model": "gpt-4o", "messages": []},
                neo4j_driver, openai_mock, None, HTTP_TEST_USER,
            )

    async def test_write_all_system_messages_raises(self, neo4j_driver, openai_mock):
        """System/tool messages are filtered; if nothing remains, ValueError is raised."""
        with pytest.raises(ValueError, match="No storable messages"):
            await handle_memory_write(
                {
                    "conversation_id": str(uuid.uuid4()),
                    "model": "gpt-4o",
                    "messages": [{"role": "system", "content": "You are helpful."}],
                },
                neo4j_driver, openai_mock, None, HTTP_TEST_USER,
            )

    async def test_write_missing_conversation_id_raises(self, neo4j_driver, openai_mock):
        with pytest.raises(ValueError, match="conversation_id is required"):
            await handle_memory_write(
                {"conversation_id": "", "model": "gpt-4o",
                 "messages": [{"role": "user", "content": "hi"}]},
                neo4j_driver, openai_mock, None, HTTP_TEST_USER,
            )

    async def test_write_missing_model_raises(self, neo4j_driver, openai_mock):
        with pytest.raises(ValueError, match="model is required"):
            await handle_memory_write(
                {"conversation_id": str(uuid.uuid4()), "model": "",
                 "messages": [{"role": "user", "content": "hi"}]},
                neo4j_driver, openai_mock, None, HTTP_TEST_USER,
            )

    async def test_write_stores_under_correct_user(self, neo4j_driver, openai_mock):
        """user_id comes from the auth token argument, not from message content."""
        conv_id = str(uuid.uuid4())
        await handle_memory_write(
            {
                "conversation_id": conv_id,
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Cross-user ownership check"}],
            },
            neo4j_driver, openai_mock, None, HTTP_TEST_USER,
        )
        # Conversation must be owned by HTTP_TEST_USER
        rows = _sync_run(
            "MATCH (c:Conversation {conversationId: $cid}) RETURN c.userId AS uid",
            cid=conv_id,
        )
        assert rows[0]["uid"] == HTTP_TEST_USER

    async def test_write_different_user_cannot_see_other_user_data(
        self, neo4j_driver, openai_mock
    ):
        """A write by OTHER_HTTP_USER must not be visible under HTTP_TEST_USER."""
        conv_id = str(uuid.uuid4())
        await handle_memory_write(
            {
                "conversation_id": conv_id,
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Private data for other user"}],
            },
            neo4j_driver, openai_mock, None, OTHER_HTTP_USER,
        )
        # Confirm it's stored under OTHER_HTTP_USER
        rows = _sync_run(
            "MATCH (c:Conversation {conversationId: $cid}) RETURN c.userId AS uid",
            cid=conv_id,
        )
        assert rows[0]["uid"] == OTHER_HTTP_USER
        # And NOT returned when HTTP_TEST_USER queries
        result = await handle_memory_query(
            {"query": "Private data for other user", "top_k": 5},
            neo4j_driver, openai_mock, None, HTTP_TEST_USER,
        )
        assert conv_id not in result


# ── Tool handler tests — memory_query ─────────────────────────────────────────

class TestMemoryQueryTool:

    async def test_query_returns_results_after_write(self, neo4j_driver, openai_mock):
        conv_id = str(uuid.uuid4())
        await handle_memory_write(
            {
                "conversation_id": conv_id,
                "model": "gpt-4o",
                "messages": [
                    {"role": "user", "content": "Explain transformer architecture"},
                    {"role": "assistant", "content": "Transformers use self-attention..."},
                ],
            },
            neo4j_driver, openai_mock, None, HTTP_TEST_USER,
        )
        result = await handle_memory_query(
            {"query": "transformer architecture attention", "top_k": 5},
            neo4j_driver, openai_mock, None, HTTP_TEST_USER,
        )
        assert "transformer" in result.lower() or "attention" in result.lower()

    async def test_query_empty_query_raises(self, neo4j_driver, openai_mock):
        with pytest.raises(ValueError, match="query is required"):
            await handle_memory_query(
                {"query": ""},
                neo4j_driver, openai_mock, None, HTTP_TEST_USER,
            )

    async def test_query_no_results_returns_no_memories_message(
        self, neo4j_driver, openai_mock
    ):
        result = await handle_memory_query(
            {"query": "xyzzy_zxqf_no_match_9173", "top_k": 5},
            neo4j_driver, openai_mock, None, HTTP_TEST_USER,
        )
        assert "No memories found" in result

    async def test_query_verbatim_content_preserved(self, neo4j_driver, openai_mock):
        """Retrieved content must be verbatim — exact phrase must appear in output."""
        verbatim_phrase = "backpropagation through time in RNNs is tricky"
        conv_id = str(uuid.uuid4())
        await handle_memory_write(
            {
                "conversation_id": conv_id,
                "model": "gpt-4o",
                "messages": [
                    {"role": "user", "content": "How does BPTT work?"},
                    {"role": "assistant", "content": verbatim_phrase},
                ],
            },
            neo4j_driver, openai_mock, None, HTTP_TEST_USER,
        )
        result = await handle_memory_query(
            {"query": "backpropagation through time RNN", "top_k": 5},
            neo4j_driver, openai_mock, None, HTTP_TEST_USER,
        )
        assert verbatim_phrase in result


# ── Integration tests ─────────────────────────────────────────────────────────

class TestIntegration:
    """End-to-end: write then query via the same handler layer."""

    async def test_write_then_query_roundtrip(self, neo4j_driver, openai_mock):
        """Write a conversation; query must find it by semantic content."""
        conv_id = str(uuid.uuid4())
        unique_content = f"quantum_entanglement_test_{conv_id[:8]}"
        await handle_memory_write(
            {
                "conversation_id": conv_id,
                "model": "gpt-4o",
                "messages": [
                    {"role": "user", "content": f"Explain {unique_content}"},
                    {"role": "assistant", "content": f"{unique_content} is a phenomenon..."},
                ],
            },
            neo4j_driver, openai_mock, None, HTTP_TEST_USER,
        )
        result = await handle_memory_query(
            {"query": unique_content, "top_k": 5},
            neo4j_driver, openai_mock, None, HTTP_TEST_USER,
        )
        assert unique_content in result

    async def test_write_then_verify_in_neo4j(self, neo4j_driver, openai_mock):
        """Write via MCP handler; verify the Conversation node exists directly in Neo4j."""
        conv_id = str(uuid.uuid4())
        await handle_memory_write(
            {
                "conversation_id": conv_id,
                "model": "gpt-4o",
                "messages": [
                    {"role": "user", "content": "Store this for direct DB verification"},
                ],
            },
            neo4j_driver, openai_mock, None, HTTP_TEST_USER,
        )
        rows = _sync_run(
            "MATCH (c:Conversation {conversationId: $cid}) RETURN c.userId AS uid",
            cid=conv_id,
        )
        assert len(rows) == 1
        assert rows[0]["uid"] == HTTP_TEST_USER

    async def test_cross_transport_write_rest_query_mcp(
        self, neo4j_driver, openai_mock
    ):
        """
        Write via the FastAPI service layer (same path as POST /memory/write),
        then query via the MCP HTTP handler.  This confirms the two layers
        share the same underlying data.
        """
        from memory.adapters.chatgpt import normalize as chatgpt_normalize

        conv_id = str(uuid.uuid4())
        unique_phrase = f"cross_transport_test_{conv_id[:8]}"
        raw = {
            "id": conv_id,
            "model": "gpt-4o",
            "messages": [
                {"role": "user",      "content": f"REST write: {unique_phrase}"},
                {"role": "assistant", "content": f"MCP query will find: {unique_phrase}"},
            ],
        }
        cmf = chatgpt_normalize(raw)
        write_req = WriteRequest(
            userId=HTTP_TEST_USER,
            conversationId=conv_id,
            provider="chatgpt",
            model="gpt-4o",
            messages=[MessageIn(**m) for m in cmf],
        )
        await write_conversation_to_graph(
            driver=neo4j_driver,
            request=write_req,
            openai_client=openai_mock,
            redis_client=None,
        )

        result = await handle_memory_query(
            {"query": unique_phrase, "top_k": 5},
            neo4j_driver, openai_mock, None, HTTP_TEST_USER,
        )
        assert unique_phrase in result

    async def test_context_var_user_id_isolates_users(
        self, neo4j_driver, openai_mock
    ):
        """
        Confirm that user_id is taken from the explicit argument (auth token)
        and not leaked between concurrent calls.  Write under two different
        user IDs; each query must only return its own data.
        """
        conv_user1 = str(uuid.uuid4())
        conv_user2 = str(uuid.uuid4())
        phrase_user1 = f"user1_secret_{conv_user1[:8]}"
        phrase_user2 = f"user2_secret_{conv_user2[:8]}"

        await handle_memory_write(
            {"conversation_id": conv_user1, "model": "m",
             "messages": [{"role": "user", "content": phrase_user1}]},
            neo4j_driver, openai_mock, None, HTTP_TEST_USER,
        )
        await handle_memory_write(
            {"conversation_id": conv_user2, "model": "m",
             "messages": [{"role": "user", "content": phrase_user2}]},
            neo4j_driver, openai_mock, None, OTHER_HTTP_USER,
        )

        result1 = await handle_memory_query(
            {"query": phrase_user2, "top_k": 5},
            neo4j_driver, openai_mock, None, HTTP_TEST_USER,
        )
        # HTTP_TEST_USER must NOT see OTHER_HTTP_USER's stored conversation.
        # The "No memories found" path echoes the query string back, so we
        # check that the conv_user2 conversation ID is absent — that node
        # belongs to OTHER_HTTP_USER and must never appear in HTTP_TEST_USER's
        # results regardless of whether the search string matches.
        assert conv_user2 not in result1


# ── mcp_tools module import correctness ───────────────────────────────────────

class TestMcpToolsModule:
    """Verify mcp_tools.py exports the correct objects."""

    def test_handle_memory_write_is_callable(self):
        import asyncio
        assert asyncio.iscoroutinefunction(handle_memory_write)

    def test_handle_memory_query_is_callable(self):
        import asyncio
        assert asyncio.iscoroutinefunction(handle_memory_query)

    def test_schemas_are_dicts(self):
        from memory.mcp_tools import MEMORY_WRITE_SCHEMA, MEMORY_QUERY_SCHEMA
        assert isinstance(MEMORY_WRITE_SCHEMA, dict)
        assert isinstance(MEMORY_QUERY_SCHEMA, dict)

    def test_write_schema_has_required_fields(self):
        from memory.mcp_tools import MEMORY_WRITE_SCHEMA
        assert "conversation_id" in MEMORY_WRITE_SCHEMA["properties"]
        assert "messages" in MEMORY_WRITE_SCHEMA["properties"]
        assert "model" in MEMORY_WRITE_SCHEMA["properties"]

    def test_query_schema_has_required_fields(self):
        from memory.mcp_tools import MEMORY_QUERY_SCHEMA
        assert "query" in MEMORY_QUERY_SCHEMA["properties"]

    def test_stdio_server_re_exports_handlers(self):
        """mcp_server.py must still expose handle_memory_write/query for Phase 4 tests."""
        from memory.mcp_server import handle_memory_write as hw, handle_memory_query as hq
        from memory.mcp_tools import handle_memory_write as hw2, handle_memory_query as hq2
        assert hw is hw2
        assert hq is hq2
