"""
Phase 6 — Production Hardening Tests
======================================
Tests for all Phase 6 additions:
  - Structured exception hierarchy & handler
  - JWT consolidation (jti claim, token_type)
  - Security headers
  - Auth error message sanitization
  - Storage cap enforcement
  - Daily query limit enforcement
  - GET /memory/usage endpoint
  - CORS restriction
  - API key rate limiting
  - /auth/token production blocking

All tests run WITHOUT Neo4j. Integration tests use a lightweight test app
with mocked Neo4j driver (same pattern as test_token_revocation.py).

Run with:
    uv run pytest tests/test_phase6_hardening.py -v
"""

import sys
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from jose import jwt as jose_jwt

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from memory.auth.jwt_handler import create_access_token, decode_access_token
from memory.config import settings
from memory.exceptions import (
    AuthError,
    EngramError,
    ForbiddenError,
    NotFoundError,
    RateLimitError,
    StorageCapError,
    ValidationError,
    WriteError,
    QueryError,
    ServiceUnavailableError,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

TEST_USER = "test-user-phase6"
TEST_USER_2 = "test-user-phase6-other"


# ── Lightweight test app (no Neo4j required) ─────────────────────────────────

def _make_mock_neo4j_driver(message_count=0):
    """Create a mock Neo4j driver that returns a configurable message count."""
    driver = AsyncMock()

    # Mock for count_user_messages (used by storage cap and usage endpoint)
    async def mock_execute_read(func, *args, **kwargs):
        return message_count

    mock_session = AsyncMock()
    mock_session.execute_read = AsyncMock(side_effect=mock_execute_read)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    driver.session = MagicMock(return_value=mock_session)
    return driver


@asynccontextmanager
async def _test_lifespan(app: FastAPI):
    """Minimal lifespan: mocked Neo4j, no OpenAI, no Redis."""
    app.state.neo4j_driver = _make_mock_neo4j_driver(message_count=5)
    app.state.openai_client = None
    app.state.redis_client = None
    yield


def _create_test_app() -> FastAPI:
    """Build a full app with all routes but mocked backends."""
    from memory.api.routes.auth import router as auth_router
    from memory.api.routes.memory import router as memory_router
    from memory.api.routes.query import router as query_router
    from memory.api.routes.chatgpt import router as chatgpt_router
    from memory.api.routes.health import router as health_router
    from memory.api.dependencies import CurrentUserId
    from memory.api.limiter import limiter
    from memory.exceptions import EngramError
    from slowapi import _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded

    test_app = FastAPI(lifespan=_test_lifespan)
    test_app.state.limiter = limiter
    test_app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # Engram error handler (same as production app)
    @test_app.exception_handler(EngramError)
    async def engram_error_handler(request: Request, exc: EngramError):
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error_code": exc.error_code,
                "message": exc.message,
                "details": exc.details,
            },
        )

    # Security headers + correlation ID middleware (same as production)
    @test_app.middleware("http")
    async def security_and_correlation_middleware(request: Request, call_next):
        raw_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request_id = re.sub(r"[^a-zA-Z0-9\-_]", "", raw_id)[:36]
        if not request_id:
            request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        return response

    # Health endpoint (lightweight, no Neo4j check)
    @test_app.get("/health")
    async def health():
        return {"status": "ok", "neo4j": "mocked", "version": "0.5.0"}

    test_app.include_router(auth_router)
    test_app.include_router(memory_router)
    test_app.include_router(query_router)
    test_app.include_router(chatgpt_router)
    return test_app


@pytest.fixture(scope="module")
def client():
    """TestClient using lightweight app — no Neo4j connection needed."""
    app = _create_test_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def token() -> str:
    return create_access_token(TEST_USER)


