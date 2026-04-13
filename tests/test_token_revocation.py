"""
Token Revocation Tests
=======================
Tests for the Redis-backed token deny-list (Phase 7):
  - Revoke a session token, then use it -> 401
  - Revoke an API key, then use it -> 401
  - User A cannot revoke user B's token -> 403
  - Legacy token (no jti) -> clear error message
  - Redis unavailable -> graceful degradation
  - Deny-list TTL matches token remaining lifetime
  - Revocation service unit tests

Run with:
    uv run pytest tests/test_token_revocation.py -v

These tests use a lightweight test app that does NOT require Neo4j or any
external service. Only the auth routes + a dummy protected endpoint are mounted.
"""

import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt as jose_jwt

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from memory.auth.jwt_handler import create_access_token, decode_access_token_full
from memory.auth.revocation import revoke_token, is_token_revoked, _KEY_PREFIX
from memory.config import settings

# ── Lightweight test app (no Neo4j required) ──────────────────────────────────

def _make_mock_redis():
    """Create a mock Redis client that stores data in a dict."""
    store = {}
    mock = AsyncMock()

    async def mock_setex(key, ttl, value):
        store[key] = (value, ttl)

    async def mock_get(key):
        entry = store.get(key)
        return entry[0] if entry else None

    mock.setex = AsyncMock(side_effect=mock_setex)
    mock.get = AsyncMock(side_effect=mock_get)
    mock._store = store  # expose for assertions
    return mock


@asynccontextmanager
async def _test_lifespan(app: FastAPI):
    """Minimal lifespan: no external services required."""
    app.state.redis_client = None
    app.state.neo4j_driver = None
    app.state.openai_client = None
    yield


def _create_test_app() -> FastAPI:
    """Build a minimal app with auth routes + a dummy protected route."""
    from memory.api.routes.auth import router as auth_router
    from memory.api.dependencies import CurrentUserId
    from memory.api.limiter import limiter
    from slowapi import _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded

    test_app = FastAPI(lifespan=_test_lifespan)
    test_app.state.limiter = limiter
    test_app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    test_app.include_router(auth_router)

    # A dummy protected endpoint that requires auth — used to verify revocation
    @test_app.get("/protected")
    async def protected(user_id: CurrentUserId):
        return {"userId": user_id}

    return test_app


_test_app = _create_test_app()


# ── Fixtures ──────────────────────────────────────────────────────────────────

TEST_USER = "test-user-revocation"
TEST_USER_2 = "test-user-revocation-other"


@pytest.fixture()
def client():
    """Create a TestClient with the lightweight test app."""
    with TestClient(_test_app) as c:
        yield c


@pytest.fixture()
def mock_redis(client):
    """Provide a fresh mock Redis and set it on app.state for the test."""
    redis = _make_mock_redis()
    _test_app.state.redis_client = redis
    yield redis
    _test_app.state.redis_client = None


@pytest.fixture()
def mock_redis_none(client):
    """Ensure Redis is None (unavailable) on app.state."""
    _test_app.state.redis_client = None
    yield
    _test_app.state.redis_client = None


@pytest.fixture()
def mock_redis_broken(client):
    """Set Redis to a mock that raises on every call."""
    broken = AsyncMock()
    broken.get = AsyncMock(side_effect=ConnectionError("Redis down"))
    broken.setex = AsyncMock(side_effect=ConnectionError("Redis down"))
    _test_app.state.redis_client = broken
    yield broken
    _test_app.state.redis_client = None


