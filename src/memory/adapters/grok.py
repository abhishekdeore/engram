"""
Grok / xAI Adapter
===================
Normalizes Grok API responses into CMF.

Grok's API follows the OpenAI Chat Completions specification closely,
so this adapter is a thin wrapper around the ChatGPT adapter with
Grok-specific ID prefixes and message ID generation.

Format — Chat Completions-compatible response
  {
    "id": "...",
    "object": "chat.completion",
    "model": "grok-2",
    "created": 1717000000,
    "choices": [{"message": {"role": "assistant", "content": "..."}}],
    "messages": [{"role": "user", "content": "..."}],
    "usage": {"prompt_tokens": 10, "completion_tokens": 20}
  }
"""

import hashlib
from datetime import datetime, timezone

from .base import ConversationAdapter


def _make_message_id(conversation_id: str, role: str, index: int) -> str:
    seed = f"{conversation_id}:{role}:{index}"
    return "grok-" + hashlib.sha256(seed.encode()).hexdigest()[:24]


def _unix_to_iso(ts) -> str:
    if ts is None:
        return datetime.now(timezone.utc).isoformat()
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except (ValueError, TypeError, OSError):
        return datetime.now(timezone.utc).isoformat()


class GrokAdapter(ConversationAdapter):
    """Adapter for Grok / xAI (OpenAI-compatible) format."""

    def normalize_messages(self, raw: dict) -> list[dict]:
        conv_id = raw.get("id") or "unknown"
        created = raw.get("created")
        base_ts = _unix_to_iso(created)
        usage = raw.get("usage") or {}

        messages = list(raw.get("messages", []))
        for choice in raw.get("choices", []):
            msg = choice.get("message")
            if msg:
                messages.append(msg)

        result = []
        for i, msg in enumerate(messages):
            role = msg.get("role", "")
            if role not in ("user", "assistant"):
                continue
            content = msg.get("content", "")
            if not isinstance(content, str):
                content = str(content)
            if not content.strip():
                continue
            # Approximate token count from usage metadata when available
            token_count = 0
            if i == len(messages) - 1 and role == "assistant":
                token_count = usage.get("completion_tokens") or 0

            result.append({
                "messageId":  msg.get("id") or _make_message_id(conv_id, role, i),
                "role":       role,
                "content":    content,
                "timestamp":  base_ts,
                "tokenCount": token_count,
            })
        return result

    def extract_metadata(self, raw: dict) -> dict:
        return {
            "conversationId": raw.get("id") or "unknown",
            "model":          raw.get("model") or "",
            "title":          None,
        }


_adapter = GrokAdapter()


def normalize(raw: dict) -> list[dict]:
    return _adapter.normalize_messages(raw)


def extract_metadata(raw: dict) -> dict:
    return _adapter.extract_metadata(raw)
