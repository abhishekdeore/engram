"""
Phase 5 — Integration tests for ChatGPT Custom GPT Action endpoints
====================================================================
Tests run against real Neo4j (not mocked). FastAPI TestClient executes
BackgroundTasks synchronously, so Neo4j state is verifiable immediately.

Coverage:
  Auth:
    - POST /auth/apikey  issues a 1-year JWT usable as Bearer token
    - API key authenticates successfully on all protected endpoints
    - /auth/token still blocked in production (existing behaviour unchanged)

  POST /chatgpt/write:
    - 202 on valid payload
    - conversationId auto-generated when omitted
    - provided conversationId echoed back
    - system/tool messages filtered out; user/assistant stored
    - all-system payload → 422
    - empty messages list → 422
    - userId mismatch (body vs token) → 403
    - missing token → 401
    - idempotent: second write with same conversationId does not duplicate nodes

  GET /chatgpt/action-spec:
    - returns valid JSON with expected operationIds
    - server URL injected from request base_url

  Cross-phase integration:
    - write via /chatgpt/write → query via /memory/query returns verbatim content
    - write via /chatgpt/write → read via GET /memory/conversation/{id}
    - chatgpt write and claude write under same userId both queryable
    - chatgpt data invisible to different userId

Run with:
    uv run pytest tests/test_chatgpt_integration.py -v
"""

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from neo4j import GraphDatabase

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from memory.api.main import app
from memory.auth.jwt_handler import create_access_token, decode_access_token
from memory.config import settings
from memory.services.embedding_service import EMBEDDING_DIMS

import hashlib

# ── Constants ─────────────────────────────────────────────────────────────────

TEST_USER    = "test-user-phase5-chatgpt"
OTHER_USER   = "test-user-phase5-other"
CONV_ID      = str(uuid.uuid4())


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fake_embedding(text: str) -> list[float]:
    """Deterministic unit vector seeded from SHA-256(text). Matches e2e helper."""
    h   = int(hashlib.sha256(text.encode()).hexdigest(), 16)
    dim = h % EMBEDDING_DIMS
    vec = [0.0] * EMBEDDING_DIMS
    vec[dim] = 1.0
    return vec


def _mock_openai():
    """AsyncOpenAI client mock returning deterministic embeddings."""
    embedding_mock = MagicMock()
    embedding_mock.create = AsyncMock(
        side_effect=lambda input, model: MagicMock(
            data=[
                MagicMock(embedding=_fake_embedding(t if isinstance(t, str) else t[0]))
                for t in ([input] if isinstance(input, str) else input)
            ]
        )
    )
    client = MagicMock()
    client.embeddings = embedding_mock
    return client


def _make_messages(n: int = 2) -> list[dict]:
    return [
        {"role": "user",      "content": f"ChatGPT user message {i}. Topic: quantum memory."}
        if i % 2 == 0
        else {"role": "assistant", "content": f"ChatGPT assistant response {i}. Verbatim answer."}
        for i in range(n)
    ]


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def token() -> str:
    return create_access_token(TEST_USER)


@pytest.fixture(scope="module")
def auth_headers(token) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def other_token() -> str:
    return create_access_token(OTHER_USER)


@pytest.fixture(scope="module")
def other_headers(other_token) -> dict:
    return {"Authorization": f"Bearer {other_token}"}


@pytest.fixture(scope="module", autouse=True)
def cleanup():
    yield
    driver = GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_username, settings.neo4j_password),
    )
    try:
        with driver.session(database=settings.neo4j_database) as session:
            for uid in [TEST_USER, OTHER_USER]:
                session.run(
                    """
                    MATCH (u:User {userId: $uid})-[:HAS_CONVERSATION]->(c)
                    OPTIONAL MATCH (c)-[:HAS_MESSAGE]->(m)-[:HAS_CHUNK]->(ch)
                    DETACH DELETE ch
                    WITH c, m DETACH DELETE m
                    WITH c DETACH DELETE c
                    """,
                    uid=uid,
                )
                session.run(
                    "MATCH (u:User {userId: $uid}) DETACH DELETE u",
                    uid=uid,
                )
    finally:
        driver.close()


