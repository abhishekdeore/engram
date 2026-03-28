"""
Claude / Anthropic Adapter
==========================
Normalizes Anthropic Messages API responses and Claude conversation exports
into CMF.

Format A — Anthropic Messages API response
  {
    "id": "msg_...",
    "type": "message",
    "role": "assistant",
    "content": [{"type": "text", "text": "..."}],
    "model": "claude-sonnet-4-6",
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 10, "output_tokens": 20}
  }
  Paired with the originating request:
  {
    "model": "...",
    "messages": [{"role": "user", "content": "..."}],
    "system": "..."
  }

Format B — Conversation history array (common browser extension export)
  {
    "id": "...",
    "model": "claude-sonnet-4-6",
    "created_at": "2024-01-15T10:00:00Z",
    "messages": [
      {"role": "user",      "content": "...", "created_at": "..."},
      {"role": "assistant", "content": "...", "created_at": "..."}
    ]
  }

System messages and tool use turns are excluded.
"""

import hashlib
import uuid
from datetime import datetime, timezone

from .base import ConversationAdapter


def _make_message_id(conversation_id: str, role: str, index: int) -> str:
    seed = f"{conversation_id}:{role}:{index}"
    return "claude-" + hashlib.sha256(seed.encode()).hexdigest()[:24]


def _parse_ts(ts) -> str:
    """Parse an ISO-8601 string or return now() as fallback."""
    if ts is None:
        return datetime.now(timezone.utc).isoformat()
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            pass
    return datetime.now(timezone.utc).isoformat()


def _extract_text(content) -> str:
    """
    Extract plain text from Claude content.
    Handles strings and content block arrays: [{"type": "text", "text": "..."}].
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(p for p in parts if p)
    return ""


class ClaudeAdapter(ConversationAdapter):
    """Adapter for Claude / Anthropic formats."""

    def normalize_messages(self, raw: dict) -> list[dict]:
        # Format A: Messages API response — look for both request messages
        # and the response object
        if raw.get("type") == "message":
            return self._normalize_api_response(raw)
        # Format B: Conversation history array
        if "messages" in raw:
            return self._normalize_history(raw)
        return []

    def extract_metadata(self, raw: dict) -> dict:
        conv_id = raw.get("id") or str(uuid.uuid4())
        model = raw.get("model") or ""
        return {"conversationId": conv_id, "model": model, "title": None}

    def _normalize_api_response(self, raw: dict) -> list[dict]:
        """Normalize a Messages API response (single assistant turn)."""
        conv_id = raw.get("id") or "unknown"
        now_ts = datetime.now(timezone.utc).isoformat()
        usage = raw.get("usage") or {}

        result = []

        # Input messages are not present in the response object alone.
        # The caller should pass the full {request + response} bundle if available.
        # Here we handle just the response side.
        content = _extract_text(raw.get("content", ""))
        if content.strip():
            result.append({
                "messageId":  raw.get("id") or _make_message_id(conv_id, "assistant", 0),
                "role":       "assistant",
                "content":    content,
                "timestamp":  now_ts,
                "tokenCount": usage.get("output_tokens") or 0,
            })
        return result

    def _normalize_history(self, raw: dict) -> list[dict]:
        """Normalize a conversation history array (Format B)."""
        conv_id = raw.get("id") or "unknown"
        base_ts = _parse_ts(raw.get("created_at"))
        messages = raw.get("messages", [])

        result = []
        for i, msg in enumerate(messages):
            role = msg.get("role", "")
            if role not in ("user", "assistant"):
                continue
            content = _extract_text(msg.get("content", ""))
            if not content.strip():
                continue
            ts = _parse_ts(msg.get("created_at") or base_ts)
            usage = msg.get("usage") or {}
            result.append({
                "messageId":  msg.get("id") or _make_message_id(conv_id, role, i),
                "role":       role,
                "content":    content,
                "timestamp":  ts,
                "tokenCount": (usage.get("output_tokens") or usage.get("input_tokens") or 0),
            })
        return result


_adapter = ClaudeAdapter()


def normalize(raw: dict) -> list[dict]:
    return _adapter.normalize_messages(raw)


def extract_metadata(raw: dict) -> dict:
    return _adapter.extract_metadata(raw)
