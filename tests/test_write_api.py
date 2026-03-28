"""
Phase 1 — Integration tests for POST /memory/write
=====================================================
Tests run against the real Neo4j instance (not mocked).
The TestClient executes BackgroundTasks synchronously,
so we can verify Neo4j state immediately after the response.

Run with:
    uv run pytest tests/test_write_api.py -v

Test coverage:
  - Auth: missing token, invalid token, userId mismatch
  - Validation: empty messages, invalid role, invalid provider, missing content
  - Write success: 202 response, User/Conversation/Message node creation,
    verbatim content, NEXT_MESSAGE chain, User→Conversation relationship
  - Idempotency: duplicate messageId send creates no extra nodes;
    messageCount stays accurate
  - Append: writing additional messages to an existing conversation produces
    correct messageIndex sequence and extends the NEXT_MESSAGE chain
  - Segmentation: threshold trigger, verbatim segment content, NEXT_SEGMENT
    chain, segmentCount accuracy on duplicate send, segment on append
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

# ── Fixtures ──────────────────────────────────────────────────────────────────

TEST_USER_ID = "test-user-phase1"
CONV_ID      = str(uuid.uuid4())


@pytest.fixture(scope="module")
def client():
    """Shared TestClient for all tests in this module."""
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def token() -> str:
    """Valid JWT for TEST_USER_ID."""
    return create_access_token(TEST_USER_ID)


@pytest.fixture(scope="module")
def auth_headers(token) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module", autouse=True)
def cleanup_test_data():
    """
    Remove all test data created by this module after all tests complete.

    Creates its own driver directly — does NOT use the module-level singleton
    in connection.py. This prevents the cleanup from interfering with the
    application driver lifecycle managed by FastAPI's lifespan context.
    """
    yield
    driver = GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_username, settings.neo4j_password),
    )
    try:
        with driver.session(database=settings.neo4j_database) as session:
            session.run(
                """
                MATCH (u:User {userId: $userId})
                OPTIONAL MATCH (u)-[:HAS_CONVERSATION]->(c:Conversation)
                OPTIONAL MATCH (c)-[:HAS_MESSAGE]->(m:Message)
                OPTIONAL MATCH (c)-[:HAS_SEGMENT]->(s:Segment)
                DETACH DELETE u, c, m, s
                """,
                userId=TEST_USER_ID,
            )
    finally:
        driver.close()


# ── Helper ─────────────────────────────────────────────────────────────────────

def _make_messages(count: int, start_index: int = 0) -> list[dict]:
    """Generate realistic alternating user/assistant message dicts."""
    roles    = ["user", "assistant"]
    messages = []
    base_ts  = datetime(2026, 3, 5, 14, 0, 0, tzinfo=timezone.utc)

    for i in range(count):
        role = roles[i % 2]
        msg  = {
            "messageId":  str(uuid.uuid4()),
            "role":       role,
            "content":    (
                f"This is message {start_index + i} from {role}. "
                f"It contains verbatim content that must not be modified."
            ),
            "timestamp":  base_ts.replace(minute=i % 60).isoformat(),
            "tokenCount": 20 + i,
        }
        messages.append(msg)

    return messages


def _build_write_payload(
    conversation_id: str = None,
    message_count: int = 2,
    user_id: str = TEST_USER_ID,
    provider: str = "chatgpt",
) -> dict:
    return {
        "userId":         user_id,
        "conversationId": conversation_id or CONV_ID,
        "provider":       provider,
        "model":          "gpt-4o",
        "messages":       _make_messages(message_count),
    }


def _neo4j_fetch(cypher: str, **params) -> list[dict]:
    """
    Run a read query against Neo4j and return results as list of dicts.

    Creates a short-lived driver for each call — tests are integration tests
    that must observe committed state, not in-flight transactions.
    """
    driver = GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_username, settings.neo4j_password),
    )
    try:
        with driver.session(database=settings.neo4j_database) as session:
            result = session.run(cypher, **params)
            return result.data()
    finally:
        driver.close()


# ── Health check ──────────────────────────────────────────────────────────────

class TestHealth:
    def test_health_returns_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["neo4j"] == "connected"


# ── Auth — token issuance ─────────────────────────────────────────────────────

class TestAuthToken:
    def test_issue_token_success(self, client):
        resp = client.post("/auth/token", json={"userId": "some-user"})
        assert resp.status_code == 201
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["userId"] == "some-user"

    def test_issue_token_missing_userId(self, client):
        resp = client.post("/auth/token", json={})
        assert resp.status_code == 422


# ── Write — request validation ────────────────────────────────────────────────

class TestWriteValidation:
    def test_write_requires_auth(self, client):
        resp = client.post("/memory/write", json=_build_write_payload())
        assert resp.status_code == 401   # missing bearer → 401 Unauthorized

    def test_write_rejects_invalid_token(self, client):
        resp = client.post(
            "/memory/write",
            json=_build_write_payload(),
            headers={"Authorization": "Bearer not.a.real.token"},
        )
        assert resp.status_code == 401   # invalid token → 401 Unauthorized

    def test_write_rejects_mismatched_userId(self, client, auth_headers):
        payload = _build_write_payload(user_id="different-user")
        resp = client.post("/memory/write", json=payload, headers=auth_headers)
        assert resp.status_code == 403

    def test_write_rejects_empty_messages(self, client, auth_headers):
        payload = _build_write_payload()
        payload["messages"] = []
        resp = client.post("/memory/write", json=payload, headers=auth_headers)
        assert resp.status_code == 422

    def test_write_rejects_invalid_role(self, client, auth_headers):
        payload = _build_write_payload(message_count=1)
        payload["messages"][0]["role"] = "system"
        resp = client.post("/memory/write", json=payload, headers=auth_headers)
        assert resp.status_code == 422

    def test_write_rejects_invalid_provider(self, client, auth_headers):
        payload = _build_write_payload()
        payload["provider"] = "unknown_llm"
        resp = client.post("/memory/write", json=payload, headers=auth_headers)
        assert resp.status_code == 422

    def test_write_rejects_missing_content(self, client, auth_headers):
        payload = _build_write_payload(message_count=1)
        del payload["messages"][0]["content"]
        resp = client.post("/memory/write", json=payload, headers=auth_headers)
        assert resp.status_code == 422


# ── Write — successful storage ────────────────────────────────────────────────

class TestWriteSuccess:
    def test_write_returns_202(self, client, auth_headers):
        payload = _build_write_payload()
        resp = client.post("/memory/write", json=payload, headers=auth_headers)
        assert resp.status_code == 202

    def test_write_response_body(self, client, auth_headers):
        payload = _build_write_payload(message_count=3)
        resp = client.post("/memory/write", json=payload, headers=auth_headers)
        data = resp.json()
        assert data["status"] == "accepted"
        assert data["messageCount"] == 3
        assert "conversationId" in data

    def test_user_node_created_in_neo4j(self, client, auth_headers):
        payload = _build_write_payload(message_count=2)
        client.post("/memory/write", json=payload, headers=auth_headers)

        rows = _neo4j_fetch(
            "MATCH (u:User {userId: $uid}) RETURN u.userId AS id",
            uid=TEST_USER_ID,
        )
        assert len(rows) == 1
        assert rows[0]["id"] == TEST_USER_ID

    def test_conversation_node_created_in_neo4j(self, client, auth_headers):
        conv_id = str(uuid.uuid4())
        payload = _build_write_payload(conversation_id=conv_id, message_count=2)
        client.post("/memory/write", json=payload, headers=auth_headers)

        rows = _neo4j_fetch(
            "MATCH (c:Conversation {conversationId: $cid}) RETURN c.provider AS p, c.messageCount AS mc",
            cid=conv_id,
        )
        assert len(rows) == 1
        assert rows[0]["p"] == "chatgpt"
        assert rows[0]["mc"] == 2

    def test_messages_stored_verbatim(self, client, auth_headers):
        conv_id  = str(uuid.uuid4())
        messages = _make_messages(2)
        payload  = {
            "userId":         TEST_USER_ID,
            "conversationId": conv_id,
            "provider":       "claude",
            "model":          "claude-sonnet-4-6",
            "messages":       messages,
        }
        client.post("/memory/write", json=payload, headers=auth_headers)

        rows = _neo4j_fetch(
            """
            MATCH (c:Conversation {conversationId: $cid})-[:HAS_MESSAGE]->(m:Message)
            RETURN m.messageId AS id, m.content AS content, m.role AS role
            ORDER BY m.messageIndex
            """,
            cid=conv_id,
        )
        assert len(rows) == 2
        for i, row in enumerate(rows):
            # Content must be EXACTLY what was sent — verbatim
            assert row["content"] == messages[i]["content"]
            assert row["role"]    == messages[i]["role"]

    def test_next_message_chain_created(self, client, auth_headers):
        conv_id  = str(uuid.uuid4())
        messages = _make_messages(3)
        payload  = _build_write_payload(conversation_id=conv_id, message_count=3)
        payload["messages"] = messages
        client.post("/memory/write", json=payload, headers=auth_headers)

        # The chain: msg[0] → msg[1] → msg[2]
        rows = _neo4j_fetch(
            """
            MATCH (a:Message {messageId: $id0})-[:NEXT_MESSAGE]->(b:Message {messageId: $id1})
            MATCH (b)-[:NEXT_MESSAGE]->(c:Message {messageId: $id2})
            RETURN count(*) AS chain
            """,
            id0=messages[0]["messageId"],
            id1=messages[1]["messageId"],
            id2=messages[2]["messageId"],
        )
        assert rows[0]["chain"] == 1

    def test_user_conversation_relationship(self, client, auth_headers):
        conv_id = str(uuid.uuid4())
        payload = _build_write_payload(conversation_id=conv_id, message_count=2)
        client.post("/memory/write", json=payload, headers=auth_headers)

        rows = _neo4j_fetch(
            """
            MATCH (u:User {userId: $uid})-[:HAS_CONVERSATION]->(c:Conversation {conversationId: $cid})
            RETURN c.conversationId AS id
            """,
            uid=TEST_USER_ID,
            cid=conv_id,
        )
        assert len(rows) == 1

    def test_idempotent_write_same_message_id(self, client, auth_headers):
        """Sending the same messageId twice must not create a duplicate node."""
        conv_id  = str(uuid.uuid4())
        payload  = _build_write_payload(conversation_id=conv_id, message_count=2)

        client.post("/memory/write", json=payload, headers=auth_headers)
        client.post("/memory/write", json=payload, headers=auth_headers)   # duplicate

        rows = _neo4j_fetch(
            """
            MATCH (c:Conversation {conversationId: $cid})-[:HAS_MESSAGE]->(m:Message)
            RETURN count(m) AS total
            """,
            cid=conv_id,
        )
        assert rows[0]["total"] == 2   # still 2, not 4


# ── messageCount accuracy ─────────────────────────────────────────────────────

class TestMessageCountAccuracy:
    def test_message_count_correct_on_first_write(self, client, auth_headers):
        """messageCount reflects the number of messages actually stored."""
        conv_id = str(uuid.uuid4())
        payload = _build_write_payload(conversation_id=conv_id, message_count=3)
        client.post("/memory/write", json=payload, headers=auth_headers)

        rows = _neo4j_fetch(
            "MATCH (c:Conversation {conversationId: $cid}) RETURN c.messageCount AS mc",
            cid=conv_id,
        )
        assert rows[0]["mc"] == 3

    def test_message_count_unchanged_on_duplicate_send(self, client, auth_headers):
        """
        Sending the exact same batch twice must not inflate messageCount.
        Root cause of the fixed bug: ON MATCH SET c.messageCount += incoming
        incremented even when no new messages were created.
        """
        conv_id = str(uuid.uuid4())
        payload = _build_write_payload(conversation_id=conv_id, message_count=2)

        client.post("/memory/write", json=payload, headers=auth_headers)
        client.post("/memory/write", json=payload, headers=auth_headers)  # resend

        rows = _neo4j_fetch(
            "MATCH (c:Conversation {conversationId: $cid}) RETURN c.messageCount AS mc",
            cid=conv_id,
        )
        assert rows[0]["mc"] == 2   # must still be 2, not 4

    def test_message_count_increments_correctly_on_append(self, client, auth_headers):
        """
        Appending new messages to an existing conversation increments
        messageCount only by the actual number of new messages.
        """
        conv_id  = str(uuid.uuid4())
        batch1   = _make_messages(2)
        batch2   = _make_messages(3)   # all-new messageIds

        payload1 = {
            "userId": TEST_USER_ID, "conversationId": conv_id,
            "provider": "chatgpt", "model": "gpt-4o", "messages": batch1,
        }
        payload2 = {
            "userId": TEST_USER_ID, "conversationId": conv_id,
            "provider": "chatgpt", "model": "gpt-4o", "messages": batch2,
        }

        client.post("/memory/write", json=payload1, headers=auth_headers)
        client.post("/memory/write", json=payload2, headers=auth_headers)

        rows = _neo4j_fetch(
            "MATCH (c:Conversation {conversationId: $cid}) RETURN c.messageCount AS mc",
            cid=conv_id,
        )
        assert rows[0]["mc"] == 5   # 2 + 3


# ── messageIndex and chain continuity on append ───────────────────────────────

class TestAppendBehavior:
    def test_appended_messages_get_sequential_indices(self, client, auth_headers):
        """
        Messages appended in a second write get indices immediately after
        the last index of the first write.
        """
        conv_id = str(uuid.uuid4())
        batch1  = _make_messages(3)
        batch2  = _make_messages(2)

        payload1 = {
            "userId": TEST_USER_ID, "conversationId": conv_id,
            "provider": "chatgpt", "model": "gpt-4o", "messages": batch1,
        }
        payload2 = {
            "userId": TEST_USER_ID, "conversationId": conv_id,
            "provider": "chatgpt", "model": "gpt-4o", "messages": batch2,
        }

        client.post("/memory/write", json=payload1, headers=auth_headers)
        client.post("/memory/write", json=payload2, headers=auth_headers)

        rows = _neo4j_fetch(
            """
            MATCH (c:Conversation {conversationId: $cid})-[:HAS_MESSAGE]->(m:Message)
            RETURN m.messageId AS mid, m.messageIndex AS idx
            ORDER BY m.messageIndex
            """,
            cid=conv_id,
        )
        assert len(rows) == 5
        indices = [r["idx"] for r in rows]
        assert indices == [0, 1, 2, 3, 4], f"Expected [0,1,2,3,4], got {indices}"

    def test_next_message_chain_spans_batches(self, client, auth_headers):
        """
        After two consecutive writes, the NEXT_MESSAGE chain connects all
        messages in order across the batch boundary.
        """
        conv_id = str(uuid.uuid4())
        batch1  = _make_messages(2)
        batch2  = _make_messages(2)

        payload1 = {
            "userId": TEST_USER_ID, "conversationId": conv_id,
            "provider": "chatgpt", "model": "gpt-4o", "messages": batch1,
        }
        payload2 = {
            "userId": TEST_USER_ID, "conversationId": conv_id,
            "provider": "chatgpt", "model": "gpt-4o", "messages": batch2,
        }

        client.post("/memory/write", json=payload1, headers=auth_headers)
        client.post("/memory/write", json=payload2, headers=auth_headers)

        # Expect: b1[0] → b1[1] → b2[0] → b2[1]
        rows = _neo4j_fetch(
            """
            MATCH (a:Message {messageId: $id0})-[:NEXT_MESSAGE]->(b:Message {messageId: $id1})
            MATCH (b)-[:NEXT_MESSAGE]->(c:Message {messageId: $id2})
            MATCH (c)-[:NEXT_MESSAGE]->(d:Message {messageId: $id3})
            RETURN count(*) AS chain
            """,
            id0=batch1[0]["messageId"],
            id1=batch1[1]["messageId"],
            id2=batch2[0]["messageId"],
            id3=batch2[1]["messageId"],
        )
        assert rows[0]["chain"] == 1

    def test_duplicate_resend_does_not_corrupt_chain(self, client, auth_headers):
        """
        Resending the same batch does not add spurious NEXT_MESSAGE edges that
        would corrupt the chain order.
        The fixed bug: base_offset on resend pointed to the last message in the
        batch (not a prior message), so prev→first was created backwards.
        """
        conv_id = str(uuid.uuid4())
        batch   = _make_messages(2)

        payload = {
            "userId": TEST_USER_ID, "conversationId": conv_id,
            "provider": "chatgpt", "model": "gpt-4o", "messages": batch,
        }

        client.post("/memory/write", json=payload, headers=auth_headers)
        client.post("/memory/write", json=payload, headers=auth_headers)  # resend

        # The valid edge is batch[0] → batch[1]. There must be no reverse edge.
        forward_rows = _neo4j_fetch(
            """
            MATCH (a:Message {messageId: $id0})-[:NEXT_MESSAGE]->(b:Message {messageId: $id1})
            RETURN count(*) AS n
            """,
            id0=batch[0]["messageId"],
            id1=batch[1]["messageId"],
        )
        backward_rows = _neo4j_fetch(
            """
            MATCH (a:Message {messageId: $id1})-[:NEXT_MESSAGE]->(b:Message {messageId: $id0})
            RETURN count(*) AS n
            """,
            id0=batch[0]["messageId"],
            id1=batch[1]["messageId"],
        )
        assert forward_rows[0]["n"] == 1, "Forward edge batch[0]→batch[1] must exist"
        assert backward_rows[0]["n"] == 0, "Backward edge batch[1]→batch[0] must NOT exist"

    def test_message_indices_are_unique_within_conversation(self, client, auth_headers):
        """
        Every message in a conversation must have a unique messageIndex.
        This would fail under the TOCTOU race if two overlapping batches
        assigned the same base offset.
        """
        conv_id = str(uuid.uuid4())
        # Two sequential writes (simulating the common append pattern)
        batch1 = _make_messages(4)
        batch2 = _make_messages(4)

        for batch in (batch1, batch2):
            payload = {
                "userId": TEST_USER_ID, "conversationId": conv_id,
                "provider": "chatgpt", "model": "gpt-4o", "messages": batch,
            }
            client.post("/memory/write", json=payload, headers=auth_headers)

        rows = _neo4j_fetch(
            """
            MATCH (c:Conversation {conversationId: $cid})-[:HAS_MESSAGE]->(m:Message)
            RETURN m.messageIndex AS idx
            ORDER BY idx
            """,
            cid=conv_id,
        )
        indices = [r["idx"] for r in rows]
        assert len(indices) == 8
        assert len(set(indices)) == 8, f"Duplicate indices found: {indices}"
        assert indices == list(range(8)), f"Indices not sequential: {indices}"


# ── Segmentation ──────────────────────────────────────────────────────────────

class TestSegmentation:
    def test_no_segment_below_threshold(self, client, auth_headers):
        """Fewer than 20 messages → no Segment node created."""
        conv_id = str(uuid.uuid4())
        payload = _build_write_payload(conversation_id=conv_id, message_count=5)
        client.post("/memory/write", json=payload, headers=auth_headers)

        rows = _neo4j_fetch(
            "MATCH (c:Conversation {conversationId: $cid})-[:HAS_SEGMENT]->(s) RETURN count(s) AS n",
            cid=conv_id,
        )
        assert rows[0]["n"] == 0

    def test_segment_created_at_threshold(self, client, auth_headers):
        """Exactly 20 messages → 1 Segment node created."""
        conv_id = str(uuid.uuid4())
        payload = _build_write_payload(conversation_id=conv_id, message_count=20)
        client.post("/memory/write", json=payload, headers=auth_headers)

        rows = _neo4j_fetch(
            "MATCH (c:Conversation {conversationId: $cid})-[:HAS_SEGMENT]->(s) RETURN count(s) AS n",
            cid=conv_id,
        )
        assert rows[0]["n"] == 1

    def test_segment_contains_verbatim_content(self, client, auth_headers):
        """Segment.content must contain the actual message text."""
        conv_id  = str(uuid.uuid4())
        messages = _make_messages(20)
        payload  = {
            "userId":         TEST_USER_ID,
            "conversationId": conv_id,
            "provider":       "chatgpt",
            "model":          "gpt-4o",
            "messages":       messages,
        }
        client.post("/memory/write", json=payload, headers=auth_headers)

        rows = _neo4j_fetch(
            "MATCH (c:Conversation {conversationId: $cid})-[:HAS_SEGMENT]->(s) RETURN s.content AS content",
            cid=conv_id,
        )
        assert len(rows) == 1
        segment_content = rows[0]["content"]

        # Every message's content must appear verbatim in the segment
        for msg in messages:
            assert msg["content"] in segment_content

    def test_two_segments_at_40_messages(self, client, auth_headers):
        """40 messages → 2 Segment nodes, linked by NEXT_SEGMENT."""
        conv_id = str(uuid.uuid4())
        payload = _build_write_payload(conversation_id=conv_id, message_count=40)
        client.post("/memory/write", json=payload, headers=auth_headers)

        rows = _neo4j_fetch(
            "MATCH (c:Conversation {conversationId: $cid})-[:HAS_SEGMENT]->(s) RETURN count(s) AS n",
            cid=conv_id,
        )
        assert rows[0]["n"] == 2

        link_rows = _neo4j_fetch(
            """
            MATCH (c:Conversation {conversationId: $cid})-[:HAS_SEGMENT]->(s0:Segment {segmentIndex: 0})
            MATCH (s0)-[:NEXT_SEGMENT]->(s1:Segment {segmentIndex: 1})
            RETURN count(*) AS linked
            """,
            cid=conv_id,
        )
        assert link_rows[0]["linked"] == 1

    def test_segment_count_unchanged_on_duplicate_send(self, client, auth_headers):
        """
        Resending the same 20 messages must not create a second Segment node.
        Root cause of the fixed bug: CREATE instead of MERGE allowed duplicate
        Segment nodes with different UUIDs at the same segmentIndex.
        """
        conv_id  = str(uuid.uuid4())
        payload  = _build_write_payload(conversation_id=conv_id, message_count=20)

        client.post("/memory/write", json=payload, headers=auth_headers)
        client.post("/memory/write", json=payload, headers=auth_headers)  # resend

        rows = _neo4j_fetch(
            "MATCH (c:Conversation {conversationId: $cid})-[:HAS_SEGMENT]->(s) RETURN count(s) AS n",
            cid=conv_id,
        )
        assert rows[0]["n"] == 1, "Duplicate send must not create a second Segment"

    def test_segment_count_property_accurate(self, client, auth_headers):
        """
        Conversation.segmentCount must match the actual number of Segment nodes.
        Tests the fix where segmentCount was derived from actual count instead
        of an error-prone increment.
        """
        conv_id = str(uuid.uuid4())
        payload = _build_write_payload(conversation_id=conv_id, message_count=40)
        client.post("/memory/write", json=payload, headers=auth_headers)

        rows = _neo4j_fetch(
            """
            MATCH (c:Conversation {conversationId: $cid})
            OPTIONAL MATCH (c)-[:HAS_SEGMENT]->(s:Segment)
            RETURN c.segmentCount AS sc, count(s) AS actual
            """,
            cid=conv_id,
        )
        assert rows[0]["sc"] == rows[0]["actual"] == 2

    def test_segment_count_accurate_after_duplicate_send(self, client, auth_headers):
        """
        segmentCount must stay at 1 after resending the same 20 messages.
        Tests that the fixed segmentCount update (actual count, not increment)
        remains correct under idempotent conditions.
        """
        conv_id = str(uuid.uuid4())
        payload = _build_write_payload(conversation_id=conv_id, message_count=20)

        client.post("/memory/write", json=payload, headers=auth_headers)
        client.post("/memory/write", json=payload, headers=auth_headers)

        rows = _neo4j_fetch(
            """
            MATCH (c:Conversation {conversationId: $cid})
            OPTIONAL MATCH (c)-[:HAS_SEGMENT]->(s:Segment)
            RETURN c.segmentCount AS sc, count(s) AS actual
            """,
            cid=conv_id,
        )
        assert rows[0]["sc"] == rows[0]["actual"] == 1

    def test_segment_created_when_threshold_crossed_on_append(self, client, auth_headers):
        """
        Writing 15 messages then 10 more crosses the 20-message threshold.
        The segment should be created after the second write.
        """
        conv_id = str(uuid.uuid4())
        batch1  = _make_messages(15)
        batch2  = _make_messages(10)

        for batch in (batch1, batch2):
            payload = {
                "userId": TEST_USER_ID, "conversationId": conv_id,
                "provider": "chatgpt", "model": "gpt-4o", "messages": batch,
            }
            client.post("/memory/write", json=payload, headers=auth_headers)

        rows = _neo4j_fetch(
            "MATCH (c:Conversation {conversationId: $cid})-[:HAS_SEGMENT]->(s) RETURN count(s) AS n",
            cid=conv_id,
        )
        assert rows[0]["n"] == 1, "Threshold crossed across two writes must produce one segment"

    def test_contains_message_links_all_messages_in_segment(self, client, auth_headers):
        """
        Every message in the segment range must have a CONTAINS_MESSAGE link.
        Tests the UNWIND fix that replaced the N+1 loop.
        """
        conv_id  = str(uuid.uuid4())
        messages = _make_messages(20)
        payload  = {
            "userId":         TEST_USER_ID,
            "conversationId": conv_id,
            "provider":       "chatgpt",
            "model":          "gpt-4o",
            "messages":       messages,
        }
        client.post("/memory/write", json=payload, headers=auth_headers)

        rows = _neo4j_fetch(
            """
            MATCH (c:Conversation {conversationId: $cid})-[:HAS_SEGMENT]->(s:Segment)
            MATCH (s)-[:CONTAINS_MESSAGE]->(m:Message)
            RETURN count(m) AS linked
            """,
            cid=conv_id,
        )
        assert rows[0]["linked"] == 20, "All 20 messages must be linked to the segment"
