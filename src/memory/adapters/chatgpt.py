"""
ChatGPT / OpenAI Adapter
========================
Normalizes two ChatGPT export formats into CMF:

Format A — Chat Completions API response
  {
    "id": "chatcmpl-...",
    "model": "gpt-4o",
    "created": 1717000000,
    "choices": [{"message": {"role": "...", "content": "..."}}],
    "messages": [{"role": "...", "content": "..."}]   ← input messages
  }

Format B — conversations.json export (from data export / GDPR download)
  {
    "id": "...",
    "title": "...",
    "create_time": 1717000000,
    "update_time": 1717000000,
    "mapping": {
      "<node_id>": {
        "id": "...",
        "message": {
          "id": "...",
          "author": {"role": "user"|"assistant"|"system"|"tool"},
          "create_time": 1717000000.0,
          "content": {"content_type": "text", "parts": ["..."]},
          "metadata": {"model_slug": "gpt-4o"}
        },
        "parent": "<parent_node_id>",
        "children": ["<child_node_id>"]
      }
    }
  }

System and tool messages are excluded — only user/assistant turns are stored.
"""

import hashlib
from datetime import datetime, timezone

from .base import ConversationAdapter


def _make_message_id(conversation_id: str, role: str, index: int) -> str:
    """Stable deterministic message ID from conversation context."""
    seed = f"{conversation_id}:{role}:{index}"
    return "chatgpt-" + hashlib.sha256(seed.encode()).hexdigest()[:24]


def _unix_to_iso(ts) -> str:
    """Convert a Unix timestamp (int or float) to ISO-8601 with UTC timezone."""
    if ts is None:
        return datetime.now(timezone.utc).isoformat()
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except (ValueError, TypeError, OSError):
        return datetime.now(timezone.utc).isoformat()


def _extract_text(content) -> str:
    """
    Extract plain text from a ChatGPT content object.
    Handles both string content and {"content_type": "text", "parts": [...]} dicts.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        parts = content.get("parts", [])
        return " ".join(str(p) for p in parts if p)
    return ""


class ChatGPTAdapter(ConversationAdapter):
    """Adapter for ChatGPT / OpenAI formats."""

    def normalize_messages(self, raw: dict) -> list[dict]:
        # Format B: conversations.json mapping structure
        if "mapping" in raw:
            return self._normalize_mapping(raw)
        # Format A: Chat Completions API / simple message list
        return self._normalize_completions(raw)

    def extract_metadata(self, raw: dict) -> dict:
        conv_id = raw.get("id") or raw.get("conversation_id") or ""
        model = raw.get("model") or ""
        title = raw.get("title")
        return {"conversationId": conv_id, "model": model, "title": title}

    def _normalize_completions(self, raw: dict) -> list[dict]:
        """Handle OpenAI Chat Completions API format."""
        conv_id = raw.get("id") or raw.get("conversation_id") or "unknown"
        created = raw.get("created")
        base_ts = _unix_to_iso(created)

        # Collect input messages + assistant reply
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
            content = _extract_text(msg.get("content", ""))
            if not content.strip():
                continue
            result.append({
                "messageId":  msg.get("id") or _make_message_id(conv_id, role, i),
                "role":       role,
                "content":    content,
                "timestamp":  base_ts,
                "tokenCount": 0,
            })
        return result

    def _normalize_mapping(self, raw: dict) -> list[dict]:
        """Handle conversations.json mapping format (GDPR export)."""
        conv_id = raw.get("id") or "unknown"
        mapping = raw.get("mapping", {})

        # Build parent→children graph and find root
        nodes = {}
        for node_id, node in mapping.items():
            msg = node.get("message")
            if msg is None:
                continue
            role = (msg.get("author") or {}).get("role", "")
            if role not in ("user", "assistant"):
                continue
            content = _extract_text(msg.get("content", ""))
            if not content.strip():
                continue
            ts = _unix_to_iso(msg.get("create_time"))
            nodes[node_id] = {
                "messageId":  msg.get("id") or _make_message_id(conv_id, role, len(nodes)),
                "role":       role,
                "content":    content,
                "timestamp":  ts,
                "tokenCount": 0,
                "parent":     node.get("parent"),
                "_create_time": msg.get("create_time") or 0,
            }

        # Sort by create_time to get chronological order
        ordered = sorted(nodes.values(), key=lambda n: n["_create_time"])
        for n in ordered:
            n.pop("parent", None)
            n.pop("_create_time", None)
        return ordered


# Module-level convenience instance
_adapter = ChatGPTAdapter()


def normalize(raw: dict) -> list[dict]:
    """Normalize a ChatGPT export to a list of CMF message dicts."""
    return _adapter.normalize_messages(raw)


def extract_metadata(raw: dict) -> dict:
    """Extract metadata from a ChatGPT export."""
    return _adapter.extract_metadata(raw)
