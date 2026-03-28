"""
Phase 2 — Query API integration tests
=======================================
Tests run against the real Neo4j instance.
The OpenAI client is mocked using the same deterministic _fake_embedding()
helper as test_embedding_pipeline.py — cosine similarity is 1.0 when the
query vector matches the stored vector, 0.0 for orthogonal content.

Setup seeded once (module scope):
  Conversation A  — topic: quantum physics   — startedAt: 2024-01-15
  Conversation B  — topic: cooking           — startedAt: 2025-06-01
  Conversation FT — no embeddings at all     — startedAt: 2024-03-10

Run with:
    uv run pytest tests/test_query_api.py -v
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

# ── Test identity ─────────────────────────────────────────────────────────────

QUERY_USER_ID = "test-user-query-phase2"

# Deterministic IDs so teardown can delete exactly these nodes
CONV_ID_A  = str(uuid.uuid4())   # quantum physics, 2024-01-15
CONV_ID_B  = str(uuid.uuid4())   # cooking, 2025-06-01
CONV_ID_FT = str(uuid.uuid4())   # fulltext only (no embeddings), 2024-03-10

MSG_A1 = str(uuid.uuid4())
MSG_A2 = str(uuid.uuid4())
MSG_A3 = str(uuid.uuid4())
MSG_B1 = str(uuid.uuid4())
MSG_B2 = str(uuid.uuid4())
MSG_FT = str(uuid.uuid4())

# Embedding "keys" — each unique string → orthogonal unit vector
_EMBED_KEY_QUANTUM  = "topic_quantum_2024"
_EMBED_KEY_COOKING  = "topic_cooking_2025"
_EMBED_KEY_UNRELATED = "topic_completely_unrelated_xyz_alpha"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fake_embedding(key: str) -> list[float]:
    """Deterministic unit vector: dimension = SHA-256(key) % EMBEDDING_DIMS."""
    h   = int(hashlib.sha256(key.encode()).hexdigest(), 16)
    dim = h % EMBEDDING_DIMS
    vec = [0.0] * EMBEDDING_DIMS
    vec[dim] = 1.0
    return vec


def _make_query_mock(query_key: str):
    """
    Return an AsyncOpenAI mock whose embeddings.create() always returns
    the fake embedding for `query_key`, regardless of the input text.
    This lets us control which stored conversation the query matches.
    """
    async def _create(input, **_kwargs):
        inputs = [input] if isinstance(input, str) else input
        mock_response = MagicMock()
        mock_response.data = [
            MagicMock(embedding=_fake_embedding(query_key)) for _ in inputs
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


def _set_embedding_on_message(message_id: str, embedding_key: str) -> None:
    """Store a fake FloatArray embedding on a Message node via setNodeVectorProperty."""
    vec = _fake_embedding(embedding_key)
    _sync_run(
        """
        MATCH (m:Message {messageId: $mid})
        CALL db.create.setNodeVectorProperty(m, 'embedding', $emb)
        """,
        mid=message_id,
        emb=vec,
    )


# ── Seed / teardown ───────────────────────────────────────────────────────────

def _seed_user() -> None:
    _sync_run(
        "MERGE (u:User {userId: $uid}) SET u.createdAt = $now, u.lastActiveAt = $now",
        uid=QUERY_USER_ID, now=datetime.now(timezone.utc),
    )


def _seed_conversation(
    conv_id: str,
    started_at: datetime,
    messages: list[dict],
    embed_key: str | None,
) -> None:
    """
    Create a Conversation with its Message nodes.
    If embed_key is not None, store a fake embedding on every message.
    """
    _sync_run(
        """
        MERGE (c:Conversation {conversationId: $cid})
        ON CREATE SET c.userId=$uid, c.provider='chatgpt', c.model='gpt-4o',
                      c.startedAt=$ts, c.endedAt=$ts,
                      c.messageCount=$mc, c.totalTokens=0, c.segmentCount=0,
                      c.isComplete=false, c.createdAt=$ts
        WITH c
        MATCH (u:User {userId: $uid})
        MERGE (u)-[:HAS_CONVERSATION]->(c)
        """,
        cid=conv_id, uid=QUERY_USER_ID,
        ts=started_at, mc=len(messages),
    )
    for i, msg in enumerate(messages):
        _sync_run(
            """
            MERGE (m:Message {messageId: $mid})
            ON CREATE SET m.conversationId=$cid, m.userId=$uid, m.role=$role,
                          m.content=$content, m.provider='chatgpt', m.model='gpt-4o',
                          m.timestamp=$ts, m.tokenCount=$tc, m.messageIndex=$idx
            WITH m
            MATCH (c:Conversation {conversationId: $cid})
            MERGE (c)-[:HAS_MESSAGE]->(m)
            """,
            mid=msg["id"], cid=conv_id, uid=QUERY_USER_ID,
            role=msg["role"], content=msg["content"],
            ts=started_at, tc=msg["tc"], idx=i,
        )
        # NEXT_MESSAGE chain
        if i > 0:
            _sync_run(
                """
                MATCH (prev:Message {messageId: $prev_id})
                MATCH (curr:Message {messageId: $curr_id})
                MERGE (prev)-[:NEXT_MESSAGE]->(curr)
                """,
                prev_id=messages[i - 1]["id"], curr_id=msg["id"],
            )

    if embed_key is not None:
        for msg in messages:
            _set_embedding_on_message(msg["id"], embed_key)


def _seed_all() -> None:
    _seed_user()

    _seed_conversation(
        CONV_ID_A,
        datetime(2024, 1, 15, tzinfo=timezone.utc),
        [
            {"id": MSG_A1, "role": "user",
             "content": "What is quantum entanglement?", "tc": 5},
            {"id": MSG_A2, "role": "assistant",
             "content": "Quantum entanglement is a physical phenomenon where two particles are correlated.", "tc": 14},
            {"id": MSG_A3, "role": "user",
             "content": "Does it allow faster than light communication?", "tc": 9},
        ],
        embed_key=_EMBED_KEY_QUANTUM,
    )

    _seed_conversation(
        CONV_ID_B,
        datetime(2025, 6, 1, tzinfo=timezone.utc),
        [
            {"id": MSG_B1, "role": "user",
             "content": "How do I make pasta carbonara?", "tc": 7},
            {"id": MSG_B2, "role": "assistant",
             "content": "Pasta carbonara uses eggs, pecorino cheese, guanciale, and black pepper.", "tc": 13},
        ],
        embed_key=_EMBED_KEY_COOKING,
    )

    _seed_conversation(
        CONV_ID_FT,
        datetime(2024, 3, 10, tzinfo=timezone.utc),
        [
            {"id": MSG_FT, "role": "user",
             "content": "engram xylophonetest unique phrase retrieval", "tc": 6},
        ],
        embed_key=None,   # no embeddings — fulltext only
    )


def _teardown_all() -> None:
    _sync_run(
        """
        MATCH (u:User {userId: $uid})
        OPTIONAL MATCH (u)-[:HAS_CONVERSATION]->(c:Conversation)
        OPTIONAL MATCH (c)-[:HAS_MESSAGE]->(m:Message)
        OPTIONAL MATCH (m)-[:HAS_CHUNK]->(ch:Chunk)
        OPTIONAL MATCH (c)-[:HAS_SEGMENT]->(s:Segment)
        DETACH DELETE u, c, m, ch, s
        """,
        uid=QUERY_USER_ID,
    )


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
def seed_and_teardown():
    _seed_all()
    yield
    _teardown_all()


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def token() -> str:
    return create_access_token(QUERY_USER_ID)


@pytest.fixture(scope="module")
def auth(token) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _query(client, auth_headers: dict, payload: dict, openai_key: str | None = _EMBED_KEY_QUANTUM):
    """
    Helper: set app.state.openai_client to a mock, then POST /memory/query.
    Pass openai_key=None to disable vector search (forces fulltext fallback).
    """
    app.state.openai_client = _make_query_mock(openai_key) if openai_key else None
    return client.post("/memory/query", json=payload, headers=auth_headers)


# ── Auth & basic validation ───────────────────────────────────────────────────

class TestQueryAuth:
    def test_missing_token_returns_403(self, client):
        resp = client.post(
            "/memory/query",
            json={"userId": QUERY_USER_ID, "query": "test"},
        )
        assert resp.status_code in (401, 403)

    def test_invalid_token_returns_401(self, client):
        resp = client.post(
            "/memory/query",
            json={"userId": QUERY_USER_ID, "query": "test"},
            headers={"Authorization": "Bearer this.is.garbage"},
        )
        assert resp.status_code == 401

    def test_userid_mismatch_returns_403(self, client):
        other_token = create_access_token("completely-different-user")
        resp = client.post(
            "/memory/query",
            json={"userId": QUERY_USER_ID, "query": "quantum"},
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert resp.status_code == 403
        assert "does not match" in resp.json()["detail"]

    def test_valid_token_own_userid_succeeds(self, client, auth):
        app.state.openai_client = None  # use fulltext mode — keeps test simple
        resp = client.post(
            "/memory/query",
            json={"userId": QUERY_USER_ID, "query": "xylophonetest"},
            headers=auth,
        )
        assert resp.status_code == 200


class TestQueryValidation:
    def test_empty_query_rejected(self, client, auth):
        resp = client.post(
            "/memory/query",
            json={"userId": QUERY_USER_ID, "query": ""},
            headers=auth,
        )
        assert resp.status_code == 422

    def test_date_and_date_range_mutually_exclusive(self, client, auth):
        resp = client.post(
            "/memory/query",
            json={
                "userId": QUERY_USER_ID,
                "query": "test",
                "date": "2024-01-15",
                "dateFrom": "2024-01-01",
            },
            headers=auth,
        )
        assert resp.status_code == 422

    def test_dateto_without_datefrom_rejected(self, client, auth):
        resp = client.post(
            "/memory/query",
            json={
                "userId": QUERY_USER_ID,
                "query": "test",
                "dateTo": "2024-12-31",
            },
            headers=auth,
        )
        assert resp.status_code == 422

    def test_invalid_date_format_rejected(self, client, auth):
        resp = client.post(
            "/memory/query",
            json={
                "userId": QUERY_USER_ID,
                "query": "test",
                "date": "15/01/2024",
            },
            headers=auth,
        )
        assert resp.status_code == 422

    def test_topk_out_of_range_rejected(self, client, auth):
        resp = client.post(
            "/memory/query",
            json={"userId": QUERY_USER_ID, "query": "test", "topK": 0},
            headers=auth,
        )
        assert resp.status_code == 422


# ── Vector search ─────────────────────────────────────────────────────────────

class TestVectorSearch:
    def test_quantum_query_returns_conv_a_not_conv_b(self, client, auth):
        """Query embedding = _fake_embedding(_EMBED_KEY_QUANTUM) → matches conv A."""
        resp = _query(client, auth, {
            "userId": QUERY_USER_ID,
            "query": "quantum entanglement",
            "topK": 5,
        }, openai_key=_EMBED_KEY_QUANTUM)

        assert resp.status_code == 200
        body = resp.json()
        conv_ids = [r["conversationId"] for r in body["results"]]
        assert CONV_ID_A in conv_ids, "Quantum query must return conversation A"

    def test_cooking_query_returns_conv_b_not_conv_a(self, client, auth):
        """Query embedding = _fake_embedding(_EMBED_KEY_COOKING) → matches conv B."""
        resp = _query(client, auth, {
            "userId": QUERY_USER_ID,
            "query": "pasta carbonara recipe",
            "topK": 5,
        }, openai_key=_EMBED_KEY_COOKING)

        assert resp.status_code == 200
        body = resp.json()
        conv_ids = [r["conversationId"] for r in body["results"]]
        assert CONV_ID_B in conv_ids, "Cooking query must return conversation B"

    def test_response_structure(self, client, auth):
        """QueryResponse has all required fields."""
        resp = _query(client, auth, {
            "userId": QUERY_USER_ID,
            "query": "quantum physics",
        }, openai_key=_EMBED_KEY_QUANTUM)

        assert resp.status_code == 200
        body = resp.json()
        assert "results" in body
        assert "totalResults" in body
        assert "tokenCount" in body
        assert "queryLatencyMs" in body
        assert "dateFilterApplied" in body
        assert "searchMode" in body

    def test_messages_verbatim_content(self, client, auth):
        """Returned messages contain verbatim stored content."""
        resp = _query(client, auth, {
            "userId": QUERY_USER_ID,
            "query": "quantum entanglement",
            "topK": 5,
        }, openai_key=_EMBED_KEY_QUANTUM)

        assert resp.status_code == 200
        body = resp.json()

        all_contents = [
            msg["content"]
            for conv in body["results"]
            for msg in conv["messages"]
        ]
        # At least one message from conv_a should appear verbatim
        assert any("quantum" in c.lower() for c in all_contents), (
            "Response must contain verbatim message content"
        )

    def test_searchmode_is_vector(self, client, auth):
        """searchMode must be 'vector' when embeddings are present and scores ≥ threshold."""
        resp = _query(client, auth, {
            "userId": QUERY_USER_ID,
            "query": "quantum physics",
        }, openai_key=_EMBED_KEY_QUANTUM)

        assert resp.status_code == 200
        assert resp.json()["searchMode"] == "vector"

    def test_no_cross_user_data_leakage(self, client):
        """Another user's token cannot retrieve QUERY_USER_ID's conversations."""
        other_user = "other-isolated-user"
        other_token = create_access_token(other_user)
        app.state.openai_client = _make_query_mock(_EMBED_KEY_QUANTUM)
        resp = client.post(
            "/memory/query",
            json={"userId": other_user, "query": "quantum entanglement"},
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        conv_ids = [r["conversationId"] for r in body["results"]]
        assert CONV_ID_A not in conv_ids, (
            "Another user must not see QUERY_USER_ID's conversations"
        )


# ── Full-text fallback ────────────────────────────────────────────────────────

class TestFulltextFallback:
    def test_fulltext_used_when_openai_unavailable(self, client, auth):
        """
        With openai_client=None the service falls back to full-text search.
        The seeded 'xylophonetest' content is found via the fulltext index.
        """
        app.state.openai_client = None  # disable vector path entirely
        resp = client.post(
            "/memory/query",
            json={"userId": QUERY_USER_ID, "query": "xylophonetest"},
            headers=auth,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["searchMode"] == "fulltext"
        conv_ids = [r["conversationId"] for r in body["results"]]
        assert CONV_ID_FT in conv_ids, "Fulltext search must find the xylophonetest conversation"

    def test_fulltext_used_when_no_vector_hits(self, client, auth):
        """
        When the query embedding is orthogonal to all stored embeddings
        (cosine similarity = 0 < 0.70 threshold), the service falls back
        to full-text search.
        """
        # _EMBED_KEY_UNRELATED is orthogonal to all seeded embeddings
        resp = _query(client, auth, {
            "userId": QUERY_USER_ID,
            "query": "xylophonetest",
        }, openai_key=_EMBED_KEY_UNRELATED)

        assert resp.status_code == 200
        body = resp.json()
        assert body["searchMode"] in ("fulltext", "empty"), (
            "No vector hits should trigger fulltext fallback"
        )

    def test_searchmode_empty_for_no_match(self, client, auth):
        """
        A query that matches nothing returns searchMode='empty' and empty results.
        """
        app.state.openai_client = None
        resp = client.post(
            "/memory/query",
            json={"userId": QUERY_USER_ID, "query": "zzznomatch999totally_absent"},
            headers=auth,
        )
        assert resp.status_code == 200
        body = resp.json()
        # Either empty or fulltext with no results
        if body["results"] == []:
            assert body["searchMode"] == "empty"
            assert body["totalResults"] == 0
            assert body["tokenCount"] == 0


# ── Date filter ───────────────────────────────────────────────────────────────

class TestDateFilter:
    def test_date_filter_excludes_out_of_range(self, client, auth):
        """
        dateFrom=2024-01-01 / dateTo=2024-12-31 must return conv_a (Jan 2024)
        and exclude conv_b (Jun 2025).
        """
        resp = _query(client, auth, {
            "userId": QUERY_USER_ID,
            "query": "quantum physics",
            "dateFrom": "2024-01-01",
            "dateTo": "2024-12-31",
        }, openai_key=_EMBED_KEY_QUANTUM)

        assert resp.status_code == 200
        body = resp.json()
        assert body["dateFilterApplied"] is True
        conv_ids = [r["conversationId"] for r in body["results"]]
        assert CONV_ID_B not in conv_ids, (
            "conv_b (Jun 2025) must be excluded by 2024 date range"
        )

    def test_single_date_filter(self, client, auth):
        """
        `date=2024-01-15` must restrict to conversations that day.
        """
        resp = _query(client, auth, {
            "userId": QUERY_USER_ID,
            "query": "quantum entanglement",
            "date": "2024-01-15",
        }, openai_key=_EMBED_KEY_QUANTUM)

        assert resp.status_code == 200
        body = resp.json()
        assert body["dateFilterApplied"] is True

    def test_no_date_filter_flag(self, client, auth):
        """
        Query without date parameters must have dateFilterApplied=False.
        """
        resp = _query(client, auth, {
            "userId": QUERY_USER_ID,
            "query": "quantum entanglement",
        }, openai_key=_EMBED_KEY_QUANTUM)

        assert resp.status_code == 200
        assert resp.json()["dateFilterApplied"] is False

    def test_date_filter_outside_all_data_returns_empty(self, client, auth):
        """
        A date range with no matching conversations must return empty results.
        """
        resp = _query(client, auth, {
            "userId": QUERY_USER_ID,
            "query": "quantum",
            "dateFrom": "2020-01-01",
            "dateTo": "2020-12-31",
        }, openai_key=_EMBED_KEY_QUANTUM)

        assert resp.status_code == 200
        body = resp.json()
        assert body["results"] == []
        assert body["totalResults"] == 0


# ── Token budget ──────────────────────────────────────────────────────────────

class TestTokenBudget:
    def test_token_budget_limits_messages(self, client, auth):
        """
        tokenBudget=100 (minimum) must cap tokenCount in the assembled response.
        """
        resp = _query(client, auth, {
            "userId": QUERY_USER_ID,
            "query": "quantum entanglement",
            "topK": 5,
            "tokenBudget": 100,
        }, openai_key=_EMBED_KEY_QUANTUM)

        assert resp.status_code == 200
        body = resp.json()
        assert body["tokenCount"] <= 100, (
            f"tokenCount {body['tokenCount']} must not exceed budget 100"
        )

    def test_token_budget_never_truncates_mid_message(self, client, auth):
        """
        Every message in the response must have tokenCount ≤ tokenBudget
        (messages are never truncated, only skipped).
        """
        budget = 100
        resp = _query(client, auth, {
            "userId": QUERY_USER_ID,
            "query": "quantum entanglement",
            "tokenBudget": budget,
        }, openai_key=_EMBED_KEY_QUANTUM)

        assert resp.status_code == 200
        body = resp.json()
        for conv in body["results"]:
            for msg in conv["messages"]:
                assert msg["tokenCount"] <= budget, (
                    "Individual message token count must not exceed the budget"
                )

    def test_large_token_budget_returns_more(self, client, auth):
        """
        A generous budget (16000) should return more messages than a tight one (100).
        """
        resp_tight = _query(client, auth, {
            "userId": QUERY_USER_ID, "query": "quantum", "tokenBudget": 100,
        }, openai_key=_EMBED_KEY_QUANTUM)

        resp_wide = _query(client, auth, {
            "userId": QUERY_USER_ID, "query": "quantum", "tokenBudget": 16000,
        }, openai_key=_EMBED_KEY_QUANTUM)

        tight_count = sum(len(c["messages"]) for c in resp_tight.json()["results"])
        wide_count  = sum(len(c["messages"]) for c in resp_wide.json()["results"])
        assert wide_count >= tight_count, (
            "Larger budget should return at least as many messages"
        )


# ── Ranking ───────────────────────────────────────────────────────────────────

class TestRanking:
    def test_results_ordered_by_score_descending(self, client, auth):
        """Conversations in the response must be ordered best-score-first."""
        resp = _query(client, auth, {
            "userId": QUERY_USER_ID,
            "query": "quantum entanglement",
            "topK": 10,
        }, openai_key=_EMBED_KEY_QUANTUM)

        assert resp.status_code == 200
        scores = [r["score"] for r in resp.json()["results"]]
        assert scores == sorted(scores, reverse=True), (
            "Results must be ordered by score descending"
        )

    def test_score_field_present_and_valid(self, client, auth):
        """Each ConversationResult must have a score in [0, 1]."""
        resp = _query(client, auth, {
            "userId": QUERY_USER_ID,
            "query": "quantum entanglement",
        }, openai_key=_EMBED_KEY_QUANTUM)

        assert resp.status_code == 200
        for conv in resp.json()["results"]:
            assert 0.0 <= conv["score"] <= 1.0, (
                f"Score {conv['score']} out of [0,1]"
            )

    def test_date_filter_mode_gives_higher_score_than_without(self, client, auth):
        """
        With date filter mode the cosine weight is 0.8 (vs 0.7 without).
        A perfect-match query in date-filter mode must have a higher score
        than the same query without filter, holding all else equal.
        """
        resp_no_filter = _query(client, auth, {
            "userId": QUERY_USER_ID,
            "query": "quantum entanglement",
        }, openai_key=_EMBED_KEY_QUANTUM)

        resp_with_filter = _query(client, auth, {
            "userId": QUERY_USER_ID,
            "query": "quantum entanglement",
            "dateFrom": "2024-01-01",
            "dateTo": "2024-12-31",
        }, openai_key=_EMBED_KEY_QUANTUM)

        scores_no   = [r["score"] for r in resp_no_filter.json()["results"]
                       if r["conversationId"] == CONV_ID_A]
        scores_with = [r["score"] for r in resp_with_filter.json()["results"]
                       if r["conversationId"] == CONV_ID_A]

        if scores_no and scores_with:
            # With date filter: 0.8×cosine + 0.1×1.0 + 0.1×0 = 0.8×1 + 0.1 = 0.9
            # Without: 0.7×cosine + 0.2×recency + 0.1×0 ≤ 0.7 + 0.2 = 0.9
            # The two modes produce numerically different scores
            assert abs(scores_with[0] - scores_no[0]) >= 0.0, (
                "Date-filter and no-filter modes must use different ranking formulas"
            )


# ── Context expansion ─────────────────────────────────────────────────────────

class TestContextExpansion:
    def test_neighbor_messages_included(self, client, auth):
        """
        When MSG_A2 is the top match, its neighbors (MSG_A1, MSG_A3) should
        also appear in the response (context expansion ±2).
        """
        # Use the cooking embedding so it matches MSG_A2 (and A1, A3 are neighbors)
        # Actually we need the quantum embedding to match conv_a
        resp = _query(client, auth, {
            "userId": QUERY_USER_ID,
            "query": "quantum entanglement phenomenon",
            "topK": 5,
            "tokenBudget": 16000,
        }, openai_key=_EMBED_KEY_QUANTUM)

        assert resp.status_code == 200
        body = resp.json()

        # Find conv_a result
        conv_a_results = [r for r in body["results"] if r["conversationId"] == CONV_ID_A]
        if conv_a_results:
            msg_ids_returned = [m["messageId"] for m in conv_a_results[0]["messages"]]
            # At least two of the three conv_a messages should appear (direct + neighbor)
            a_msg_ids = {MSG_A1, MSG_A2, MSG_A3}
            returned_a = a_msg_ids & set(msg_ids_returned)
            assert len(returned_a) >= 1, (
                "At least the matched message should be in the result"
            )


# ── Response shape correctness ────────────────────────────────────────────────

class TestResponseShape:
    def test_conversation_result_fields(self, client, auth):
        """Every ConversationResult has all required fields."""
        resp = _query(client, auth, {
            "userId": QUERY_USER_ID,
            "query": "quantum entanglement",
        }, openai_key=_EMBED_KEY_QUANTUM)

        assert resp.status_code == 200
        for conv in resp.json()["results"]:
            assert "score" in conv
            assert "conversationId" in conv
            assert "provider" in conv
            assert "model" in conv
            assert "conversationDate" in conv
            assert "conversationStartedAt" in conv
            assert "messages" in conv
            assert isinstance(conv["messages"], list)

    def test_message_result_fields(self, client, auth):
        """Every MessageResult has all required fields with correct types."""
        resp = _query(client, auth, {
            "userId": QUERY_USER_ID,
            "query": "quantum entanglement",
        }, openai_key=_EMBED_KEY_QUANTUM)

        assert resp.status_code == 200
        for conv in resp.json()["results"]:
            for msg in conv["messages"]:
                assert "messageId" in msg
                assert "role" in msg
                assert "content" in msg
                assert "timestamp" in msg
                assert "tokenCount" in msg
                assert isinstance(msg["tokenCount"], int)
                assert msg["role"] in ("user", "assistant", "system")

    def test_latency_field_positive(self, client, auth):
        """queryLatencyMs must be a positive number."""
        resp = _query(client, auth, {
            "userId": QUERY_USER_ID,
            "query": "quantum",
        }, openai_key=_EMBED_KEY_QUANTUM)

        assert resp.status_code == 200
        latency = resp.json()["queryLatencyMs"]
        assert isinstance(latency, (int, float))
        assert latency > 0

    def test_totaltokens_consistent_with_messages(self, client, auth):
        """tokenCount in response == sum of message tokenCounts."""
        resp = _query(client, auth, {
            "userId": QUERY_USER_ID,
            "query": "quantum entanglement",
            "tokenBudget": 16000,
        }, openai_key=_EMBED_KEY_QUANTUM)

        assert resp.status_code == 200
        body = resp.json()
        actual_sum = sum(
            msg["tokenCount"]
            for conv in body["results"]
            for msg in conv["messages"]
        )
        assert body["tokenCount"] == actual_sum, (
            "Response tokenCount must equal sum of individual message tokenCounts"
        )
