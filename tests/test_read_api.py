"""
Phase 3 — Integration tests for GET /memory/conversations and
          GET /memory/conversation/{conversationId}
==============================================================
Tests run against the real Neo4j instance.
All test data is seeded via POST /memory/write (BackgroundTask fires
synchronously under TestClient) so the read path is tested end-to-end.

Run with:
    uv run pytest tests/test_read_api.py -v
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

READ_USER_ID   = "test-user-read-api"
OTHER_USER_ID  = "test-user-read-other"


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
    for uid in (READ_USER_ID, OTHER_USER_ID):
        _sync_run(
            """
            MATCH (u:User {userId: $uid})
            OPTIONAL MATCH (u)-[:HAS_CONVERSATION]->(c:Conversation)
            OPTIONAL MATCH (c)-[:HAS_MESSAGE]->(m:Message)
            OPTIONAL MATCH (c)-[:HAS_SEGMENT]->(s:Segment)
            DETACH DELETE u, c, m, s
            """,
            uid=uid,
        )


def _write_conversation(client, auth, user_id, conv_id, provider="chatgpt",
                        model="gpt-4o", messages=None):
    if messages is None:
        messages = [
            {
                "messageId":  str(uuid.uuid4()),
                "role":       "user",
                "content":    f"Test message for {conv_id}",
                "timestamp":  datetime.now(timezone.utc).isoformat(),
                "tokenCount": 5,
            }
        ]
    resp = client.post(
        "/memory/write",
        json={
            "userId":         user_id,
            "conversationId": conv_id,
            "provider":       provider,
            "model":          model,
            "messages":       messages,
        },
        headers=auth,
    )
    assert resp.status_code == 202
    return resp


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
    return {"Authorization": f"Bearer {create_access_token(READ_USER_ID)}"}


@pytest.fixture(scope="module")
def other_auth() -> dict:
    return {"Authorization": f"Bearer {create_access_token(OTHER_USER_ID)}"}


@pytest.fixture(scope="module")
def seeded_conv_ids(client, auth) -> list[str]:
    """
    Seed three conversations for READ_USER_ID with different providers.
    Returns the list of conversationIds in insertion order.
    """
    app.state.openai_client = None
    ids = []
    for provider in ("chatgpt", "claude", "gemini"):
        cid = str(uuid.uuid4())
        _write_conversation(client, auth, READ_USER_ID, cid, provider=provider)
        ids.append(cid)
    return ids


# ── TestListConversations ─────────────────────────────────────────────────────

class TestListConversations:
    def test_requires_auth(self, client):
        resp = client.get("/memory/conversations")
        assert resp.status_code in (401, 403)

    def test_returns_200_with_valid_token(self, client, auth, seeded_conv_ids):
        resp = client.get("/memory/conversations", headers=auth)
        assert resp.status_code == 200

    def test_response_shape(self, client, auth, seeded_conv_ids):
        resp = client.get("/memory/conversations", headers=auth)
        body = resp.json()
        assert "conversations" in body
        assert "total" in body
        assert "limit" in body
        assert "offset" in body

    def test_returns_seeded_conversations(self, client, auth, seeded_conv_ids):
        resp = client.get("/memory/conversations", headers=auth)
        returned_ids = {c["conversationId"] for c in resp.json()["conversations"]}
        for cid in seeded_conv_ids:
            assert cid in returned_ids

    def test_total_reflects_all_conversations(self, client, auth, seeded_conv_ids):
        resp = client.get("/memory/conversations", headers=auth)
        body = resp.json()
        assert body["total"] >= len(seeded_conv_ids)

    def test_provider_filter_returns_only_matching(self, client, auth, seeded_conv_ids):
        resp = client.get(
            "/memory/conversations", headers=auth, params={"provider": "claude"}
        )
        body = resp.json()
        assert resp.status_code == 200
        for conv in body["conversations"]:
            assert conv["provider"] == "claude"

    def test_provider_filter_excludes_other_providers(self, client, auth, seeded_conv_ids):
        resp = client.get(
            "/memory/conversations", headers=auth, params={"provider": "chatgpt"}
        )
        returned_ids = {c["conversationId"] for c in resp.json()["conversations"]}
        # The claude and gemini conversations must not appear
        assert seeded_conv_ids[1] not in returned_ids  # claude
        assert seeded_conv_ids[2] not in returned_ids  # gemini

    def test_pagination_limit(self, client, auth, seeded_conv_ids):
        resp = client.get(
            "/memory/conversations", headers=auth, params={"limit": 1, "offset": 0}
        )
        body = resp.json()
        assert resp.status_code == 200
        assert len(body["conversations"]) <= 1
        assert body["limit"] == 1

    def test_pagination_offset(self, client, auth, seeded_conv_ids):
        resp_all  = client.get("/memory/conversations", headers=auth, params={"limit": 100})
        resp_page = client.get("/memory/conversations", headers=auth,
                               params={"limit": 1, "offset": 1})
        all_ids  = [c["conversationId"] for c in resp_all.json()["conversations"]]
        page_ids = [c["conversationId"] for c in resp_page.json()["conversations"]]
        if len(all_ids) >= 2:
            assert page_ids[0] == all_ids[1]

    def test_no_cross_user_data_leakage(self, client, auth, other_auth, seeded_conv_ids):
        """Other user's list must not contain READ_USER_ID's conversations."""
        # Seed one conversation for the other user
        other_cid = str(uuid.uuid4())
        _write_conversation(client, other_auth, OTHER_USER_ID, other_cid)

        resp = client.get("/memory/conversations", headers=other_auth)
        returned_ids = {c["conversationId"] for c in resp.json()["conversations"]}
        for cid in seeded_conv_ids:
            assert cid not in returned_ids

    def test_dateto_without_datefrom_rejected(self, client, auth):
        resp = client.get(
            "/memory/conversations", headers=auth,
            params={"dateTo": "2024-01-31"}
        )
        assert resp.status_code == 422

    def test_date_filter_excludes_out_of_range(self, client, auth, seeded_conv_ids):
        """Conversations with today's date must not appear when filtering to 1970."""
        resp = client.get(
            "/memory/conversations", headers=auth,
            params={"dateFrom": "1970-01-01", "dateTo": "1970-01-02"}
        )
        body = resp.json()
        assert resp.status_code == 200
        returned_ids = {c["conversationId"] for c in body["conversations"]}
        for cid in seeded_conv_ids:
            assert cid not in returned_ids

    def test_summary_fields_present(self, client, auth, seeded_conv_ids):
        resp = client.get("/memory/conversations", headers=auth)
        conv = resp.json()["conversations"][0]
        for field in ("conversationId", "provider", "model", "messageCount",
                      "totalTokens", "segmentCount", "startedAt", "endedAt", "isComplete"):
            assert field in conv, f"Missing field: {field}"


