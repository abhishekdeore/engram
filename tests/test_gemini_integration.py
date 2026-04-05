"""
Phase 5B — Gemini Integration Tests
=====================================
Tests for the provider dispatch fix and Gemini-specific flows:
  - MCP tools accept `provider` param and dispatch to correct adapter
  - Default provider is "claude" (backward compat)
  - Invalid provider rejected
  - Gemini-formatted writes stored with provider="gemini"
  - Cross-provider query returns Gemini results

All tests run WITHOUT Neo4j using a lightweight test app with mocked driver.

Run with:
    uv run pytest tests/test_gemini_integration.py -v
"""

import sys
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from memory.adapters.normalizer import normalize as dispatch_normalize, AdapterError
from memory.auth.jwt_handler import create_access_token
from memory.config import settings


# ── Test fixtures ────────────────────────────────────────────────────────────

TEST_USER = "test-user-gemini"


@pytest.fixture(scope="module")
def token() -> str:
    return create_access_token(TEST_USER)


@pytest.fixture(scope="module")
def auth_headers(token) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ═══════════════════════════════════════════════════════════════════════════════
# 1. NORMALIZER DISPATCH TESTS (unit, no Neo4j)
# ═══════════════════════════════════════════════════════════════════════════════

class TestNormalizerDispatch:
    """Verify the normalizer dispatches to the correct adapter based on provider."""

    def test_gemini_provider_dispatches_to_gemini_adapter(self):
        """Gemini-formatted data should be normalized by the Gemini adapter."""
        raw = {
            "id": "conv-gemini-1",
            "model": "gemini-2.5-pro",
            "contents": [
                {"role": "user", "parts": [{"text": "Hello from Gemini"}]},
                {"role": "model", "parts": [{"text": "Hello! I am Gemini."}]},
            ],
        }
        result = dispatch_normalize(raw, provider="gemini")
        assert len(result) == 2
        assert result[0]["role"] == "user"
        assert result[0]["content"] == "Hello from Gemini"
        assert result[1]["role"] == "assistant"  # "model" mapped to "assistant"
        assert result[1]["content"] == "Hello! I am Gemini."
        # Message IDs should have gemini prefix
        assert result[0]["messageId"].startswith("gemini-")

    def test_claude_provider_dispatches_to_claude_adapter(self):
        """Claude-formatted data should still work (backward compat)."""
        raw = {
            "id": "conv-claude-1",
            "model": "claude-sonnet-4-6",
            "messages": [
                {"role": "user", "content": "Hello from Claude"},
                {"role": "assistant", "content": "Hello! I am Claude."},
            ],
        }
        result = dispatch_normalize(raw, provider="claude")
        assert len(result) == 2
        assert result[0]["content"] == "Hello from Claude"
        assert result[0]["messageId"].startswith("claude-")

    def test_chatgpt_provider_dispatches_to_chatgpt_adapter(self):
        """ChatGPT-formatted data should work."""
        raw = {
            "id": "conv-gpt-1",
            "model": "gpt-4o",
            "messages": [
                {"role": "user", "content": "Hello from ChatGPT"},
                {"role": "assistant", "content": "Hello!"},
            ],
        }
        result = dispatch_normalize(raw, provider="chatgpt")
        assert len(result) == 2

    def test_invalid_provider_raises_adapter_error(self):
        """Unknown provider should raise AdapterError."""
        raw = {"id": "x", "model": "x", "messages": []}
        with pytest.raises(AdapterError, match="No adapter for provider"):
            dispatch_normalize(raw, provider="unknown_llm")

    def test_provider_is_case_insensitive(self):
        """Provider name should be case-insensitive."""
        raw = {
            "id": "conv-1",
            "model": "gemini-2.5-pro",
            "contents": [
                {"role": "user", "parts": [{"text": "test"}]},
            ],
        }
        result = dispatch_normalize(raw, provider="GEMINI")
        assert len(result) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 2. MCP TOOLS PROVIDER DISPATCH TESTS (unit, mocked Neo4j)
# ═══════════════════════════════════════════════════════════════════════════════