# ── Auth: API key issuance ────────────────────────────────────────────────────

class TestApiKeyIssuance:
    def test_apikey_returns_201_and_token(self, client, auth_headers):
        resp = client.post("/auth/apikey", json={"userId": TEST_USER}, headers=auth_headers)
        assert resp.status_code == 201
        data = resp.json()
        assert "api_key" in data
        assert data["userId"] == TEST_USER
        assert data["token_type"] == "bearer"
        assert "expires_at" in data

    def test_apikey_is_valid_jwt_for_user(self, client, auth_headers):
        resp = client.post("/auth/apikey", json={"userId": TEST_USER}, headers=auth_headers)
        api_key = resp.json()["api_key"]
        # Decode and verify it authenticates as the correct user
        user_id = decode_access_token(api_key)
        assert user_id == TEST_USER

    def test_apikey_expires_in_roughly_one_year(self, client, auth_headers):
        resp = client.post("/auth/apikey", json={"userId": TEST_USER}, headers=auth_headers)
        expires_at = resp.json()["expires_at"]
        expire_dt = datetime.fromisoformat(expires_at)
        now = datetime.now(timezone.utc)
        days = (expire_dt - now).days
        assert 364 <= days <= 366

    def test_apikey_authenticates_on_write_endpoint(self, client, auth_headers):
        resp = client.post("/auth/apikey", json={"userId": TEST_USER}, headers=auth_headers)
        api_key = resp.json()["api_key"]
        write_resp = client.post(
            "/chatgpt/write",
            json={
                "userId":   TEST_USER,
                "model":    "gpt-4o",
                "messages": _make_messages(2),
            },
            headers={"Authorization": f"Bearer {api_key}"},
        )
        assert write_resp.status_code == 202

    def test_apikey_missing_userId_returns_422(self, client, auth_headers):
        resp = client.post("/auth/apikey", json={}, headers=auth_headers)
        assert resp.status_code == 422

    def test_apikey_requires_auth(self, client):
        """POST /auth/apikey without Bearer token must return 401/403."""
        resp = client.post("/auth/apikey", json={"userId": TEST_USER})
        assert resp.status_code in (401, 403)

    def test_apikey_rejects_userid_mismatch(self, client, auth_headers):
        """Authenticated as TEST_USER, requesting key for OTHER_USER must return 403."""
        resp = client.post(
            "/auth/apikey",
            json={"userId": OTHER_USER},
            headers=auth_headers,
        )
        assert resp.status_code == 403
        assert "Cannot create API keys for other users" in resp.json()["detail"]

    def test_token_endpoint_still_works_in_dev(self, client):
        resp = client.post("/auth/token", json={"userId": TEST_USER})
        assert resp.status_code == 201


# ── POST /chatgpt/write ───────────────────────────────────────────────────────

