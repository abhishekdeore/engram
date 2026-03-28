"""
Normalizer — dispatch layer
============================
Single entry point for all provider adapters.

    from memory.adapters import normalize

    cmf_messages = normalize(raw_export, provider="chatgpt")

The returned list of dicts is directly usable as the `messages` field in a
WriteRequest. The caller is responsible for supplying userId, conversationId,
and model before constructing the final WriteRequest.
"""

from . import chatgpt, claude, gemini, grok, copilot

_ADAPTERS = {
    "chatgpt": chatgpt,
    "claude":  claude,
    "gemini":  gemini,
    "grok":    grok,
    "copilot": copilot,
}


class AdapterError(ValueError):
    """Raised when the requested provider adapter does not exist."""


def normalize(raw: dict, provider: str) -> list[dict]:
    """
    Normalize a provider-native conversation export to a list of CMF message dicts.

    Args:
        raw:      The raw export dict from the provider.
        provider: One of: chatgpt, claude, gemini, grok, copilot.

    Returns:
        List of dicts, each compatible with MessageIn:
          {"messageId": str, "role": str, "content": str,
           "timestamp": str, "tokenCount": int}

    Raises:
        AdapterError: if provider is not supported.
        ValueError:   if the raw export is not a dict.
    """
    if not isinstance(raw, dict):
        raise ValueError(f"Expected a dict, got {type(raw).__name__}")

    adapter_module = _ADAPTERS.get(provider.lower())
    if adapter_module is None:
        supported = ", ".join(sorted(_ADAPTERS.keys()))
        raise AdapterError(
            f"No adapter for provider '{provider}'. Supported: {supported}"
        )

    messages = adapter_module.normalize(raw)

    # Filter out any messages with empty content or invalid roles — defense in depth
    return [
        m for m in messages
        if m.get("role") in ("user", "assistant")
        and isinstance(m.get("content"), str)
        and m["content"].strip()
    ]


def supported_providers() -> list[str]:
    """Return the list of providers that have a registered adapter."""
    return sorted(_ADAPTERS.keys())
