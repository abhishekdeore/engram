"""
Phase 3 — Integration tests for DELETE /memory/conversation/{id}
            and DELETE /memory/user/{userId}
================================================================
Tests run against the real Neo4j instance.

Run with:
    uv run pytest tests/test_delete_api.py -v
"""

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from neo4j import GraphDatabase

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from memory.api.main import app
from memory.auth.jwt_handler import create_access_token
from memory.config import settings

# ── Constants ─────────────────────────────────────────────────────────────────

DELETE_USER_ID       = "test-user-delete-api"
DELETE_OTHER_USER_ID = "test-user-delete-other"


# ── Helpers ───────────────────────────────────────────────────────────────────

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
    for uid in (DELETE_USER_ID, DELETE_OTHER_USER_ID):
        _sync_run(
            """
            MATCH (u:User {userId: $uid})
            OPTIONAL MATCH (u)-[:HAS_CONVERSATION]->(c:Conversation)
            OPTIONAL MATCH (c)-[:HAS_MESSAGE]->(m:Message)
            OPTIONAL MATCH (m)-[:HAS_CHUNK]->(ch:Chunk)
            OPTIONAL MATCH (c)-[:HAS_SEGMENT]->(s:Segment)
            DETACH DELETE ch, s, m, c, u
            """,
            uid=uid,
        )


def _write(client, auth, user_id, conv_id=None, provider="chatgpt"):
    cid = conv_id or str(uuid.uuid4())
    resp = client.post(
        "/memory/write",
        json={
            "userId":         user_id,
            "conversationId": cid,
            "provider":       provider,
            "model":          "gpt-4o",
            "messages": [{
                "messageId":  str(uuid.uuid4()),
                "role":       "user",
                "content":    f"Delete test message for {cid}",
                "timestamp":  datetime.now(timezone.utc).isoformat(),
                "tokenCount": 4,
            }],
        },
        headers=auth,
    )
    assert resp.status_code == 202
    return cid


def _conv_exists(conv_id: str) -> bool:
    rows = _sync_run(
        "MATCH (c:Conversation {conversationId: $cid}) RETURN c.conversationId AS id",
        cid=conv_id,
    )
    return bool(rows)


def _user_exists(user_id: str) -> bool:
    rows = _sync_run(
        "MATCH (u:User {userId: $uid}) RETURN u.userId AS id",
        uid=user_id,
    )
    return bool(rows)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
def teardown():
    yield
    _teardown()


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def auth() -> dict:
    return {"Authorization": f"Bearer {create_access_token(DELETE_USER_ID)}"}


@pytest.fixture(scope="module")
def other_auth() -> dict:
    return {"Authorization": f"Bearer {create_access_token(DELETE_OTHER_USER_ID)}"}


# ── TestDeleteConversation ────────────────────────────────────────────────────

class TestDeleteConversation:
    def test_requires_auth(self, client):
        resp = client.delete(f"/memory/conversation/{uuid.uuid4()}")
        assert resp.status_code in (401, 403)

    def test_returns_200_on_success(self, client, auth):
        app.state.openai_client = None
        cid = _write(client, auth, DELETE_USER_ID)
        resp = client.delete(f"/memory/conversation/{cid}", headers=auth)
        assert resp.status_code == 200

    def test_response_shape(self, client, auth):
        cid  = _write(client, auth, DELETE_USER_ID)
        resp = client.delete(f"/memory/conversation/{cid}", headers=auth)
        body = resp.json()
        assert body["status"] == "deleted"
        assert body["deletedId"] == cid
        assert "nodesDeleted" in body
        assert "message" in body

    def test_conversation_no_longer_exists_in_neo4j(self, client, auth):
        cid = _write(client, auth, DELETE_USER_ID)
        assert _conv_exists(cid)
        client.delete(f"/memory/conversation/{cid}", headers=auth)
        assert not _conv_exists(cid)

    def test_messages_cascade_deleted(self, client, auth):
        cid = _write(client, auth, DELETE_USER_ID)
        msg_id_rows = _sync_run(
            """
            MATCH (c:Conversation {conversationId: $cid})-[:HAS_MESSAGE]->(m:Message)
            RETURN m.messageId AS mid
            """,
            cid=cid,
        )
        assert msg_id_rows  # messages exist before delete
        client.delete(f"/memory/conversation/{cid}", headers=auth)
        for row in msg_id_rows:
            remaining = _sync_run(
                "MATCH (m:Message {messageId: $mid}) RETURN m.messageId AS id",
                mid=row["mid"],
            )
            assert not remaining, f"Message {row['mid']} was not deleted"

    def test_404_for_nonexistent_conversation(self, client, auth):
        resp = client.delete(f"/memory/conversation/{uuid.uuid4()}", headers=auth)
        assert resp.status_code == 404

    def test_cannot_delete_other_users_conversation(self, client, auth, other_auth):
        """A user must not be able to delete another user's conversation."""
        other_cid = _write(client, other_auth, DELETE_OTHER_USER_ID)
        resp = client.delete(f"/memory/conversation/{other_cid}", headers=auth)
        # Must not succeed — 404 (no information leak) or 403
        assert resp.status_code in (403, 404)
        # Conversation must still exist
        assert _conv_exists(other_cid)

    def test_delete_is_idempotent_404_on_second_call(self, client, auth):
        cid = _write(client, auth, DELETE_USER_ID)
        r1 = client.delete(f"/memory/conversation/{cid}", headers=auth)
        assert r1.status_code == 200
        r2 = client.delete(f"/memory/conversation/{cid}", headers=auth)
        assert r2.status_code == 404


