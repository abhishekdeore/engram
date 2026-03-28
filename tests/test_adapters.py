"""
Phase 3 — Unit tests for provider adapters
===========================================
Pure unit tests — no Neo4j, no HTTP, no I/O.
All adapter functions are deterministic pure functions.

Run with:
    uv run pytest tests/test_adapters.py -v
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from memory.adapters.normalizer import normalize, AdapterError, supported_providers
from memory.adapters import chatgpt, claude, gemini, grok, copilot

# ── Helpers ───────────────────────────────────────────────────────────────────

def _assert_valid_cmf(messages: list[dict]) -> None:
    """Assert every message in the list is a valid CMF message dict."""
    assert isinstance(messages, list)
    for msg in messages:
        assert msg["role"] in ("user", "assistant"), f"Bad role: {msg['role']}"
        assert isinstance(msg["content"], str) and msg["content"].strip()
        assert isinstance(msg["messageId"], str) and msg["messageId"]
        assert isinstance(msg["timestamp"], str) and msg["timestamp"]
        assert isinstance(msg["tokenCount"], int) and msg["tokenCount"] >= 0


# ── TestNormalizerDispatch ────────────────────────────────────────────────────

class TestNormalizerDispatch:
    def test_supported_providers_returns_all_five(self):
        providers = supported_providers()
        assert set(providers) == {"chatgpt", "claude", "gemini", "grok", "copilot"}

    def test_unknown_provider_raises_adapter_error(self):
        with pytest.raises(AdapterError, match="No adapter for provider"):
            normalize({}, provider="unknown_llm")

    def test_non_dict_input_raises_value_error(self):
        with pytest.raises(ValueError):
            normalize("not a dict", provider="chatgpt")

    def test_empty_dict_returns_empty_list(self):
        for provider in supported_providers():
            result = normalize({}, provider=provider)
            assert result == [], f"Expected [] for {provider} with empty input"

    def test_normalize_filters_empty_content(self):
        raw = {
            "messages": [
                {"role": "user", "content": ""},
                {"role": "assistant", "content": "   "},
                {"role": "user", "content": "Valid content"},
            ]
        }
        result = normalize(raw, provider="chatgpt")
        assert len(result) == 1
        assert result[0]["content"] == "Valid content"

    def test_normalize_filters_system_roles(self):
        raw = {
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there"},
            ]
        }
        result = normalize(raw, provider="chatgpt")
        roles = [m["role"] for m in result]
        assert "system" not in roles
        assert "user" in roles
        assert "assistant" in roles


# ── TestChatGPTAdapter ────────────────────────────────────────────────────────

class TestChatGPTAdapter:
    def test_completions_format_two_turns(self):
        raw = {
            "id": "chatcmpl-abc",
            "model": "gpt-4o",
            "created": 1717000000,
            "messages": [{"role": "user", "content": "Hello"}],
            "choices": [{"message": {"role": "assistant", "content": "Hi there"}}],
        }
        msgs = chatgpt.normalize(raw)
        _assert_valid_cmf(msgs)
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "Hello"
        assert msgs[1]["role"] == "assistant"
        assert msgs[1]["content"] == "Hi there"

    def test_completions_format_message_ids_stable(self):
        raw = {
            "id": "conv-123",
            "created": 1717000000,
            "messages": [{"role": "user", "content": "Stable ID test"}],
            "choices": [],
        }
        m1 = chatgpt.normalize(raw)
        m2 = chatgpt.normalize(raw)
        assert m1[0]["messageId"] == m2[0]["messageId"]

    def test_mapping_format_chronological_order(self):
        raw = {
            "id": "conv-mapping",
            "mapping": {
                "node-1": {
                    "id": "node-1",
                    "message": {
                        "id": "msg-1",
                        "author": {"role": "user"},
                        "create_time": 1000.0,
                        "content": {"content_type": "text", "parts": ["First message"]},
                    },
                    "parent": None,
                    "children": ["node-2"],
                },
                "node-2": {
                    "id": "node-2",
                    "message": {
                        "id": "msg-2",
                        "author": {"role": "assistant"},
                        "create_time": 2000.0,
                        "content": {"content_type": "text", "parts": ["Second message"]},
                    },
                    "parent": "node-1",
                    "children": [],
                },
            },
        }
        msgs = chatgpt.normalize(raw)
        _assert_valid_cmf(msgs)
        assert len(msgs) == 2
        assert msgs[0]["content"] == "First message"
        assert msgs[1]["content"] == "Second message"

    def test_mapping_format_excludes_system_nodes(self):
        raw = {
            "id": "conv-sys",
            "mapping": {
                "n1": {
                    "id": "n1",
                    "message": {
                        "id": "m1",
                        "author": {"role": "system"},
                        "create_time": 1.0,
                        "content": {"content_type": "text", "parts": ["System prompt"]},
                    },
                    "parent": None, "children": [],
                },
                "n2": {
                    "id": "n2",
                    "message": {
                        "id": "m2",
                        "author": {"role": "user"},
                        "create_time": 2.0,
                        "content": {"content_type": "text", "parts": ["User turn"]},
                    },
                    "parent": "n1", "children": [],
                },
            },
        }
        msgs = chatgpt.normalize(raw)
        assert all(m["role"] != "system" for m in msgs)
        assert len(msgs) == 1

    def test_extract_metadata(self):
        raw = {"id": "conv-abc", "model": "gpt-4o", "title": "My chat"}
        meta = chatgpt.extract_metadata(raw)
        assert meta["conversationId"] == "conv-abc"
        assert meta["model"] == "gpt-4o"
        assert meta["title"] == "My chat"


# ── TestClaudeAdapter ─────────────────────────────────────────────────────────

class TestClaudeAdapter:
    def test_api_response_format(self):
        raw = {
            "id": "msg_abc",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-4-6",
            "content": [{"type": "text", "text": "Hello from Claude"}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        msgs = claude.normalize(raw)
        _assert_valid_cmf(msgs)
        assert len(msgs) == 1
        assert msgs[0]["role"] == "assistant"
        assert msgs[0]["content"] == "Hello from Claude"
        assert msgs[0]["tokenCount"] == 5

    def test_history_format(self):
        raw = {
            "id": "conv-claude",
            "model": "claude-sonnet-4-6",
            "created_at": "2024-01-15T10:00:00Z",
            "messages": [
                {"role": "user",      "content": "What is AI?", "created_at": "2024-01-15T10:00:00Z"},
                {"role": "assistant", "content": "AI is...",    "created_at": "2024-01-15T10:00:05Z"},
            ],
        }
        msgs = claude.normalize(raw)
        _assert_valid_cmf(msgs)
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"

    def test_content_block_array_extracted(self):
        raw = {
            "type": "message",
            "id": "msg_x",
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Part one. "},
                {"type": "text", "text": "Part two."},
            ],
            "usage": {"output_tokens": 10},
        }
        msgs = claude.normalize(raw)
        assert "Part one." in msgs[0]["content"]
        assert "Part two." in msgs[0]["content"]

    def test_extract_metadata(self):
        raw = {"id": "msg_abc", "model": "claude-sonnet-4-6"}
        meta = claude.extract_metadata(raw)
        assert meta["model"] == "claude-sonnet-4-6"


# ── TestGeminiAdapter ─────────────────────────────────────────────────────────

class TestGeminiAdapter:
    def test_generate_content_response(self):
        raw = {
            "id": "gemini-conv",
            "contents": [
                {"role": "user",  "parts": [{"text": "What is ML?"}]},
            ],
            "candidates": [{
                "content": {
                    "role": "model",
                    "parts": [{"text": "ML is machine learning."}],
                }
            }],
            "usageMetadata": {"candidatesTokenCount": 8},
        }
        msgs = gemini.normalize(raw)
        _assert_valid_cmf(msgs)
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"
        assert msgs[1]["content"] == "ML is machine learning."
        assert msgs[1]["tokenCount"] == 8

    def test_model_role_mapped_to_assistant(self):
        raw = {
            "contents": [
                {"role": "model", "parts": [{"text": "I am the model"}]}
            ],
        }
        msgs = gemini.normalize(raw)
        assert msgs[0]["role"] == "assistant"

    def test_history_format(self):
        raw = {
            "id": "hist-123",
            "model": "gemini-1.5-pro",
            "contents": [
                {"role": "user",  "parts": [{"text": "Hello"}]},
                {"role": "model", "parts": [{"text": "Hi"}]},
            ],
        }
        msgs = gemini.normalize(raw)
        _assert_valid_cmf(msgs)
        assert len(msgs) == 2

    def test_extract_metadata(self):
        raw = {"id": "g-conv", "model": "gemini-1.5-pro"}
        meta = gemini.extract_metadata(raw)
        assert meta["model"] == "gemini-1.5-pro"


# ── TestGrokAdapter ───────────────────────────────────────────────────────────

class TestGrokAdapter:
    def test_completions_format(self):
        raw = {
            "id": "grok-xyz",
            "model": "grok-2",
            "created": 1717000000,
            "messages": [{"role": "user", "content": "Hello Grok"}],
            "choices": [{"message": {"role": "assistant", "content": "Hello human"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        }
        msgs = grok.normalize(raw)
        _assert_valid_cmf(msgs)
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"

    def test_message_ids_have_grok_prefix(self):
        raw = {
            "id": "grok-id-test",
            "created": 1717000000,
            "messages": [{"role": "user", "content": "Prefix test"}],
            "choices": [],
        }
        msgs = grok.normalize(raw)
        assert msgs[0]["messageId"].startswith("grok-")

    def test_completion_tokens_applied_to_last_assistant(self):
        raw = {
            "id": "grok-tokens",
            "created": 1717000000,
            "messages": [{"role": "user", "content": "Token test"}],
            "choices": [{"message": {"role": "assistant", "content": "Response"}}],
            "usage": {"completion_tokens": 7},
        }
        msgs = grok.normalize(raw)
        assistant_msg = next(m for m in msgs if m["role"] == "assistant")
        assert assistant_msg["tokenCount"] == 7


# ── TestCopilotAdapter ────────────────────────────────────────────────────────

class TestCopilotAdapter:
    def test_vscode_requests_format(self):
        raw = {
            "id": "copilot-sess",
            "model": "gpt-4o",
            "requests": [
                {
                    "message":   "How do I write a loop?",
                    "response":  [{"value": "Use a for loop like this..."}],
                    "timestamp": 1717000000000,
                },
                {
                    "message":   "And a while loop?",
                    "response":  [{"value": "While loops continue while..."}],
                    "timestamp": 1717000001000,
                },
            ],
        }
        msgs = copilot.normalize(raw)
        _assert_valid_cmf(msgs)
        assert len(msgs) == 4
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "How do I write a loop?"
        assert msgs[1]["role"] == "assistant"
        assert msgs[2]["role"] == "user"
        assert msgs[3]["role"] == "assistant"

    def test_turns_format(self):
        raw = {
            "id": "copilot-turns",
            "model": "gpt-4o-copilot",
            "turns": [
                {"role": "user",      "content": "Help me",  "created_at": "2024-01-15T10:00:00Z"},
                {"role": "assistant", "content": "Sure!",    "created_at": "2024-01-15T10:00:05Z"},
            ],
        }
        msgs = copilot.normalize(raw)
        _assert_valid_cmf(msgs)
        assert len(msgs) == 2

    def test_message_ids_have_copilot_prefix(self):
        raw = {
            "id": "cp-prefix",
            "requests": [{
                "message":   "Prefix check",
                "response":  [{"value": "OK"}],
                "timestamp": 1717000000000,
            }],
        }
        msgs = copilot.normalize(raw)
        for m in msgs:
            assert m["messageId"].startswith("copilot-")

    def test_empty_response_filtered(self):
        raw = {
            "id": "cp-empty",
            "requests": [{
                "message":   "Question",
                "response":  [{"value": ""}],
                "timestamp": 1717000000000,
            }],
        }
        msgs = copilot.normalize(raw)
        # Only the user turn should remain (assistant content is empty)
        assert all(m["role"] == "user" for m in msgs)


# ── Cross-provider consistency ────────────────────────────────────────────────

class TestCrossProviderConsistency:
    """All adapters must produce the same CMF schema regardless of provider."""

    @pytest.mark.parametrize("provider,raw", [
        ("chatgpt", {
            "id": "test", "created": 1717000000,
            "messages": [{"role": "user", "content": "Hi"}],
            "choices": [{"message": {"role": "assistant", "content": "Hello"}}],
        }),
        ("claude", {
            "id": "test",
            "messages": [
                {"role": "user",      "content": "Hi",    "created_at": "2024-01-15T10:00:00Z"},
                {"role": "assistant", "content": "Hello", "created_at": "2024-01-15T10:00:05Z"},
            ],
        }),
        ("gemini", {
            "id": "test",
            "contents": [
                {"role": "user",  "parts": [{"text": "Hi"}]},
                {"role": "model", "parts": [{"text": "Hello"}]},
            ],
        }),
        ("grok", {
            "id": "test", "created": 1717000000,
            "messages": [{"role": "user", "content": "Hi"}],
            "choices": [{"message": {"role": "assistant", "content": "Hello"}}],
        }),
        ("copilot", {
            "id": "test",
            "requests": [{
                "message": "Hi", "response": [{"value": "Hello"}],
                "timestamp": 1717000000000,
            }],
        }),
    ])
    def test_all_providers_produce_valid_cmf(self, provider, raw):
        msgs = normalize(raw, provider=provider)
        _assert_valid_cmf(msgs)
        assert len(msgs) >= 1
