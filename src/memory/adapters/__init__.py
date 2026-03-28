"""
Provider Adapters — Phase 3
============================
Thin normalizers that convert each LLM provider's native conversation format
into Canonical Message Format (CMF) — the only format the memory service
accepts for writing.

Each adapter is a pure function: no I/O, no external calls, no side effects.
All output goes through WriteRequest Pydantic validation before any write.

Usage:
    from memory.adapters import normalize

    write_payload = normalize(raw_export, provider="chatgpt")
    # → dict compatible with WriteRequest

Supported providers: chatgpt, claude, gemini, grok, copilot
"""

from .normalizer import normalize, AdapterError

__all__ = ["normalize", "AdapterError"]
