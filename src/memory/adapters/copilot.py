"""
GitHub Copilot / Microsoft Adapter
====================================
Normalizes GitHub Copilot Chat conversation exports into CMF.

Copilot Chat embeds a VS Code extension whose conversation history can be
exported as JSON. The format varies between GitHub.com Copilot and VS Code
Copilot — this adapter handles both.

Format A — VS Code Copilot Chat export
  {
    "version": 1,
    "requests": [
      {
        "message": "user question",
        "response": [{"value": "assistant response"}],
        "agent": "@workspace",
        "timestamp": 1717000000000   ← milliseconds
      }
    ],
    "model": "gpt-4o"
  }

Format B — Generic turns array (github.com export or CI API)
  {
    "id": "...",
    "model": "gpt-4o-copilot",
    "created_at": "2024-01-15T10:00:00Z",
    "turns": [
      {"role": "user",      "content": "...", "created_at": "..."},
      {"role": "assistant", "content": "...", "created_at": "..."}
    ]
  }
"""

import hashlib
import uuid
from datetime import datetime, timezone

from .base import ConversationAdapter


def _make_message_id(conversation_id: str, role: str, index: int) -> str:
    seed = f"{conversation_id}:{role}:{index}"
    return "copilot-" + hashlib.sha256(seed.encode()).hexdigest()[:24]


def _ms_to_iso(ts_ms) -> str:
    """Convert millisecond Unix timestamp to ISO-8601."""
    if ts_ms is None:
        return datetime.now(timezone.utc).isoformat()
    try:
        return datetime.fromtimestamp(float(ts_ms) / 1000.0, tz=timezone.utc).isoformat()
    except (ValueError, TypeError, OSError):
        return datetime.now(timezone.utc).isoformat()


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


def _extract_response_text(response) -> str:
    """Extract text from VS Code Copilot response field (list of value objects)."""
    if isinstance(response, str):
        return response
    if isinstance(response, list):
        return "\n".join(
            item.get("value", "") for item in response
            if isinstance(item, dict) and item.get("value")
        )
    return ""


class CopilotAdapter(ConversationAdapter):
    """Adapter for GitHub Copilot Chat formats."""

    def normalize_messages(self, raw: dict) -> list[dict]:
        # Format A: VS Code requests array
        if "requests" in raw:
            return self._normalize_vscode(raw)
        # Format B: turns array
        if "turns" in raw:
            return self._normalize_turns(raw)
        return []

    def extract_metadata(self, raw: dict) -> dict:
        conv_id = raw.get("id") or str(uuid.uuid4())
        model = raw.get("model") or ""
        return {"conversationId": conv_id, "model": model, "title": None}

    def _normalize_vscode(self, raw: dict) -> list[dict]:
        """Normalize VS Code Copilot Chat export (requests array)."""
        conv_id = raw.get("id") or str(uuid.uuid4())
        result = []

        for i, req in enumerate(raw.get("requests", [])):
            ts = _ms_to_iso(req.get("timestamp"))

            # User turn
            user_content = req.get("message", "")
            if user_content.strip():
                result.append({
                    "messageId":  _make_message_id(conv_id, "user", i * 2),
                    "role":       "user",
                    "content":    user_content,
                    "timestamp":  ts,
                    "tokenCount": 0,
                })

            # Assistant turn
            assistant_content = _extract_response_text(req.get("response", ""))
            if assistant_content.strip():
                result.append({
                    "messageId":  _make_message_id(conv_id, "assistant", i * 2 + 1),
                    "role":       "assistant",
                    "content":    assistant_content,
                    "timestamp":  ts,
                    "tokenCount": 0,
                })

        return result

    def _normalize_turns(self, raw: dict) -> list[dict]:
        """Normalize turns array format (github.com / API export)."""
        conv_id = raw.get("id") or "unknown"
        base_ts = _parse_ts(raw.get("created_at"))
        result = []

        for i, turn in enumerate(raw.get("turns", [])):
            role = turn.get("role", "")
            if role not in ("user", "assistant"):
                continue
            content = turn.get("content", "")
            if not content.strip():
                continue
            ts = _parse_ts(turn.get("created_at") or base_ts)
            result.append({
                "messageId":  _make_message_id(conv_id, role, i),
                "role":       role,
                "content":    content,
                "timestamp":  ts,
                "tokenCount": 0,
            })
        return result


_adapter = CopilotAdapter()


def normalize(raw: dict) -> list[dict]:
    return _adapter.normalize_messages(raw)


def extract_metadata(raw: dict) -> dict:
    return _adapter.extract_metadata(raw)
