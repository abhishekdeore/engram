"""
Phase 6 — Chunk cascade delete tests
======================================
Verifies that deleting a conversation or purging a user also removes
all Chunk nodes linked via (Message)-[:HAS_CHUNK]->(Chunk).

These tests create conversations with long messages (>512 tokens) that
trigger chunk creation during the embedding pipeline, then verify that
cascade deletes remove chunks completely — leaving no orphans in the
database or vector index.

Run with:
    uv run pytest tests/test_chunk_cascade_delete.py -v
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

# ── Constants ────────────────────────────────────────────────────────────────

CHUNK_USER_ID = "test-user-chunk-cascade"

# A message long enough to exceed the 512-token CHUNK_THRESHOLD and produce
# multiple Chunk nodes.  ~600 tokens of repeated words.
LONG_CONTENT = ("The quick brown fox jumps over the lazy dog. " * 120).strip()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _sync_run(cypher: str, **params) -> list[dict]:
    driver = GraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_username, settings.neo4j_password)
    )
    try:
        with driver.session(database=settings.neo4j_database) as sess:
            return sess.run(cypher, **params).data()
    finally:
        driver.close()


def _teardown():
    """Remove all test data including Chunk nodes."""
    _sync_run(
        """
        MATCH (u:User {userId: $uid})
        OPTIONAL MATCH (u)-[:HAS_CONVERSATION]->(c:Conversation)
        OPTIONAL MATCH (c)-[:HAS_MESSAGE]->(m:Message)
        OPTIONAL MATCH (m)-[:HAS_CHUNK]->(ch:Chunk)
        OPTIONAL MATCH (c)-[:HAS_SEGMENT]->(s:Segment)
        DETACH DELETE ch, s, m, c, u
        """,
        uid=CHUNK_USER_ID,
    )


def _fake_embedding(text: str) -> list[float]:
    """Deterministic unit vector from text hash."""
    h = int(hashlib.sha256(text.encode()).hexdigest(), 16)
    dim = h % EMBEDDING_DIMS
    vec = [0.0] * EMBEDDING_DIMS
    vec[dim] = 1.0
    return vec


def _make_openai_mock():
    """Mock OpenAI client that returns deterministic embeddings."""
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


def _count_chunks_for_conversation(conv_id: str) -> int:
    rows = _sync_run(
        """
        MATCH (c:Conversation {conversationId: $cid})-[:HAS_MESSAGE]->(m:Message)
              -[:HAS_CHUNK]->(ch:Chunk)
        RETURN count(ch) AS cnt
        """,
        cid=conv_id,
    )
    return rows[0]["cnt"] if rows else 0


def _count_orphaned_chunks() -> int:
    """Count Chunk nodes that have no incoming HAS_CHUNK relationship."""
    rows = _sync_run(
        """
        MATCH (ch:Chunk)
        WHERE NOT (ch)<-[:HAS_CHUNK]-()
        RETURN count(ch) AS cnt
        """
    )
    return rows[0]["cnt"] if rows else 0


def _count_chunks_for_user(user_id: str) -> int:
    rows = _sync_run(
        """
        MATCH (ch:Chunk {userId: $uid})
        RETURN count(ch) AS cnt
        """,
        uid=user_id,
    )
    return rows[0]["cnt"] if rows else 0


def _write_long_message(client, auth, user_id, conv_id=None):
    """Write a conversation with a message long enough to produce chunks."""
    cid = conv_id or str(uuid.uuid4())
    resp = client.post(
        "/memory/write",
        json={
            "userId":         user_id,
            "conversationId": cid,
            "provider":       "chatgpt",
            "model":          "gpt-4o",
            "messages": [{
                "messageId":  str(uuid.uuid4()),
                "role":       "user",
                "content":    LONG_CONTENT,
                "timestamp":  datetime.now(timezone.utc).isoformat(),
                "tokenCount": 600,  # above CHUNK_THRESHOLD of 512
            }],
        },
        headers=auth,
    )
    assert resp.status_code == 202
    return cid


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
def teardown():
    _teardown()
    yield
    _teardown()


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def auth() -> dict:
    return {"Authorization": f"Bearer {create_access_token(CHUNK_USER_ID)}"}


# ── Tests: Conversation delete cascades to chunks ────────────────────────────

class TestDeleteConversationRemovesChunks:
    """Verify that DELETE /memory/conversation/{id} also removes Chunk nodes."""

    def test_chunks_created_after_write(self, client, auth):
        """Precondition: writing a long message with mock OpenAI creates chunks."""
        app.state.openai_client = _make_openai_mock()
        cid = _write_long_message(client, auth, CHUNK_USER_ID)
        # Wait for background task to complete (TestClient runs them synchronously)
        chunk_count = _count_chunks_for_conversation(cid)
        assert chunk_count > 0, (
            f"Expected Chunk nodes for conversation {cid}, got {chunk_count}"
        )
        # Clean up
        client.delete(f"/memory/conversation/{cid}", headers=auth)

    def test_delete_conversation_removes_chunks(self, client, auth):
        """After deleting a conversation, all its Chunk nodes must be gone."""
        app.state.openai_client = _make_openai_mock()
        cid = _write_long_message(client, auth, CHUNK_USER_ID)

        # Confirm chunks exist before delete
        assert _count_chunks_for_conversation(cid) > 0

        # Delete the conversation
        resp = client.delete(f"/memory/conversation/{cid}", headers=auth)
        assert resp.status_code == 200

        # Verify chunks are gone
        remaining = _sync_run(
            """
            MATCH (ch:Chunk {conversationId: $cid})
            RETURN count(ch) AS cnt
            """,
            cid=cid,
        )
        assert remaining[0]["cnt"] == 0, "Chunk nodes were not cascade-deleted"

    def test_nodes_deleted_count_includes_chunks(self, client, auth):
        """The nodesDeleted count in the response must include Chunk nodes."""
        app.state.openai_client = _make_openai_mock()
        cid = _write_long_message(client, auth, CHUNK_USER_ID)

        chunk_count = _count_chunks_for_conversation(cid)
        assert chunk_count > 0

        resp = client.delete(f"/memory/conversation/{cid}", headers=auth)
        body = resp.json()

        # At minimum: chunks + 1 message + 1 conversation = chunk_count + 2
        assert body["nodesDeleted"] >= chunk_count + 2, (
            f"nodesDeleted={body['nodesDeleted']} but expected at least "
            f"{chunk_count + 2} (chunks={chunk_count} + message + conversation)"
        )

    def test_no_orphaned_chunks_after_delete(self, client, auth):
        """After deletion, no Chunk nodes should lack a parent HAS_CHUNK relationship."""
        app.state.openai_client = _make_openai_mock()
        cid = _write_long_message(client, auth, CHUNK_USER_ID)
        assert _count_chunks_for_conversation(cid) > 0

        client.delete(f"/memory/conversation/{cid}", headers=auth)

        orphans = _count_orphaned_chunks()
        assert orphans == 0, f"Found {orphans} orphaned Chunk nodes after delete"


# ── Tests: GDPR purge cascades to chunks ─────────────────────────────────────

class TestGDPRDeleteRemovesChunks:
    """Verify that DELETE /memory/user/{userId} also removes all Chunk nodes."""

    def test_gdpr_delete_removes_chunks(self, client):
        """After GDPR purge, all Chunk nodes for the user must be gone."""
        purge_uid = f"purge-chunk-user-{uuid.uuid4()}"
        token = create_access_token(purge_uid)
        h = {"Authorization": f"Bearer {token}"}
        app.state.openai_client = _make_openai_mock()

        # Write a conversation with chunks
        cid = str(uuid.uuid4())
        client.post(
            "/memory/write",
            json={
                "userId":         purge_uid,
                "conversationId": cid,
                "provider":       "chatgpt",
                "model":          "gpt-4o",
                "messages": [{
                    "messageId":  str(uuid.uuid4()),
                    "role":       "user",
                    "content":    LONG_CONTENT,
                    "timestamp":  datetime.now(timezone.utc).isoformat(),
                    "tokenCount": 600,
                }],
            },
            headers=h,
        )

        # Confirm chunks exist
        assert _count_chunks_for_user(purge_uid) > 0

        # GDPR purge
        resp = client.delete(f"/memory/user/{purge_uid}", headers=h)
        assert resp.status_code == 200

        # Verify no chunks remain for this user
        assert _count_chunks_for_user(purge_uid) == 0, (
            "Chunk nodes survived GDPR purge"
        )

    def test_gdpr_nodes_deleted_includes_chunks(self, client):
        """The nodesDeleted count from GDPR purge must include Chunk nodes."""
        purge_uid = f"purge-chunk-count-{uuid.uuid4()}"
        token = create_access_token(purge_uid)
        h = {"Authorization": f"Bearer {token}"}
        app.state.openai_client = _make_openai_mock()

        cid = str(uuid.uuid4())
        client.post(
            "/memory/write",
            json={
                "userId":         purge_uid,
                "conversationId": cid,
                "provider":       "chatgpt",
                "model":          "gpt-4o",
                "messages": [{
                    "messageId":  str(uuid.uuid4()),
                    "role":       "user",
                    "content":    LONG_CONTENT,
                    "timestamp":  datetime.now(timezone.utc).isoformat(),
                    "tokenCount": 600,
                }],
            },
            headers=h,
        )

        chunk_count = _count_chunks_for_user(purge_uid)
        assert chunk_count > 0

        resp = client.delete(f"/memory/user/{purge_uid}", headers=h)
        body = resp.json()

        # At minimum: chunks + 1 message + 1 conversation + 1 user
        assert body["nodesDeleted"] >= chunk_count + 3, (
            f"nodesDeleted={body['nodesDeleted']} but expected at least "
            f"{chunk_count + 3} (chunks={chunk_count} + message + conv + user)"
        )

    def test_no_orphaned_chunks_after_gdpr_purge(self, client):
        """After GDPR purge, no orphaned Chunk nodes should exist for the user."""
        purge_uid = f"purge-orphan-check-{uuid.uuid4()}"
        token = create_access_token(purge_uid)
        h = {"Authorization": f"Bearer {token}"}
        app.state.openai_client = _make_openai_mock()

        cid = str(uuid.uuid4())
        client.post(
            "/memory/write",
            json={
                "userId":         purge_uid,
                "conversationId": cid,
                "provider":       "chatgpt",
                "model":          "gpt-4o",
                "messages": [{
                    "messageId":  str(uuid.uuid4()),
                    "role":       "user",
                    "content":    LONG_CONTENT,
                    "timestamp":  datetime.now(timezone.utc).isoformat(),
                    "tokenCount": 600,
                }],
            },
            headers=h,
        )

        assert _count_chunks_for_user(purge_uid) > 0

        client.delete(f"/memory/user/{purge_uid}", headers=h)

        # Check for orphans specifically belonging to this user
        rows = _sync_run(
            """
            MATCH (ch:Chunk {userId: $uid})
            RETURN count(ch) AS cnt
            """,
            uid=purge_uid,
        )
        assert rows[0]["cnt"] == 0, "Orphaned Chunk nodes remain after GDPR purge"
