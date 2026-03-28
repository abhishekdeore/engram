"""
Base adapter interface.

All provider adapters implement normalize_messages(raw) → list[dict],
where each dict is a valid MessageIn-compatible payload:
  {
    "messageId":  str,       # stable deterministic ID
    "role":       "user" | "assistant",
    "content":    str,       # verbatim text
    "timestamp":  str,       # ISO-8601 with timezone
    "tokenCount": int,       # 0 when not provided by the export
  }

The adapter MUST NOT generate, summarise, or modify content.
Only structural normalization is permitted.
"""

from abc import ABC, abstractmethod


class ConversationAdapter(ABC):
    """Abstract base class for all provider adapters."""

    @abstractmethod
    def normalize_messages(self, raw: dict) -> list[dict]:
        """
        Convert a provider-native conversation export into a list of
        CMF-compatible message dicts.  Must return messages in chronological
        order.  Must not modify content.
        """
        ...

    @abstractmethod
    def extract_metadata(self, raw: dict) -> dict:
        """
        Extract conversation-level metadata from the raw export.
        Returns a dict with at minimum:
          { "conversationId": str, "model": str, "title": str | None }
        """
        ...
