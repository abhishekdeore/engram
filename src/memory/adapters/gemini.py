"""
Gemini / Google Adapter
=======================
Normalizes Google Gemini API responses into CMF.

Format A — Gemini generateContent API response
  {
    "candidates": [{
      "content": {
        "role": "model",
        "parts": [{"text": "..."}]
      }
    }],
    "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 20}
  }
  Optionally accompanied by the request:
  {
    "contents": [
      {"role": "user",  "parts": [{"text": "..."}]},
      {"role": "model", "parts": [{"text": "..."}]}
    ],
    "model": "gemini-1.5-pro"
  }

Format B — Conversation history (contents array)
  {
    "id": "...",
    "model": "gemini-1.5-pro",
    "created_at": "...",
    "contents": [
      {"role": "user",  "parts": [{"text": "..."}]},
      {"role": "model", "parts": [{"text": "..."}]}
    ]
  }

Gemini uses "model" as the role for assistant turns.  The adapter maps
"model" → "assistant" so the stored data uses CMF's role vocabulary.
"""

import hashlib
import uuid
from datetime import datetime, timezone

from .base import ConversationAdapter


def _make_message_id(conversation_id: str, role: str, index: int) -> str:
    seed = f"{conversation_id}:{role}:{index}"
    return "gemini-" + hashlib.sha256(seed.encode()).hexdigest()[:24]


def _parse_ts(ts) -> str:
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


def _extract_text(parts) -> str:
    """Extract plain text from a Gemini parts list: [{"text": "..."}]."""
    if not parts:
        return ""
    if isinstance(parts, str):
        return parts
    return " ".join(
        p.get("text", "") for p in parts
        if isinstance(p, dict) and p.get("text")
    )


def _map_role(role: str) -> str | None:
    """Map Gemini role names to CMF roles. Returns None for non-stored roles."""
    if role == "user":
        return "user"
    if role in ("model", "assistant"):
        return "assistant"
    return None


class GeminiAdapter(ConversationAdapter):
    """Adapter for Gemini / Google formats."""

    def normalize_messages(self, raw: dict) -> list[dict]:
        # Format A: generateContent response — may have request contents too
        if "candidates" in raw:
            return self._normalize_generate_response(raw)
        # Format B: conversation history (contents array)
        if "contents" in raw:
            return self._normalize_contents(raw)
        return []

    def extract_metadata(self, raw: dict) -> dict:
        conv_id = raw.get("id") or str(uuid.uuid4())
        model = raw.get("model") or raw.get("modelVersion") or ""
        return {"conversationId": conv_id, "model": model, "title": None}

    def _normalize_generate_response(self, raw: dict) -> list[dict]:
        """Normalize generateContent API response + optional request contents."""
        conv_id = raw.get("id") or "unknown"
        now_ts = datetime.now(timezone.utc).isoformat()
        usage = raw.get("usageMetadata") or {}
        result = []
        index = 0

        # Request-side contents (if provided in the same bundle)
        for msg in raw.get("contents", []):
            role = _map_role(msg.get("role", ""))
            if role is None:
                continue
            content = _extract_text(msg.get("parts", []))
            if not content.strip():
                continue
            result.append({
                "messageId":  _make_message_id(conv_id, role, index),
                "role":       role,
                "content":    content,
                "timestamp":  now_ts,
                "tokenCount": 0,
            })
            index += 1

        # Response candidates (first candidate only — best response)
        for candidate in raw.get("candidates", [])[:1]:
            content_obj = candidate.get("content") or {}
            role = _map_role(content_obj.get("role", "model"))
            if role is None:
                continue
            content = _extract_text(content_obj.get("parts", []))
            if not content.strip():
                continue
            result.append({
                "messageId":  _make_message_id(conv_id, role, index),
                "role":       role,
                "content":    content,
                "timestamp":  now_ts,
                "tokenCount": usage.get("candidatesTokenCount") or 0,
            })
            index += 1

        return result

    def _normalize_contents(self, raw: dict) -> list[dict]:
        """Normalize a conversation history (contents array)."""
        conv_id = raw.get("id") or "unknown"
        base_ts = _parse_ts(raw.get("created_at"))
        result = []

        for i, msg in enumerate(raw.get("contents", [])):
            role = _map_role(msg.get("role", ""))
            if role is None:
                continue
            content = _extract_text(msg.get("parts", []))
            if not content.strip():
                continue
            ts = _parse_ts(msg.get("timestamp") or base_ts)
            result.append({
                "messageId":  _make_message_id(conv_id, role, i),
                "role":       role,
                "content":    content,
                "timestamp":  ts,
                "tokenCount": 0,
            })
        return result


_adapter = GeminiAdapter()


def normalize(raw: dict) -> list[dict]:
    return _adapter.normalize_messages(raw)


def extract_metadata(raw: dict) -> dict:
    return _adapter.extract_metadata(raw)