# ═══════════════════════════════════════════════════════════════════════════════
# 1. REVOCATION SERVICE UNIT TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestRevocationService:
    """Unit tests for the revocation module functions."""

    @pytest.mark.asyncio
    async def test_revoke_token_stores_in_redis(self):
        mock_redis = _make_mock_redis()
        jti = "abc123"
        expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

        result = await revoke_token(mock_redis, jti, expires_at)

        assert result is True
        key = f"{_KEY_PREFIX}{jti}"
        assert key in mock_redis._store
        value, ttl = mock_redis._store[key]
        assert value == "1"
        assert 3500 <= ttl <= 3600  # ~1 hour in seconds

    @pytest.mark.asyncio
    async def test_revoke_token_ttl_matches_remaining_lifetime(self):
        mock_redis = _make_mock_redis()
        jti = "ttl-test"
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=30)

        await revoke_token(mock_redis, jti, expires_at)

        key = f"{_KEY_PREFIX}{jti}"
        _, ttl = mock_redis._store[key]
        assert 1790 <= ttl <= 1800  # ~30 minutes

    @pytest.mark.asyncio
    async def test_revoke_already_expired_token(self):
        mock_redis = _make_mock_redis()
        jti = "expired-jti"
        expires_at = datetime.now(timezone.utc) - timedelta(hours=1)

        result = await revoke_token(mock_redis, jti, expires_at)

        assert result is True  # succeeds silently — already expired
        assert len(mock_redis._store) == 0  # nothing stored

    @pytest.mark.asyncio
    async def test_revoke_token_redis_none(self):
        result = await revoke_token(None, "jti-123", datetime.now(timezone.utc) + timedelta(hours=1))
        assert result is False

    @pytest.mark.asyncio
    async def test_revoke_token_redis_error(self):
        mock_redis = AsyncMock()
        mock_redis.setex = AsyncMock(side_effect=ConnectionError("Redis down"))

        result = await revoke_token(mock_redis, "jti-err", datetime.now(timezone.utc) + timedelta(hours=1))
        assert result is False

    @pytest.mark.asyncio
    async def test_is_token_revoked_true(self):
        mock_redis = _make_mock_redis()
        jti = "revoked-jti"
        await revoke_token(mock_redis, jti, datetime.now(timezone.utc) + timedelta(hours=1))

        result = await is_token_revoked(mock_redis, jti)
        assert result is True

    @pytest.mark.asyncio
    async def test_is_token_revoked_false(self):
        mock_redis = _make_mock_redis()

        result = await is_token_revoked(mock_redis, "not-revoked")
        assert result is False

    @pytest.mark.asyncio
    async def test_is_token_revoked_redis_none(self):
        result = await is_token_revoked(None, "any-jti")
        assert result is False  # fail-open

    @pytest.mark.asyncio
    async def test_is_token_revoked_redis_error(self):
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(side_effect=ConnectionError("Redis down"))

        result = await is_token_revoked(mock_redis, "any-jti")
        assert result is False  # fail-open