# ── TestReadConversation ──────────────────────────────────────────────────────

class TestReadConversation:
    def test_requires_auth(self, client, seeded_conv_ids):
        resp = client.get(f"/memory/conversation/{seeded_conv_ids[0]}")
        assert resp.status_code in (401, 403)

    def test_returns_200_for_own_conversation(self, client, auth, seeded_conv_ids):
        resp = client.get(
            f"/memory/conversation/{seeded_conv_ids[0]}", headers=auth
        )
        assert resp.status_code == 200

    def test_404_for_nonexistent_conversation(self, client, auth):
        resp = client.get(
            f"/memory/conversation/{uuid.uuid4()}", headers=auth
        )
        assert resp.status_code == 404

    def test_404_for_other_users_conversation(self, client, auth, other_auth):
        """Cannot read another user's conversation even if the ID is known."""
        other_cid = str(uuid.uuid4())
        _write_conversation(client, other_auth, OTHER_USER_ID, other_cid)
        resp = client.get(f"/memory/conversation/{other_cid}", headers=auth)
        assert resp.status_code == 404

    def test_response_contains_verbatim_content(self, client, auth):
        cid     = str(uuid.uuid4())
        content = "Verbatim read test content — must come back unchanged."
        _write_conversation(
            client, auth, READ_USER_ID, cid,
            messages=[{
                "messageId":  str(uuid.uuid4()),
                "role":       "user",
                "content":    content,
                "timestamp":  datetime.now(timezone.utc).isoformat(),
                "tokenCount": 8,
            }],
        )
        resp = client.get(f"/memory/conversation/{cid}", headers=auth)
        assert resp.status_code == 200
        body = resp.json()
        assert any(m["content"] == content for m in body["messages"])

    def test_messages_in_chronological_order(self, client, auth):
        cid = str(uuid.uuid4())
        msgs = [
            {
                "messageId":  str(uuid.uuid4()),
                "role":       "user" if i % 2 == 0 else "assistant",
                "content":    f"Message {i}",
                "timestamp":  datetime(2024, 1, 15, 10, i, 0, tzinfo=timezone.utc).isoformat(),
                "tokenCount": 3,
            }
            for i in range(4)
        ]
        _write_conversation(client, auth, READ_USER_ID, cid, messages=msgs)

        resp = client.get(f"/memory/conversation/{cid}", headers=auth)
        returned_contents = [m["content"] for m in resp.json()["messages"]]
        assert returned_contents == [f"Message {i}" for i in range(4)]

    def test_response_shape(self, client, auth, seeded_conv_ids):
        resp = client.get(
            f"/memory/conversation/{seeded_conv_ids[0]}", headers=auth
        )
        body = resp.json()
        for field in ("conversationId", "userId", "provider", "model",
                      "messageCount", "totalTokens", "startedAt", "endedAt", "messages"):
            assert field in body, f"Missing field: {field}"

    def test_message_shape(self, client, auth, seeded_conv_ids):
        resp = client.get(
            f"/memory/conversation/{seeded_conv_ids[0]}", headers=auth
        )
        msg = resp.json()["messages"][0]
        for field in ("messageId", "role", "content", "timestamp", "tokenCount"):
            assert field in msg, f"Missing message field: {field}"

    def test_messagecount_matches_messages_list(self, client, auth):
        cid  = str(uuid.uuid4())
        msgs = [
            {
                "messageId":  str(uuid.uuid4()),
                "role":       "user" if i % 2 == 0 else "assistant",
                "content":    f"Count check message {i}",
                "timestamp":  datetime.now(timezone.utc).isoformat(),
                "tokenCount": 2,
            }
            for i in range(3)
        ]
        _write_conversation(client, auth, READ_USER_ID, cid, messages=msgs)
        resp = client.get(f"/memory/conversation/{cid}", headers=auth)
        body = resp.json()
        assert body["messageCount"] == len(body["messages"])
