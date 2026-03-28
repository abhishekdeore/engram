"""
End-to-end integration tests — write → embed → query pipeline
==============================================================
These tests exercise the full cross-phase flow as a single system:

  POST /memory/write
    → BackgroundTask fires (synchronously under TestClient)
    → embeddings land on Neo4j nodes
    → POST /memory/query returns the written conversation verbatim

The OpenAI client is replaced with a deterministic mock that returns
a unit vector keyed to the written content, so the same mock can be used
for both the embedding step (write side) and the query embedding (query side)
— guaranteeing cosine similarity = 1.0 for a matching query.

Run with:
    uv run pytest tests/test_end_to_end.py -v
"""

import sys
import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from neo4j import GraphDatabase

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from memory.api.main import app
from memory.auth.jwt_handler import create_access_token
from memory.config import settings
from memory.services.embedding_service import EMBEDDING_DIMS

# ── Constants ─────────────────────────────────────────────────────────────────

E2E_USER_ID = "test-user-e2e"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fake_embedding(text: str) -> list[float]:
    """Deterministic unit vector seeded from SHA-256(text)."""
    h   = int(hashlib.sha256(text.encode()).hexdigest(), 16)
    dim = h % EMBEDDING_DIMS
    vec = [0.0] * EMBEDDING_DIMS
    vec[dim] = 1.0
    return vec


def _make_openai_mock():
    """
    Returns a mock AsyncOpenAI client.
    embeddings.create() returns _fake_embedding(input_text) for every input.
    """
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
        settings.neo4j_uri, auth=(settings.neo4j_username, settings.neo4j_password)
    )
    try:
        with driver.session(database=settings.neo4j_database) as sess:
            return sess.run(cypher, **params).data()
    finally:
        driver.close()