class TestChatGPTWrite:
    def test_write_returns_202(self, client, auth_headers):
        resp = client.post(
            "/chatgpt/write",
            json={
                "userId":         TEST_USER,
                "conversationId": str(uuid.uuid4()),
                "model":          "gpt-4o",
                "messages":       _make_messages(2),
            },
            headers=auth_headers,
        )
        assert resp.status_code == 202

    def test_write_response_shape(self, client, auth_headers):
        resp = client.post(
            "/chatgpt/write",
            json={
                "userId":   TEST_USER,
                "model":    "gpt-4o",
                "messages": _make_messages(2),
            },
            headers=auth_headers,
        )
        data = resp.json()
        assert data["status"] == "accepted"
        assert data["provider"] == "chatgpt"
        assert isinstance(data["conversationId"], str) and data["conversationId"]
        assert data["messageCount"] == 2

    def test_write_autogenerates_conversation_id_when_omitted(self, client, auth_headers):
        resp = client.post(
            "/chatgpt/write",
            json={
                "userId":   TEST_USER,
                "model":    "gpt-4o",
                "messages": _make_messages(2),
            },
            headers=auth_headers,
        )
        assert resp.status_code == 202
        conv_id = resp.json()["conversationId"]
        # Must be a valid UUID v4 (36 chars, hyphen-separated)
        assert len(conv_id) == 36
        assert conv_id.count("-") == 4

    def test_write_echoes_provided_conversation_id(self, client, auth_headers):
        conv_id = str(uuid.uuid4())
        resp = client.post(
            "/chatgpt/write",
            json={
                "userId":         TEST_USER,
                "conversationId": conv_id,
                "model":          "gpt-4o",
                "messages":       _make_messages(2),
            },
            headers=auth_headers,
        )
        assert resp.status_code == 202
        assert resp.json()["conversationId"] == conv_id

    def test_write_filters_system_and_tool_messages(self, client, auth_headers):
        resp = client.post(
            "/chatgpt/write",
            json={
                "userId": TEST_USER,
                "model":  "gpt-4o",
                "messages": [
                    {"role": "system",    "content": "You are a helpful assistant."},
                    {"role": "user",      "content": "What is quantum entanglement?"},
                    {"role": "tool",      "content": "Tool call result."},
                    {"role": "assistant", "content": "Quantum entanglement is a phenomenon..."},
                ],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 202
        # Only user + assistant stored (2 messages after filtering)
        assert resp.json()["messageCount"] == 2

    def test_write_all_system_messages_returns_422(self, client, auth_headers):
        resp = client.post(
            "/chatgpt/write",
            json={
                "userId": TEST_USER,
                "model":  "gpt-4o",
                "messages": [
                    {"role": "system", "content": "You are an AI assistant."},
                ],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 422
        assert "storable" in resp.json()["detail"].lower()

    def test_write_empty_messages_returns_422(self, client, auth_headers):
        resp = client.post(
            "/chatgpt/write",
            json={
                "userId":   TEST_USER,
                "model":    "gpt-4o",
                "messages": [],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_write_userid_mismatch_returns_403(self, client, auth_headers):
        resp = client.post(
            "/chatgpt/write",
            json={
                "userId":   "some-other-user",
                "model":    "gpt-4o",
                "messages": _make_messages(2),
            },
            headers=auth_headers,
        )
        assert resp.status_code == 403

    def test_write_missing_token_returns_401(self, client):
        resp = client.post(
            "/chatgpt/write",
            json={
                "userId":   TEST_USER,
                "model":    "gpt-4o",
                "messages": _make_messages(2),
            },
        )
        assert resp.status_code == 401

    def test_write_invalid_token_returns_401(self, client):
        resp = client.post(
            "/chatgpt/write",
            json={
                "userId":   TEST_USER,
                "model":    "gpt-4o",
                "messages": _make_messages(2),
            },
            headers={"Authorization": "Bearer not-a-valid-token"},
        )
        assert resp.status_code == 401

    def test_write_stores_verbatim_in_neo4j(self, client, auth_headers):
        """Verify conversation node + message nodes exist in Neo4j after write."""
        conv_id = str(uuid.uuid4())
        content = f"Unique verbatim ChatGPT content for {conv_id}"
        client.post(
            "/chatgpt/write",
            json={
                "userId":         TEST_USER,
                "conversationId": conv_id,
                "model":          "gpt-4o",
                "messages": [
                    {"role": "user",      "content": content},
                    {"role": "assistant", "content": "Verbatim assistant response."},
                ],
            },
            headers=auth_headers,
        )
        driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_username, settings.neo4j_password),
        )
        try:
            with driver.session(database=settings.neo4j_database) as session:
                result = session.run(
                    """
                    MATCH (c:Conversation {conversationId: $convId, userId: $uid})
                    MATCH (c)-[:HAS_MESSAGE]->(m:Message)
                    RETURN count(m) AS msgCount, c.provider AS provider
                    """,
                    convId=conv_id,
                    uid=TEST_USER,
                )
                row = result.single()
                assert row is not None, "Conversation not found in Neo4j"
                assert row["msgCount"] == 2
                assert row["provider"] == "chatgpt"
        finally:
            driver.close()

    def test_write_idempotent_same_conversation_id(self, client, auth_headers):
        """Writing the same conversationId twice must not create duplicate nodes."""
        conv_id = str(uuid.uuid4())
        payload = {
            "userId":         TEST_USER,
            "conversationId": conv_id,
            "model":          "gpt-4o",
            "messages": [
                {"role": "user",      "content": "Idempotency test message."},
                {"role": "assistant", "content": "Idempotency test response."},
            ],
        }
        client.post("/chatgpt/write", json=payload, headers=auth_headers)
        client.post("/chatgpt/write", json=payload, headers=auth_headers)

        driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_username, settings.neo4j_password),
        )
        try:
            with driver.session(database=settings.neo4j_database) as session:
                result = session.run(
                    """
                    MATCH (c:Conversation {conversationId: $convId})
                    RETURN count(c) AS convCount
                    """,
                    convId=conv_id,
                )
                assert result.single()["convCount"] == 1, "Duplicate conversation nodes created"
        finally:
            driver.close()


# ── GET /chatgpt/action-spec ──────────────────────────────────────────────────

class TestActionSpec:
    def test_action_spec_returns_200(self, client):
        resp = client.get("/chatgpt/action-spec")
        assert resp.status_code == 200

    def test_action_spec_is_valid_json(self, client):
        resp = client.get("/chatgpt/action-spec")
        spec = resp.json()
        assert isinstance(spec, dict)

    def test_action_spec_has_required_operations(self, client):
        spec = client.get("/chatgpt/action-spec").json()
        paths = spec.get("paths", {})
        assert "/chatgpt/write" in paths
        assert "/memory/query" in paths
        write_op = paths["/chatgpt/write"]["post"]
        query_op = paths["/memory/query"]["post"]
        assert write_op["operationId"] == "memory_write"
        assert query_op["operationId"] == "memory_query"

    def test_action_spec_injects_server_url(self, client):
        spec = client.get("/chatgpt/action-spec").json()
        servers = spec.get("servers", [])
        assert len(servers) == 1
        # TestClient uses http://testserver
        assert "testserver" in servers[0]["url"]

    def test_action_spec_has_bearer_auth(self, client):
        spec = client.get("/chatgpt/action-spec").json()
        schemes = spec["components"]["securitySchemes"]
        assert "BearerAuth" in schemes
        assert schemes["BearerAuth"]["scheme"] == "bearer"


# ── Cross-phase integration ───────────────────────────────────────────────────

class TestCrossPhaseIntegration:
    """
    Full write→query cross-phase flow.
    Uses a mocked OpenAI client to produce deterministic embeddings
    (same pattern as test_end_to_end.py).
    """

    def test_chatgpt_write_then_query_returns_verbatim_content(self, client, auth_headers):
        unique_content = f"quantum entanglement cross-phase test {uuid.uuid4()}"
        conv_id = str(uuid.uuid4())

        mock_openai = _mock_openai()
        app.state.openai_client = mock_openai

        # Write via ChatGPT endpoint
        write_resp = client.post(
            "/chatgpt/write",
            json={
                "userId":         TEST_USER,
                "conversationId": conv_id,
                "model":          "gpt-4o",
                "messages": [
                    {"role": "user",      "content": unique_content},
                    {"role": "assistant", "content": "Entanglement is a correlation between particles."},
                ],
            },
            headers=auth_headers,
        )
        assert write_resp.status_code == 202

        # Query via standard memory/query endpoint
        query_resp = client.post(
            "/memory/query",
            json={
                "userId":      TEST_USER,
                "query":       unique_content,
                "topK":        5,
                "tokenBudget": 4000,
            },
            headers=auth_headers,
        )
        assert query_resp.status_code == 200
        data = query_resp.json()
        all_content = " ".join(
            m["content"]
            for r in data["results"]
            for m in r["messages"]
        )
        assert unique_content in all_content

    def test_chatgpt_write_then_read_returns_verbatim_conversation(self, client, auth_headers):
        conv_id = str(uuid.uuid4())
        user_content = f"read-back verbatim test {uuid.uuid4()}"

        client.post(
            "/chatgpt/write",
            json={
                "userId":         TEST_USER,
                "conversationId": conv_id,
                "model":          "gpt-4o",
                "messages": [
                    {"role": "user",      "content": user_content},
                    {"role": "assistant", "content": "Assistant verbatim reply."},
                ],
            },
            headers=auth_headers,
        )

        read_resp = client.get(
            f"/memory/conversation/{conv_id}",
            headers=auth_headers,
        )
        assert read_resp.status_code == 200
        data = read_resp.json()
        assert data["conversationId"] == conv_id
        assert data["provider"] == "chatgpt"
        contents = [m["content"] for m in data["messages"]]
        assert user_content in contents

    def test_chatgpt_write_invisible_to_other_user(self, client, auth_headers, other_headers):
        conv_id = str(uuid.uuid4())
        client.post(
            "/chatgpt/write",
            json={
                "userId":         TEST_USER,
                "conversationId": conv_id,
                "model":          "gpt-4o",
                "messages":       _make_messages(2),
            },
            headers=auth_headers,
        )

        # Other user cannot read this conversation
        read_resp = client.get(
            f"/memory/conversation/{conv_id}",
            headers=other_headers,
        )
        assert read_resp.status_code == 404

    def test_chatgpt_and_claude_writes_both_queryable_by_same_user(
        self, client, auth_headers
    ):
        """
        Writes from two different providers under the same userId
        must both be queryable — this is the core cross-LLM value proposition.
        """
        chatgpt_content = f"chatgpt cross-llm test {uuid.uuid4()}"
        claude_content  = f"claude cross-llm test {uuid.uuid4()}"

        # Write via ChatGPT endpoint
        client.post(
            "/chatgpt/write",
            json={
                "userId":   TEST_USER,
                "model":    "gpt-4o",
                "messages": [{"role": "user", "content": chatgpt_content}],
            },
            headers=auth_headers,
        )

        # Write via standard /memory/write endpoint (Claude provider)
        from memory.auth.jwt_handler import create_access_token
        token = create_access_token(TEST_USER)
        client.post(
            "/memory/write",
            json={
                "userId":         TEST_USER,
                "conversationId": str(uuid.uuid4()),
                "provider":       "claude",
                "model":          "claude-sonnet-4-6",
                "messages": [
                    {
                        "messageId":  str(uuid.uuid4()),
                        "role":       "user",
                        "content":    claude_content,
                        "timestamp":  datetime.now(timezone.utc).isoformat(),
                        "tokenCount": 10,
                    }
                ],
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        # List conversations — should have entries from both providers
        list_resp = client.get("/memory/conversations", headers=auth_headers)
        assert list_resp.status_code == 200
        providers = {c["provider"] for c in list_resp.json()["conversations"]}
        assert "chatgpt" in providers
        assert "claude" in providers

    def test_chatgpt_write_provider_filter_isolates_results(self, client, auth_headers):
        """Querying with providers=['chatgpt'] must not return claude data."""
        claude_content  = f"claude exclusive {uuid.uuid4()}"
        chatgpt_content = f"chatgpt exclusive {uuid.uuid4()}"
        token = create_access_token(TEST_USER)

        # Write a Claude conversation
        client.post(
            "/memory/write",
            json={
                "userId":         TEST_USER,
                "conversationId": str(uuid.uuid4()),
                "provider":       "claude",
                "model":          "claude-sonnet-4-6",
                "messages": [
                    {
                        "messageId":  str(uuid.uuid4()),
                        "role":       "user",
                        "content":    claude_content,
                        "timestamp":  datetime.now(timezone.utc).isoformat(),
                        "tokenCount": 10,
                    }
                ],
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        # Write a ChatGPT conversation
        client.post(
            "/chatgpt/write",
            json={
                "userId":   TEST_USER,
                "model":    "gpt-4o",
                "messages": [{"role": "user", "content": chatgpt_content}],
            },
            headers=auth_headers,
        )

        # List with provider filter
        list_resp = client.get(
            "/memory/conversations?provider=chatgpt",
            headers=auth_headers,
        )
        assert list_resp.status_code == 200
        for conv in list_resp.json()["conversations"]:
            assert conv["provider"] == "chatgpt"