class TestMCPToolsProviderDispatch:
    """Verify handle_memory_write uses the provider field correctly."""

    @pytest.mark.asyncio
    async def test_write_with_gemini_provider(self):
        """handle_memory_write with provider='gemini' should construct WriteRequest with provider=gemini."""
        from memory.mcp_tools import handle_memory_write

        # Track what WriteRequest was constructed
        captured_requests = []
        original_write = None

        async def mock_write(driver, request, openai_client=None, redis_client=None):
            captured_requests.append(request)

        # Monkey-patch write_conversation_to_graph
        import memory.mcp_tools as tools_module
        original_write = tools_module.write_conversation_to_graph
        tools_module.write_conversation_to_graph = mock_write

        try:
            result = await handle_memory_write(
                args={
                    "conversation_id": "gemini-conv-001",
                    "provider": "gemini",
                    "model": "gemini-2.5-pro",
                    "messages": [
                        {"role": "user", "content": "Hello Gemini"},
                        {"role": "assistant", "content": "Hello!"},
                    ],
                },
                driver=AsyncMock(),
                openai_client=None,
                redis_client=None,
                user_id=TEST_USER,
            )

            assert len(captured_requests) == 1
            req = captured_requests[0]
            assert req.provider == "gemini"
            assert req.model == "gemini-2.5-pro"
            assert req.conversationId == "gemini-conv-001"
            assert "Saved 2 message(s)" in result
        finally:
            tools_module.write_conversation_to_graph = original_write

    @pytest.mark.asyncio
    async def test_write_default_provider_is_claude(self):
        """handle_memory_write without provider field should default to 'claude'."""
        from memory.mcp_tools import handle_memory_write

        captured_requests = []

        async def mock_write(driver, request, openai_client=None, redis_client=None):
            captured_requests.append(request)

        import memory.mcp_tools as tools_module
        original_write = tools_module.write_conversation_to_graph
        tools_module.write_conversation_to_graph = mock_write

        try:
            await handle_memory_write(
                args={
                    "conversation_id": "claude-conv-001",
                    # NO provider field — should default to "claude"
                    "model": "claude-sonnet-4-6",
                    "messages": [
                        {"role": "user", "content": "Hello Claude"},
                        {"role": "assistant", "content": "Hello!"},
                    ],
                },
                driver=AsyncMock(),
                openai_client=None,
                redis_client=None,
                user_id=TEST_USER,
            )

            assert len(captured_requests) == 1
            assert captured_requests[0].provider == "claude"
        finally:
            tools_module.write_conversation_to_graph = original_write

    @pytest.mark.asyncio
    async def test_write_with_chatgpt_provider(self):
        """handle_memory_write with provider='chatgpt' should work."""
        from memory.mcp_tools import handle_memory_write

        captured_requests = []

        async def mock_write(driver, request, openai_client=None, redis_client=None):
            captured_requests.append(request)

        import memory.mcp_tools as tools_module
        original_write = tools_module.write_conversation_to_graph
        tools_module.write_conversation_to_graph = mock_write

        try:
            await handle_memory_write(
                args={
                    "conversation_id": "gpt-conv-001",
                    "provider": "chatgpt",
                    "model": "gpt-4o",
                    "messages": [
                        {"role": "user", "content": "Hello GPT"},
                        {"role": "assistant", "content": "Hello!"},
                    ],
                },
                driver=AsyncMock(),
                openai_client=None,
                redis_client=None,
                user_id=TEST_USER,
            )

            assert len(captured_requests) == 1
            assert captured_requests[0].provider == "chatgpt"
        finally:
            tools_module.write_conversation_to_graph = original_write

    @pytest.mark.asyncio
    async def test_write_invalid_provider_raises(self):
        """handle_memory_write with unknown provider should raise."""
        from memory.mcp_tools import handle_memory_write

        with pytest.raises((ValueError, AdapterError)):
            await handle_memory_write(
                args={
                    "conversation_id": "bad-conv",
                    "provider": "nonexistent_llm",
                    "model": "x",
                    "messages": [{"role": "user", "content": "test"}],
                },
                driver=AsyncMock(),
                openai_client=None,
                redis_client=None,
                user_id=TEST_USER,
            )


# ═══════════════════════════════════════════════════════════════════════════════
# 3. GEMINI ADAPTER FORMAT TESTS (unit, no I/O)
# ═══════════════════════════════════════════════════════════════════════════════

class TestGeminiAdapterFormats:
    """Verify the Gemini adapter handles all known Gemini data formats."""

    def test_generate_content_response_format(self):
        """Gemini generateContent API response with candidates."""
        raw = {
            "id": "gen-resp-1",
            "model": "gemini-2.5-pro",
            "candidates": [
                {"content": {"role": "model", "parts": [{"text": "Generated response"}]}}
            ],
            "contents": [
                {"role": "user", "parts": [{"text": "Generate something"}]},
            ],
        }
        result = dispatch_normalize(raw, provider="gemini")
        assert len(result) == 2
        # User message from contents
        assert result[0]["role"] == "user"
        assert result[0]["content"] == "Generate something"
        # Model response from candidates
        assert result[1]["role"] == "assistant"
        assert result[1]["content"] == "Generated response"

    def test_conversation_history_format(self):
        """Gemini conversation history (contents array only)."""
        raw = {
            "id": "history-1",
            "model": "gemini-2.5-flash",
            "contents": [
                {"role": "user", "parts": [{"text": "First message"}]},
                {"role": "model", "parts": [{"text": "First reply"}]},
                {"role": "user", "parts": [{"text": "Second message"}]},
                {"role": "model", "parts": [{"text": "Second reply"}]},
            ],
        }
        result = dispatch_normalize(raw, provider="gemini")
        assert len(result) == 4
        assert all(r["messageId"].startswith("gemini-") for r in result)

    def test_empty_parts_filtered(self):
        """Messages with empty parts should be filtered out."""
        raw = {
            "id": "empty-1",
            "model": "gemini-2.5-pro",
            "contents": [
                {"role": "user", "parts": [{"text": "Valid message"}]},
                {"role": "model", "parts": [{"text": ""}]},  # empty
            ],
        }
        result = dispatch_normalize(raw, provider="gemini")
        assert len(result) == 1
        assert result[0]["content"] == "Valid message"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. SCHEMA VERIFICATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestSchemaHasProviderField:
    """Verify both MCP tool schemas include the provider field."""

    def test_mcp_tools_schema_has_provider(self):
        from memory.mcp_tools import MEMORY_WRITE_SCHEMA
        props = MEMORY_WRITE_SCHEMA["properties"]
        assert "provider" in props
        assert "gemini" in props["provider"]["enum"]
        assert "claude" in props["provider"]["enum"]
        assert "chatgpt" in props["provider"]["enum"]

    def test_mcp_server_schema_has_provider(self):
        from memory.mcp_server import MEMORY_WRITE_SCHEMA
        props = MEMORY_WRITE_SCHEMA["properties"]
        assert "provider" in props
        assert "gemini" in props["provider"]["enum"]

    def test_query_schema_has_gemini_in_providers(self):
        from memory.mcp_tools import MEMORY_QUERY_SCHEMA
        providers_enum = MEMORY_QUERY_SCHEMA["properties"]["providers"]["items"]["enum"]
        assert "gemini" in providers_enum