def _teardown_e2e():
    _sync_run(
        """
        MATCH (u:User {userId: $uid})
        OPTIONAL MATCH (u)-[:HAS_CONVERSATION]->(c:Conversation)
        OPTIONAL MATCH (c)-[:HAS_MESSAGE]->(m:Message)
        OPTIONAL MATCH (m)-[:HAS_CHUNK]->(ch:Chunk)
        OPTIONAL MATCH (c)-[:HAS_SEGMENT]->(s:Segment)
        DETACH DELETE u, c, m, ch, s
        """,
        uid=E2E_USER_ID,
    )


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
def teardown():
    yield
    _teardown_e2e()


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def auth() -> dict:
    token = create_access_token(E2E_USER_ID)
    return {"Authorization": f"Bearer {token}"}


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestEndToEnd:
    def test_write_then_query_returns_verbatim_content(self, client, auth):
        """
        Full pipeline: write a conversation → BackgroundTask embeds it →
        query with matching vector → verbatim content is returned.
        """
        conv_id = str(uuid.uuid4())
        msg_id  = str(uuid.uuid4())
        content = "Quantum entanglement allows two particles to be correlated."

        # Install mock OpenAI client so both write (embed) and query use it
        openai_mock = _make_openai_mock()
        app.state.openai_client = openai_mock
        app.state.redis_client  = None

        # Write
        write_resp = client.post(
            "/memory/write",
            json={
                "userId":         E2E_USER_ID,
                "conversationId": conv_id,
                "provider":       "chatgpt",
                "model":          "gpt-4o",
                "messages": [{
                    "messageId":  msg_id,
                    "role":       "user",
                    "content":    content,
                    "timestamp":  datetime.now(timezone.utc).isoformat(),
                    "tokenCount": 12,
                }],
            },
            headers=auth,
        )
        assert write_resp.status_code == 202

        # Query with the same embedding key (content → same unit vector → cosine = 1.0)
        query_resp = client.post(
            "/memory/query",
            json={"userId": E2E_USER_ID, "query": content, "topK": 5},
            headers=auth,
        )
        assert query_resp.status_code == 200
        body = query_resp.json()

        all_contents = [
            msg["content"]
            for conv in body["results"]
            for msg in conv["messages"]
        ]
        assert content in all_contents, (
            "Verbatim message content must be returned by query after write+embed"
        )

    def test_write_totalTokens_accurate_on_duplicate_send(self, client, auth):
        """
        totalTokens must equal the actual token sum after a duplicate send.
        Re-sending the same messages must not inflate the counter.
        """
        conv_id = str(uuid.uuid4())
        msg_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
        token_counts = [5, 8]

        app.state.openai_client = None  # embeddings not needed for this test

        payload = {
            "userId":         E2E_USER_ID,
            "conversationId": conv_id,
            "provider":       "claude",
            "model":          "claude-sonnet-4-6",
            "messages": [
                {
                    "messageId":  msg_ids[0],
                    "role":       "user",
                    "content":    "First message",
                    "timestamp":  datetime.now(timezone.utc).isoformat(),
                    "tokenCount": token_counts[0],
                },
                {
                    "messageId":  msg_ids[1],
                    "role":       "assistant",
                    "content":    "Second message",
                    "timestamp":  datetime.now(timezone.utc).isoformat(),
                    "tokenCount": token_counts[1],
                },
            ],
        }

        # First write
        r1 = client.post("/memory/write", json=payload, headers=auth)
        assert r1.status_code == 202

        # Duplicate send
        r2 = client.post("/memory/write", json=payload, headers=auth)
        assert r2.status_code == 202

        rows = _sync_run(
            "MATCH (c:Conversation {conversationId: $cid}) RETURN c.totalTokens AS t",
            cid=conv_id,
        )
        assert rows[0]["t"] == sum(token_counts), (
            f"totalTokens should be {sum(token_counts)} after duplicate send, "
            f"got {rows[0]['t']}"
        )

    def test_providers_filter_excludes_other_providers(self, client, auth):
        """
        providers=['chatgpt'] must not return a conversation written with provider='claude'.
        """
        claude_conv_id = str(uuid.uuid4())

        app.state.openai_client = None

        client.post(
            "/memory/write",
            json={
                "userId":         E2E_USER_ID,
                "conversationId": claude_conv_id,
                "provider":       "claude",
                "model":          "claude-sonnet-4-6",
                "messages": [{
                    "messageId":  str(uuid.uuid4()),
                    "role":       "user",
                    "content":    "providerfiltertest unique phrase",
                    "timestamp":  datetime.now(timezone.utc).isoformat(),
                    "tokenCount": 4,
                }],
            },
            headers=auth,
        )

        # Query restricting to chatgpt only
        resp = client.post(
            "/memory/query",
            json={
                "userId":    E2E_USER_ID,
                "query":     "providerfiltertest unique phrase",
                "providers": ["chatgpt"],
            },
            headers=auth,
        )
        assert resp.status_code == 200
        conv_ids = [r["conversationId"] for r in resp.json()["results"]]
        assert claude_conv_id not in conv_ids, (
            "A claude conversation must not appear when providers=['chatgpt']"
        )

    def test_write_then_query_searchmode_reflects_embedding_presence(self, client, auth):
        """
        When embeddings are present, searchMode must be 'vector'.
        When openai_client is None (no embeddings), searchMode must be 'fulltext' or 'empty'.
        """
        conv_id = str(uuid.uuid4())
        content = "e2e searchmode verification content " + conv_id

        openai_mock = _make_openai_mock()
        app.state.openai_client = openai_mock
        app.state.redis_client  = None

        client.post(
            "/memory/write",
            json={
                "userId":         E2E_USER_ID,
                "conversationId": conv_id,
                "provider":       "chatgpt",
                "model":          "gpt-4o",
                "messages": [{
                    "messageId":  str(uuid.uuid4()),
                    "role":       "user",
                    "content":    content,
                    "timestamp":  datetime.now(timezone.utc).isoformat(),
                    "tokenCount": 8,
                }],
            },
            headers=auth,
        )

        resp = client.post(
            "/memory/query",
            json={"userId": E2E_USER_ID, "query": content},
            headers=auth,
        )
        assert resp.status_code == 200
        assert resp.json()["searchMode"] == "vector", (
            "searchMode must be 'vector' when embeddings were computed during write"
        )