# ── TestDeleteUser (GDPR) ─────────────────────────────────────────────────────

class TestDeleteUser:
    def test_requires_auth(self, client):
        resp = client.delete(f"/memory/user/{DELETE_USER_ID}")
        assert resp.status_code in (401, 403)

    def test_userId_mismatch_returns_403(self, client, auth):
        """Token userId must match path userId."""
        resp = client.delete(f"/memory/user/some-other-user", headers=auth)
        assert resp.status_code == 403

    def test_returns_200_on_success(self, client):
        """Use a fresh user so this test is independent of others."""
        purge_uid = f"purge-user-{uuid.uuid4()}"
        token  = create_access_token(purge_uid)
        h = {"Authorization": f"Bearer {token}"}
        app.state.openai_client = None
        # Seed data
        with TestClient(app) as c:
            c.post(
                "/memory/write",
                json={
                    "userId":         purge_uid,
                    "conversationId": str(uuid.uuid4()),
                    "provider":       "claude",
                    "model":          "claude-sonnet-4-6",
                    "messages": [{
                        "messageId":  str(uuid.uuid4()),
                        "role":       "user",
                        "content":    "GDPR purge test",
                        "timestamp":  datetime.now(timezone.utc).isoformat(),
                        "tokenCount": 3,
                    }],
                },
                headers=h,
            )
            resp = c.delete(f"/memory/user/{purge_uid}", headers=h)
        assert resp.status_code == 200

    def test_all_user_data_purged(self, client):
        """After GDPR delete, no User, Conversation, or Message nodes remain."""
        purge_uid = f"purge-user-{uuid.uuid4()}"
        token = create_access_token(purge_uid)
        h = {"Authorization": f"Bearer {token}"}
        app.state.openai_client = None

        conv_id = str(uuid.uuid4())
        with TestClient(app) as c:
            c.post(
                "/memory/write",
                json={
                    "userId":         purge_uid,
                    "conversationId": conv_id,
                    "provider":       "chatgpt",
                    "model":          "gpt-4o",
                    "messages": [{
                        "messageId":  str(uuid.uuid4()),
                        "role":       "user",
                        "content":    "GDPR purge data check",
                        "timestamp":  datetime.now(timezone.utc).isoformat(),
                        "tokenCount": 3,
                    }],
                },
                headers=h,
            )
            assert _user_exists(purge_uid)
            assert _conv_exists(conv_id)
            c.delete(f"/memory/user/{purge_uid}", headers=h)

        assert not _user_exists(purge_uid)
        assert not _conv_exists(conv_id)

    def test_response_shape(self, client):
        purge_uid = f"purge-user-{uuid.uuid4()}"
        token = create_access_token(purge_uid)
        h = {"Authorization": f"Bearer {token}"}
        app.state.openai_client = None

        with TestClient(app) as c:
            c.post(
                "/memory/write",
                json={
                    "userId":         purge_uid,
                    "conversationId": str(uuid.uuid4()),
                    "provider":       "chatgpt",
                    "model":          "gpt-4o",
                    "messages": [{
                        "messageId":  str(uuid.uuid4()),
                        "role":       "user",
                        "content":    "Shape check",
                        "timestamp":  datetime.now(timezone.utc).isoformat(),
                        "tokenCount": 2,
                    }],
                },
                headers=h,
            )
            resp = c.delete(f"/memory/user/{purge_uid}", headers=h)

        body = resp.json()
        assert body["status"] == "deleted"
        assert body["deletedId"] == purge_uid
        assert "nodesDeleted" in body
        assert body["nodesDeleted"] > 0

    def test_nodesdeleted_positive(self, client):
        purge_uid = f"purge-user-{uuid.uuid4()}"
        token = create_access_token(purge_uid)
        h = {"Authorization": f"Bearer {token}"}
        app.state.openai_client = None

        with TestClient(app) as c:
            c.post(
                "/memory/write",
                json={
                    "userId":         purge_uid,
                    "conversationId": str(uuid.uuid4()),
                    "provider":       "chatgpt",
                    "model":          "gpt-4o",
                    "messages": [{
                        "messageId":  str(uuid.uuid4()),
                        "role":       "user",
                        "content":    "Nodes deleted check",
                        "timestamp":  datetime.now(timezone.utc).isoformat(),
                        "tokenCount": 2,
                    }],
                },
                headers=h,
            )
            resp = c.delete(f"/memory/user/{purge_uid}", headers=h)

        # At minimum: 1 User + 1 Conversation + 1 Message = 3
        assert resp.json()["nodesDeleted"] >= 3