# ═══════════════════════════════════════════════════════════════════════════════
# 2. JWT HANDLER FULL DECODE TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestDecodeAccessTokenFull:
    """Tests for decode_access_token_full."""

    def test_returns_full_payload(self):
        token = create_access_token("user1", token_type="session")
        payload = decode_access_token_full(token)

        assert payload["sub"] == "user1"
        assert payload["type"] == "session"
        assert "jti" in payload
        assert "exp" in payload
        assert "iat" in payload

    def test_returns_apikey_type(self):
        token = create_access_token("user1", token_type="apikey", expire_minutes=60)
        payload = decode_access_token_full(token)
        assert payload["type"] == "apikey"

    def test_rejects_invalid_token(self):
        with pytest.raises(ValueError, match="Invalid or expired token"):
            decode_access_token_full("not.a.valid.token")

    def test_rejects_expired_token(self):
        expired_payload = {
            "sub": "user1",
            "iat": datetime.now(timezone.utc) - timedelta(hours=2),
            "exp": datetime.now(timezone.utc) - timedelta(hours=1),
        }
        expired_token = jose_jwt.encode(
            expired_payload, settings.jwt_secret_key,
            algorithm=settings.jwt_algorithm,
        )
        with pytest.raises(ValueError, match="Invalid or expired token"):
            decode_access_token_full(expired_token)

    def test_rejects_missing_sub(self):
        token = jose_jwt.encode(
            {"iat": datetime.now(timezone.utc),
             "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
            settings.jwt_secret_key,
            algorithm=settings.jwt_algorithm,
        )
        with pytest.raises(ValueError, match="Invalid or expired token"):
            decode_access_token_full(token)

    def test_accepts_legacy_token_without_jti(self):
        legacy_payload = {
            "sub": "legacy-user",
            "iat": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        }
        legacy_token = jose_jwt.encode(
            legacy_payload, settings.jwt_secret_key,
            algorithm=settings.jwt_algorithm,
        )
        payload = decode_access_token_full(legacy_token)
        assert payload["sub"] == "legacy-user"
        assert "jti" not in payload


# ═══════════════════════════════════════════════════════════════════════════════
# 3. REVOKE ENDPOINT INTEGRATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestRevokeEndpoint:
    """Integration tests for POST /auth/revoke."""

    def test_revoke_session_token_then_use_returns_401(self, client, mock_redis):
        """Revoke a session token, then try to use it -> 401."""
        token = create_access_token(TEST_USER, token_type="session")
        auth_headers = {"Authorization": f"Bearer {token}"}

        # Revoke it (authenticating with the same token)
        resp = client.post(
            "/auth/revoke",
            headers=auth_headers,
            json={"token": token},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "revoked"
        assert "jti" in body

        # Now try to use the revoked token on a protected endpoint
        resp = client.get("/protected", headers=auth_headers)
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Token has been revoked"

    def test_revoke_api_key_then_use_returns_401(self, client, mock_redis):
        """Revoke an API key, then try to use it -> 401."""
        api_key = create_access_token(TEST_USER, token_type="apikey", expire_minutes=525600)
        auth_headers = {"Authorization": f"Bearer {api_key}"}

        resp = client.post(
            "/auth/revoke",
            headers=auth_headers,
            json={"token": api_key},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "revoked"

        # Try to use the revoked API key
        resp = client.get("/protected", headers=auth_headers)
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Token has been revoked"

    def test_revoke_different_token_same_user(self, client, mock_redis):
        """User can revoke a different token that belongs to them."""
        auth_token = create_access_token(TEST_USER, token_type="session")
        target_token = create_access_token(TEST_USER, token_type="apikey", expire_minutes=525600)

        # Use auth_token to revoke target_token
        resp = client.post(
            "/auth/revoke",
            headers={"Authorization": f"Bearer {auth_token}"},
            json={"token": target_token},
        )
        assert resp.status_code == 200

        # target_token should be revoked
        resp = client.get(
            "/protected",
            headers={"Authorization": f"Bearer {target_token}"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Token has been revoked"

        # auth_token should still work (not revoked)
        resp = client.get(
            "/protected",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["userId"] == TEST_USER

    def test_user_cannot_revoke_other_users_token(self, client, mock_redis):
        """User A cannot revoke user B's token -> 403."""
        user1_token = create_access_token(TEST_USER, token_type="session")
        user2_token = create_access_token(TEST_USER_2, token_type="session")

        resp = client.post(
            "/auth/revoke",
            headers={"Authorization": f"Bearer {user1_token}"},
            json={"token": user2_token},
        )
        assert resp.status_code == 403
        assert "other users" in resp.json()["detail"]

    def test_legacy_token_cannot_be_revoked(self, client, mock_redis):
        """Legacy token (no jti claim) -> clear error message."""
        legacy_payload = {
            "sub": TEST_USER,
            "iat": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        }
        legacy_token = jose_jwt.encode(
            legacy_payload, settings.jwt_secret_key,
            algorithm=settings.jwt_algorithm,
        )

        # Auth with a modern token
        auth_token = create_access_token(TEST_USER, token_type="session")

        resp = client.post(
            "/auth/revoke",
            headers={"Authorization": f"Bearer {auth_token}"},
            json={"token": legacy_token},
        )
        assert resp.status_code == 400
        assert "no jti claim" in resp.json()["detail"]
        assert "legacy" in resp.json()["detail"].lower()

    def test_revoke_invalid_token_returns_400(self, client, mock_redis):
        """Revoking a garbage token -> 400."""
        auth_token = create_access_token(TEST_USER, token_type="session")

        resp = client.post(
            "/auth/revoke",
            headers={"Authorization": f"Bearer {auth_token}"},
            json={"token": "this.is.not.a.valid.jwt"},
        )
        assert resp.status_code == 400
        assert "invalid or already expired" in resp.json()["detail"].lower()

    def test_revoke_expired_token_returns_400(self, client, mock_redis):
        """Revoking an already-expired token -> 400."""
        expired_payload = {
            "sub": TEST_USER,
            "iat": datetime.now(timezone.utc) - timedelta(hours=2),
            "exp": datetime.now(timezone.utc) - timedelta(hours=1),
            "jti": "expired-jti",
            "type": "session",
        }
        expired_token = jose_jwt.encode(
            expired_payload, settings.jwt_secret_key,
            algorithm=settings.jwt_algorithm,
        )
        auth_token = create_access_token(TEST_USER, token_type="session")

        resp = client.post(
            "/auth/revoke",
            headers={"Authorization": f"Bearer {auth_token}"},
            json={"token": expired_token},
        )
        assert resp.status_code == 400

    def test_revoke_requires_authentication(self, client, mock_redis):
        """POST /auth/revoke without auth -> 401/403."""
        resp = client.post(
            "/auth/revoke",
            json={"token": "some-token"},
        )
        assert resp.status_code in (401, 403)

    def test_revoke_redis_unavailable_returns_503(self, client, mock_redis_none):
        """When Redis is None, revoke should return 503."""
        token = create_access_token(TEST_USER, token_type="session")

        resp = client.post(
            "/auth/revoke",
            headers={"Authorization": f"Bearer {token}"},
            json={"token": token},
        )
        assert resp.status_code == 503
        assert "Redis" in resp.json()["detail"]


# ═══════════════════════════════════════════════════════════════════════════════
# 4. AUTH DEPENDENCY REVOCATION CHECK TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestAuthDependencyRevocationCheck:
    """Verify the get_current_user_id dependency checks the deny-list."""

    def test_non_revoked_token_passes(self, client, mock_redis):
        """A valid, non-revoked token should pass auth."""
        token = create_access_token(TEST_USER, token_type="session")
        resp = client.get(
            "/protected",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["userId"] == TEST_USER

    def test_legacy_token_without_jti_passes_auth(self, client, mock_redis):
        """Legacy tokens without jti should still work (not checked against deny-list)."""
        legacy_payload = {
            "sub": TEST_USER,
            "iat": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        }
        legacy_token = jose_jwt.encode(
            legacy_payload, settings.jwt_secret_key,
            algorithm=settings.jwt_algorithm,
        )

        resp = client.get(
            "/protected",
            headers={"Authorization": f"Bearer {legacy_token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["userId"] == TEST_USER

    def test_redis_down_during_auth_check_fails_open(self, client, mock_redis_broken):
        """If Redis raises during auth check, token is treated as valid (fail-open)."""
        token = create_access_token(TEST_USER, token_type="session")
        resp = client.get(
            "/protected",
            headers={"Authorization": f"Bearer {token}"},
        )
        # Should NOT get 401 — fail open means token is accepted
        assert resp.status_code == 200
        assert resp.json()["userId"] == TEST_USER

    def test_redis_none_during_auth_check_fails_open(self, client, mock_redis_none):
        """If Redis is None, tokens pass through (fail-open)."""
        token = create_access_token(TEST_USER, token_type="session")
        resp = client.get(
            "/protected",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["userId"] == TEST_USER


# ═══════════════════════════════════════════════════════════════════════════════
# 5. DENY-LIST TTL TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestDenyListTTL:
    """Verify that deny-list entries have correct TTL."""

    @pytest.mark.asyncio
    async def test_ttl_5_minutes_remaining(self):
        mock_redis = _make_mock_redis()
        jti = "ttl-5min"
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)

        await revoke_token(mock_redis, jti, expires_at)

        key = f"{_KEY_PREFIX}{jti}"
        _, ttl = mock_redis._store[key]
        assert 295 <= ttl <= 300

    @pytest.mark.asyncio
    async def test_ttl_365_days_remaining(self):
        mock_redis = _make_mock_redis()
        jti = "ttl-365d"
        expires_at = datetime.now(timezone.utc) + timedelta(days=365)

        await revoke_token(mock_redis, jti, expires_at)

        key = f"{_KEY_PREFIX}{jti}"
        _, ttl = mock_redis._store[key]
        expected = 365 * 24 * 3600
        assert expected - 5 <= ttl <= expected

    @pytest.mark.asyncio
    async def test_expired_token_no_redis_key(self):
        """Already-expired token should not create a Redis entry."""
        mock_redis = _make_mock_redis()
        jti = "already-expired"
        expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

        await revoke_token(mock_redis, jti, expires_at)

        assert len(mock_redis._store) == 0

    @pytest.mark.asyncio
    async def test_revoked_entry_uses_single_redis_get(self):
        """Verify the deny-list check is a single GET, not a scan."""
        mock_redis = _make_mock_redis()
        jti = "perf-test"
        await revoke_token(mock_redis, jti, datetime.now(timezone.utc) + timedelta(hours=1))

        # Reset call count
        mock_redis.get.reset_mock()

        await is_token_revoked(mock_redis, jti)

        # Should be exactly 1 GET call
        assert mock_redis.get.call_count == 1
        # Verify it used the exact key format
        mock_redis.get.assert_called_once_with(f"{_KEY_PREFIX}{jti}")
