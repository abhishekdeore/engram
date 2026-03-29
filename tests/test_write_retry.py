"""
Phase 4 — Write service retry mechanism tests
===============================================
Validates that write_conversation_to_graph retries correctly on transient
Neo4j failures and does NOT retry on permanent errors.

These tests mock _write_to_neo4j (the extracted Neo4j pipeline helper) so
they run without a live database. asyncio.sleep is also mocked so tests
complete instantly with no real delays.

Coverage:
  - Succeeds on first attempt — no retry, no sleep
  - Retries on ServiceUnavailable, recovers on second attempt
  - Retries on SessionExpired, recovers on third attempt
  - Does NOT retry on ConstraintError (non-retryable)
  - Does NOT retry on ValueError (non-retryable)
  - Exhausts all retries → logs CRITICAL, returns without crashing
  - Retry delays follow exponential backoff (1 s, 2 s)
  - Embedding phase is still called after a successful write
  - Embedding phase is NOT called when Neo4j write ultimately fails
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from neo4j.exceptions import ConstraintError, ServiceUnavailable, SessionExpired
from memory.services.write_service import (
    _MAX_WRITE_ATTEMPTS,
    _RETRY_BASE_DELAY,
    write_conversation_to_graph,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_request():
    """Return a minimal WriteRequest-like object for testing."""
    from memory.models.requests import WriteRequest
    from memory.models.message import MessageIn

    return WriteRequest(
        userId="retry-test-user",
        conversationId="retry-conv-001",
        provider="claude",
        model="claude-sonnet-4-6",
        messages=[
            MessageIn(
                messageId="msg-retry-001",
                role="user",
                content="Hello retry",
                timestamp=datetime.now(timezone.utc),
                tokenCount=3,
            )
        ],
    )


def _make_driver():
    """Return a minimal mock driver (not used when _write_to_neo4j is patched)."""
    return MagicMock()


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestWriteRetry:

    async def test_success_on_first_attempt_no_sleep(self):
        """A clean write completes on the first attempt with no sleep call."""
        request = _make_request()
        driver  = _make_driver()

        with patch(
            "memory.services.write_service._write_to_neo4j",
            new=AsyncMock(return_value=(1, 3)),
        ) as mock_write, patch(
            "memory.services.write_service.embed_new_content",
            new=AsyncMock(),
        ), patch(
            "asyncio.sleep", new=AsyncMock()
        ) as mock_sleep:
            await write_conversation_to_graph(driver, request)

        mock_write.assert_awaited_once()
        mock_sleep.assert_not_awaited()

    async def test_retries_on_service_unavailable_recovers_on_second(self):
        """
        First attempt raises ServiceUnavailable; second attempt succeeds.
        One sleep between attempts at the base delay.
        """
        request = _make_request()
        driver  = _make_driver()

        call_count = 0

        async def flaky_write(*_args, **_kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ServiceUnavailable("test: connection refused")
            return (1, 3)

        with patch(
            "memory.services.write_service._write_to_neo4j",
            new=flaky_write,
        ), patch(
            "memory.services.write_service.embed_new_content",
            new=AsyncMock(),
        ), patch(
            "asyncio.sleep", new=AsyncMock()
        ) as mock_sleep:
            await write_conversation_to_graph(driver, request)

        assert call_count == 2, "Should have tried exactly twice"
        mock_sleep.assert_awaited_once_with(_RETRY_BASE_DELAY)

    async def test_retries_on_session_expired_recovers_on_third(self):
        """
        First two attempts raise SessionExpired; third succeeds.
        Sleep is called twice with exponential delays (1 s, 2 s).
        """
        request = _make_request()
        driver  = _make_driver()

        call_count = 0

        async def flaky_write(*_args, **_kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise SessionExpired("test: session expired")
            return (1, 3)

        with patch(
            "memory.services.write_service._write_to_neo4j",
            new=flaky_write,
        ), patch(
            "memory.services.write_service.embed_new_content",
            new=AsyncMock(),
        ), patch(
            "asyncio.sleep", new=AsyncMock()
        ) as mock_sleep:
            await write_conversation_to_graph(driver, request)

        assert call_count == 3
        assert mock_sleep.await_count == 2
        # Delays: 1.0 s (before attempt 2), 2.0 s (before attempt 3)
        mock_sleep.assert_has_awaits([call(1.0), call(2.0)])

    async def test_no_retry_on_constraint_error(self):
        """ConstraintError is non-retryable — function returns after one attempt."""
        request = _make_request()
        driver  = _make_driver()

        with patch(
            "memory.services.write_service._write_to_neo4j",
            new=AsyncMock(side_effect=ConstraintError("constraint violation")),
        ) as mock_write, patch(
            "memory.services.write_service.embed_new_content",
            new=AsyncMock(),
        ) as mock_embed, patch(
            "asyncio.sleep", new=AsyncMock()
        ) as mock_sleep:
            await write_conversation_to_graph(driver, request)

        mock_write.assert_awaited_once()
        mock_sleep.assert_not_awaited()
        # Embedding must NOT be called when the Neo4j write failed permanently
        mock_embed.assert_not_awaited()

    async def test_no_retry_on_value_error(self):
        """ValueError (e.g. bad data) is non-retryable."""
        request = _make_request()
        driver  = _make_driver()

        with patch(
            "memory.services.write_service._write_to_neo4j",
            new=AsyncMock(side_effect=ValueError("bad data")),
        ) as mock_write, patch(
            "asyncio.sleep", new=AsyncMock()
        ) as mock_sleep:
            await write_conversation_to_graph(driver, request)

        mock_write.assert_awaited_once()
        mock_sleep.assert_not_awaited()

    async def test_all_retries_exhausted_logs_critical(self):
        """
        When every attempt raises ServiceUnavailable, the function logs at
        CRITICAL level (not just WARNING/ERROR) and returns without re-raising.
        """
        request = _make_request()
        driver  = _make_driver()

        with patch(
            "memory.services.write_service._write_to_neo4j",
            new=AsyncMock(side_effect=ServiceUnavailable("always down")),
        ) as mock_write, patch(
            "memory.services.write_service.embed_new_content",
            new=AsyncMock(),
        ) as mock_embed, patch(
            "asyncio.sleep", new=AsyncMock()
        ), patch(
            "memory.services.write_service.logger"
        ) as mock_logger:
            await write_conversation_to_graph(driver, request)

        assert mock_write.await_count == _MAX_WRITE_ATTEMPTS, (
            f"Should have attempted exactly {_MAX_WRITE_ATTEMPTS} times"
        )
        mock_logger.critical.assert_called_once()
        critical_msg = mock_logger.critical.call_args[0][0]
        assert "write_failed_all_retries" in critical_msg

        # Embedding must NOT be called when all Neo4j retries failed
        mock_embed.assert_not_awaited()

    async def test_embedding_called_after_successful_write(self):
        """Embedding phase runs when the Neo4j write succeeds."""
        request     = _make_request()
        driver      = _make_driver()
        openai_mock = MagicMock()
        redis_mock  = None

        with patch(
            "memory.services.write_service._write_to_neo4j",
            new=AsyncMock(return_value=(1, 3)),
        ), patch(
            "memory.services.write_service.embed_new_content",
            new=AsyncMock(),
        ) as mock_embed, patch(
            "asyncio.sleep", new=AsyncMock()
        ):
            await write_conversation_to_graph(
                driver, request, openai_client=openai_mock, redis_client=redis_mock
            )

        mock_embed.assert_awaited_once_with(
            driver=driver,
            openai_client=openai_mock,
            redis_client=redis_mock,
            conversation_id=request.conversationId,
        )

    async def test_embedding_not_called_when_write_fails(self):
        """Embedding phase is skipped when the Neo4j write fails permanently."""
        request = _make_request()
        driver  = _make_driver()

        with patch(
            "memory.services.write_service._write_to_neo4j",
            new=AsyncMock(side_effect=ServiceUnavailable("always down")),
        ), patch(
            "memory.services.write_service.embed_new_content",
            new=AsyncMock(),
        ) as mock_embed, patch(
            "asyncio.sleep", new=AsyncMock()
        ):
            await write_conversation_to_graph(driver, request, openai_client=MagicMock())

        mock_embed.assert_not_awaited()

    async def test_max_attempts_constant_is_three(self):
        """
        _MAX_WRITE_ATTEMPTS must be 3 — this is the contractual retry budget.
        Changing it affects the delay budget (1 + 2 = 3 extra seconds max).
        """
        assert _MAX_WRITE_ATTEMPTS == 3, (
            "Retry budget is 3 attempts. If you change this, update "
            "the exponential backoff delays and this test."
        )