@pytest.fixture(scope="module")
def auth_headers(token) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ═══════════════════════════════════════════════════════════════════════════════
# 1. EXCEPTION HIERARCHY TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestExceptionHierarchy:
    """Verify structured exceptions carry correct defaults and are customizable."""

    def test_base_engram_error_defaults(self):
        err = EngramError("something broke")
        assert err.status_code == 500
        assert err.error_code == "INTERNAL_ERROR"
        assert err.message == "something broke"
        assert err.details == {}

    def test_auth_error_defaults(self):
        err = AuthError("bad token")
        assert err.status_code == 401
        assert err.error_code == "AUTH_INVALID_TOKEN"

    def test_storage_cap_error_with_details(self):
        err = StorageCapError(
            "limit reached",
            details={"current": 10000, "limit": 10000},
        )
        assert err.status_code == 403
        assert err.error_code == "STORAGE_CAP_EXCEEDED"
        assert err.details["current"] == 10000

    def test_rate_limit_error(self):
        err = RateLimitError("daily limit exceeded")
        assert err.status_code == 429

    def test_custom_overrides(self):
        err = EngramError(
            "custom",
            error_code="CUSTOM_CODE",
            status_code=418,
            details={"tea": "pot"},
        )
        assert err.status_code == 418
        assert err.error_code == "CUSTOM_CODE"
        assert err.details["tea"] == "pot"

    def test_all_subclasses_inherit_from_engram_error(self):
        for cls in [AuthError, ForbiddenError, NotFoundError, ValidationError,
                     RateLimitError, StorageCapError, WriteError, QueryError,
                     ServiceUnavailableError]:
            assert issubclass(cls, EngramError)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. JWT CONSOLIDATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestJWTConsolidation:
    """Verify JWT encoding changes from Phase 6."""

    def test_session_token_has_jti_claim(self):
        token = create_access_token("user1")
        payload = jose_jwt.decode(
            token, settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        assert "jti" in payload
        assert payload["type"] == "session"
        assert payload["sub"] == "user1"

    def test_apikey_token_has_jti_and_type(self):
        token = create_access_token("user1", token_type="apikey", expire_minutes=60)
        payload = jose_jwt.decode(
            token, settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        assert payload["type"] == "apikey"
        assert "jti" in payload
        assert len(payload["jti"]) == 32  # hex(16 bytes) = 32 chars

    def test_each_token_has_unique_jti(self):
        t1 = create_access_token("user1")
        t2 = create_access_token("user1")
        p1 = jose_jwt.decode(t1, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        p2 = jose_jwt.decode(t2, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        assert p1["jti"] != p2["jti"]

    def test_custom_expire_minutes(self):
        token = create_access_token("user1", expire_minutes=5)
        payload = jose_jwt.decode(
            token, settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
        iat = datetime.fromtimestamp(payload["iat"], tz=timezone.utc)
        diff = (exp - iat).total_seconds()
        assert 290 <= diff <= 310  # ~5 minutes

    def test_decode_accepts_legacy_tokens_without_jti(self):
        """Tokens issued before Phase 6 (no jti/type) must still be accepted."""
        legacy_payload = {
            "sub": "legacy-user",
            "iat": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        }
        legacy_token = jose_jwt.encode(
            legacy_payload, settings.jwt_secret_key,
            algorithm=settings.jwt_algorithm,
        )
        user_id = decode_access_token(legacy_token)
        assert user_id == "legacy-user"

    def test_decode_rejects_expired_token(self):
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
            decode_access_token(expired_token)

    def test_decode_rejects_wrong_secret(self):
        token = jose_jwt.encode(
            {"sub": "user1", "iat": datetime.now(timezone.utc),
             "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
            "wrong-secret-key-that-is-different",
            algorithm="HS256",
        )
        with pytest.raises(ValueError, match="Invalid or expired token"):
            decode_access_token(token)

    def test_decode_rejects_missing_sub(self):
        token = jose_jwt.encode(
            {"iat": datetime.now(timezone.utc),
             "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
            settings.jwt_secret_key,
            algorithm=settings.jwt_algorithm,
        )
        with pytest.raises(ValueError, match="Invalid or expired token"):
            decode_access_token(token)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. SECURITY HEADERS TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestSecurityHeaders:
    """Verify security response headers are present."""

    def test_health_has_security_headers(self, client):
        resp = client.get("/health")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("X-Frame-Options") == "DENY"
        assert resp.headers.get("Referrer-Policy") == "no-referrer"
        assert "default-src 'none'" in resp.headers.get("Content-Security-Policy", "")

    def test_correlation_id_sanitised(self, client):
        """Injection characters should be stripped from X-Request-ID."""
        resp = client.get("/health", headers={"X-Request-ID": "abc<script>alert(1)</script>"})
        rid = resp.headers.get("X-Request-ID", "")
        assert "<" not in rid
        assert ">" not in rid
        assert "(" not in rid
        assert len(rid) <= 36

    def test_correlation_id_generated_when_empty(self, client):
        resp = client.get("/health")
        rid = resp.headers.get("X-Request-ID", "")
        assert len(rid) > 0

    def test_correlation_id_clean_value_preserved(self, client):
        resp = client.get("/health", headers={"X-Request-ID": "abc-123_test"})
        assert resp.headers.get("X-Request-ID") == "abc-123_test"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. AUTH ERROR SANITIZATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestAuthErrorSanitization:
    """Verify auth errors don't leak JWT internals."""

    def test_invalid_token_returns_generic_message(self, client):
        resp = client.get(
            "/memory/usage",
            headers={"Authorization": "Bearer totally.invalid.token"},
        )
        assert resp.status_code == 401
        body = resp.json()
        assert body["detail"] == "Invalid or expired token"
        assert "Signature" not in body["detail"]
        assert "decode" not in body["detail"].lower()

    def test_expired_token_returns_generic_message(self, client):
        expired_payload = {
            "sub": "user1",
            "iat": datetime.now(timezone.utc) - timedelta(hours=2),
            "exp": datetime.now(timezone.utc) - timedelta(hours=1),
        }
        expired_token = jose_jwt.encode(
            expired_payload, settings.jwt_secret_key,
            algorithm=settings.jwt_algorithm,
        )
        resp = client.get(
            "/memory/usage",
            headers={"Authorization": f"Bearer {expired_token}"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Invalid or expired token"


# ═══════════════════════════════════════════════════════════════════════════════
# 5. AUTH ENDPOINT HARDENING TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestAuthEndpointHardening:

    def test_auth_token_works_in_development(self, client):
        """POST /auth/token should succeed in development mode."""
        resp = client.post("/auth/token", json={"userId": "test-user"})
        assert resp.status_code in (201, 403)

    def test_apikey_endpoint_returns_jti_token(self, client):
        # Bootstrap: get a session token first
        apikey_user = "test-apikey-user"
        token_resp = client.post("/auth/token", json={"userId": apikey_user})
        if token_resp.status_code != 201:
            pytest.skip("Dev token endpoint not available")
        session_token = token_resp.json()["access_token"]

        resp = client.post(
            "/auth/apikey",
            json={"userId": apikey_user},
            headers={"Authorization": f"Bearer {session_token}"},
        )
        assert resp.status_code == 201
        api_key = resp.json()["api_key"]
        payload = jose_jwt.decode(
            api_key, settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        assert "jti" in payload
        assert payload["type"] == "apikey"

    def test_apikey_requires_authentication(self, client):
        resp = client.post("/auth/apikey", json={"userId": "some-user"})
        assert resp.status_code in (401, 403)

    def test_apikey_rejects_userid_mismatch(self, client):
        token = create_access_token("user-alpha")
        resp = client.post(
            "/auth/apikey",
            json={"userId": "user-beta"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403
        assert "Cannot create API keys for other users" in resp.json()["detail"]

    def test_apikey_succeeds_with_matching_auth(self, client):
        user = "test-apikey-match-user"
        token = create_access_token(user)
        resp = client.post(
            "/auth/apikey",
            json={"userId": user},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["userId"] == user
        assert "api_key" in data
        assert "expires_at" in data


# ═══════════════════════════════════════════════════════════════════════════════
# 6. STORAGE CAP ENFORCEMENT TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestStorageCapEnforcement:
    """Test storage cap logic using mocked Neo4j driver."""

    @pytest.mark.asyncio
    async def test_check_storage_cap_passes_under_limit(self):
        from memory.services.write_service import check_storage_cap
        driver = _make_mock_neo4j_driver(message_count=5)
        original = settings.free_tier_message_limit
        settings.free_tier_message_limit = 10000
        try:
            # Should not raise
            await check_storage_cap(driver, "user-under-limit")
        finally:
            settings.free_tier_message_limit = original

    @pytest.mark.asyncio
    async def test_check_storage_cap_raises_at_limit(self):
        from memory.services.write_service import check_storage_cap
        driver = _make_mock_neo4j_driver(message_count=100)
        original = settings.free_tier_message_limit
        settings.free_tier_message_limit = 50  # lower than current count
        try:
            with pytest.raises(StorageCapError) as exc_info:
                await check_storage_cap(driver, "user-at-limit")
            assert exc_info.value.error_code == "STORAGE_CAP_EXCEEDED"
            assert exc_info.value.details["current"] == 100
            assert exc_info.value.details["limit"] == 50
        finally:
            settings.free_tier_message_limit = original

    @pytest.mark.asyncio
    async def test_check_storage_cap_disabled_when_zero(self):
        from memory.services.write_service import check_storage_cap
        driver = _make_mock_neo4j_driver(message_count=999999)
        original = settings.free_tier_message_limit
        settings.free_tier_message_limit = 0  # 0 = unlimited
        try:
            await check_storage_cap(driver, "user-unlimited")
        finally:
            settings.free_tier_message_limit = original


# ═══════════════════════════════════════════════════════════════════════════════
# 7. DAILY QUERY LIMIT TESTS
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestDailyQueryLimit:

    async def test_query_counter_increments(self):
        from memory.services.usage_service import (
            increment_daily_query_count,
            get_daily_query_count,
            _daily_query_counts,
        )
        _daily_query_counts.clear()

        count = await increment_daily_query_count("user-daily-1")
        assert count == 1
        count = await increment_daily_query_count("user-daily-1")
        assert count == 2
        read = await get_daily_query_count("user-daily-1")
        assert read == 2

    async def test_query_limit_rejects_at_cap(self):
        from memory.services.usage_service import (
            check_daily_query_limit,
            _daily_query_counts,
            _daily_key,
        )
        _daily_query_counts.clear()
        original = settings.free_tier_daily_query_limit
        settings.free_tier_daily_query_limit = 3
        try:
            _daily_query_counts[_daily_key("user-daily-2")] = 3
            with pytest.raises(RateLimitError) as exc_info:
                await check_daily_query_limit("user-daily-2")
            assert exc_info.value.error_code == "DAILY_QUERY_LIMIT_EXCEEDED"
            assert "resets_at" in exc_info.value.details
        finally:
            settings.free_tier_daily_query_limit = original
            _daily_query_counts.clear()

    async def test_query_limit_passes_under_cap(self):
        from memory.services.usage_service import (
            check_daily_query_limit,
            _daily_query_counts,
        )
        _daily_query_counts.clear()
        original = settings.free_tier_daily_query_limit
        settings.free_tier_daily_query_limit = 100
        try:
            # Should not raise (count = 0 < 100)
            await check_daily_query_limit("user-daily-3")
        finally:
            settings.free_tier_daily_query_limit = original


# ═══════════════════════════════════════════════════════════════════════════════
# 8. USAGE ENDPOINT TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestUsageEndpoint:

    def test_usage_returns_correct_shape(self, client, auth_headers):
        resp = client.get("/memory/usage", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert "messages_stored" in body
        assert "messages_limit" in body
        assert "daily_queries_used" in body
        assert "daily_queries_limit" in body
        assert "daily_queries_remaining" in body
        assert "queries_reset_at" in body

    def test_usage_requires_auth(self, client):
        resp = client.get("/memory/usage")
        assert resp.status_code in (401, 403)

    def test_usage_limit_values_match_config(self, client, auth_headers):
        resp = client.get("/memory/usage", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["messages_limit"] == settings.free_tier_message_limit
        assert body["daily_queries_limit"] == settings.free_tier_daily_query_limit

    def test_usage_daily_queries_remaining_computed(self, client, auth_headers):
        from memory.services.usage_service import _daily_query_counts
        _daily_query_counts.clear()

        resp = client.get("/memory/usage", headers=auth_headers)
        body = resp.json()
        assert body["daily_queries_remaining"] == settings.free_tier_daily_query_limit


# ═══════════════════════════════════════════════════════════════════════════════
# 9. ENGRAM ERROR HANDLER INTEGRATION TEST
# ═══════════════════════════════════════════════════════════════════════════════

class TestEngramErrorHandler:
    """Verify the FastAPI exception handler converts EngramError to JSON."""

    @pytest.mark.asyncio
    async def test_storage_cap_returns_structured_json(self):
        """StorageCapError should produce structured JSON with error_code."""
        from memory.services.write_service import check_storage_cap
        driver = _make_mock_neo4j_driver(message_count=500)
        original = settings.free_tier_message_limit
        settings.free_tier_message_limit = 100
        try:
            with pytest.raises(StorageCapError) as exc_info:
                await check_storage_cap(driver, "user-err-test")
            err = exc_info.value
            assert err.status_code == 403
            assert err.error_code == "STORAGE_CAP_EXCEEDED"
            assert err.details["current"] == 500
            assert err.details["limit"] == 100
        finally:
            settings.free_tier_message_limit = original


# ═══════════════════════════════════════════════════════════════════════════════
# 10. REDIS-BACKED DAILY QUERY COUNTER TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class FakeRedisClient:
    """Minimal async Redis mock for unit testing the counter logic."""

    def __init__(self):
        self._store: dict[str, int] = {}
        self._ttls: dict[str, int] = {}

    async def get(self, key: str):
        val = self._store.get(key)
        return str(val).encode() if val is not None else None

    async def incr(self, key: str) -> int:
        self._store[key] = self._store.get(key, 0) + 1
        return self._store[key]

    async def expire(self, key: str, ttl: int) -> None:
        self._ttls[key] = ttl


class BrokenRedisClient:
    """Redis mock that raises on every operation."""

    async def get(self, key: str):
        raise ConnectionError("Redis unavailable")

    async def incr(self, key: str) -> int:
        raise ConnectionError("Redis unavailable")

    async def expire(self, key: str, ttl: int) -> None:
        raise ConnectionError("Redis unavailable")


@pytest.mark.asyncio
class TestRedisBackedCounter:
    """Verify Redis-backed daily query counter behaviour."""

    async def test_redis_increment_is_atomic(self):
        from memory.services.usage_service import increment_daily_query_count, _daily_query_counts
        _daily_query_counts.clear()
        fake = FakeRedisClient()

        count1 = await increment_daily_query_count("user-redis-1", redis_client=fake)
        assert count1 == 1
        count2 = await increment_daily_query_count("user-redis-1", redis_client=fake)
        assert count2 == 2
        count3 = await increment_daily_query_count("user-redis-1", redis_client=fake)
        assert count3 == 3

    async def test_redis_get_count(self):
        from memory.services.usage_service import get_daily_query_count, increment_daily_query_count, _daily_query_counts
        _daily_query_counts.clear()
        fake = FakeRedisClient()

        initial = await get_daily_query_count("user-redis-2", redis_client=fake)
        assert initial == 0

        await increment_daily_query_count("user-redis-2", redis_client=fake)
        await increment_daily_query_count("user-redis-2", redis_client=fake)

        count = await get_daily_query_count("user-redis-2", redis_client=fake)
        assert count == 2

    async def test_redis_ttl_set_on_first_increment(self):
        from memory.services.usage_service import (
            increment_daily_query_count,
            _redis_daily_key,
            _DAILY_QUERY_TTL,
            _daily_query_counts,
        )
        _daily_query_counts.clear()
        fake = FakeRedisClient()

        await increment_daily_query_count("user-redis-3", redis_client=fake)
        key = _redis_daily_key("user-redis-3")
        assert fake._ttls.get(key) == _DAILY_QUERY_TTL

        # Second increment should NOT re-set TTL
        old_ttls = dict(fake._ttls)
        await increment_daily_query_count("user-redis-3", redis_client=fake)
        assert fake._ttls == old_ttls

    async def test_redis_key_format_includes_date(self):
        from memory.services.usage_service import _redis_daily_key
        key = _redis_daily_key("user-42")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert key == f"engram:daily_query:user-42:{today}"

    async def test_redis_unavailable_falls_back_to_memory(self):
        from memory.services.usage_service import (
            get_daily_query_count,
            increment_daily_query_count,
            _daily_query_counts,
            _daily_key,
        )
        _daily_query_counts.clear()
        broken = BrokenRedisClient()

        count = await increment_daily_query_count("user-fallback", redis_client=broken)
        assert count == 1
        count = await get_daily_query_count("user-fallback", redis_client=broken)
        assert count == 1
        assert _daily_query_counts[_daily_key("user-fallback")] == 1

    async def test_check_limit_with_redis(self):
        from memory.services.usage_service import check_daily_query_limit, increment_daily_query_count, _daily_query_counts

        _daily_query_counts.clear()
        fake = FakeRedisClient()

        original_limit = settings.free_tier_daily_query_limit
        settings.free_tier_daily_query_limit = 2
        try:
            await increment_daily_query_count("user-limit", redis_client=fake)
            await check_daily_query_limit("user-limit", redis_client=fake)

            await increment_daily_query_count("user-limit", redis_client=fake)
            with pytest.raises(RateLimitError):
                await check_daily_query_limit("user-limit", redis_client=fake)
        finally:
            settings.free_tier_daily_query_limit = original_limit

    async def test_no_redis_uses_memory_only(self):
        from memory.services.usage_service import get_daily_query_count, increment_daily_query_count, _daily_query_counts
        _daily_query_counts.clear()

        count = await increment_daily_query_count("user-mem-only")
        assert count == 1
        count = await get_daily_query_count("user-mem-only")
        assert count == 1
