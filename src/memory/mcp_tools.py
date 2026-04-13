"""
MCP Tool Implementations — used by the HTTP/SSE transport.

mcp_server_http.py (Streamable HTTP, for ChatGPT Apps and other LLMs) imports
these functions.  The stdio server (mcp_server.py) does NOT import this module;
it inlines its own tool schemas and forwards requests to the Engram API server
as a lightweight HTTP client.

Functions:
  handle_memory_write  — normalise a conversation via the claude adapter, then
                         write to Neo4j using write_conversation_to_graph.
  handle_memory_query  — run the query pipeline and return a verbatim context block.

Both functions accept explicit dependencies (driver, openai_client, redis_client,
user_id) so they are fully testable without any MCP protocol layer.
"""

import logging
from neo4j import AsyncDriver

from .adapters.normalizer import normalize as dispatch_normalize
from .models.message import MessageIn
from .models.requests import QueryRequest, WriteRequest
from .services.embedding_service import embed_new_content
from .services.query_service import query_memory
from .services.write_service import write_conversation_to_graph

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Tool schemas — single source of truth for both transports
# ─────────────────────────────────────────────────────────────────────────────

MEMORY_WRITE_DESCRIPTION = (
    "Save the current conversation to persistent memory. "
    "Call this when the user explicitly asks to save, store, keep, "
    "or remember this conversation. "
    "The entire conversation is stored verbatim — no summarisation, "
    "no modification. Set the 'provider' field to identify which LLM "
    "is saving ('claude', 'gemini', 'chatgpt', etc.). "
    "Confirm to the user once saved."
)

MEMORY_WRITE_SCHEMA = {
    "type": "object",
    "properties": {
        "conversation_id": {
            "type": "string",
            "description": (
                "Stable identifier for this conversation. "
                "Use the conversation's own ID if available; "
                "otherwise generate a UUID v4."
            ),
        },
        "provider": {
            "type": "string",
            "enum": ["claude", "chatgpt", "gemini", "grok", "copilot", "custom"],
            "description": (
                "Which LLM is saving this conversation. "
                "Defaults to 'claude' if omitted."
            ),
        },
        "model": {
            "type": "string",
            "description": "The model name, e.g. 'claude-sonnet-4-6', 'gemini-2.5-pro'.",
        },
        "messages": {
            "type": "array",
            "description": "The conversation turns to save.",
            "items": {
                "type": "object",
                "properties": {
                    "role": {
                        "type": "string",
                        "enum": ["user", "assistant"],
                    },
                    "content": {"type": "string"},
                    "created_at": {
                        "type": "string",
                        "description": "ISO-8601 timestamp (optional).",
                    },
                },
                "required": ["role", "content"],
            },
            "minItems": 1,
        },
    },
    "required": ["conversation_id", "model", "messages"],
}

MEMORY_QUERY_DESCRIPTION = (
    "Search persistent memory for relevant past conversations. "
    "Call this when the user references something from a previous "
    "session, asks 'do you remember', or needs context from prior "
    "conversations with any LLM. "
    "If the user mentions a time ('last week', 'yesterday'), resolve "
    "the date to YYYY-MM-DD and pass it in the `date` field."
)

MEMORY_QUERY_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Natural language description of what to search for.",
            "minLength": 1,
            "maxLength": 2000,
        },
        "top_k": {
            "type": "integer",
            "description": "Maximum number of conversations to return (1–20).",
            "default": 5,
            "minimum": 1,
            "maximum": 20,
        },
        "token_budget": {
            "type": "integer",
            "description": "Maximum tokens in the assembled response (100–16000).",
            "default": 4000,
            "minimum": 100,
            "maximum": 16000,
        },
        "date": {
            "type": "string",
            "description": "YYYY-MM-DD — search only this exact day.",
        },
        "date_from": {
            "type": "string",
            "description": "YYYY-MM-DD — start of date range (inclusive).",
        },
        "date_to": {
            "type": "string",
            "description": "YYYY-MM-DD — end of date range (inclusive).",
        },
        "providers": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": ["chatgpt", "claude", "gemini", "grok", "copilot", "custom"],
            },
            "description": "Restrict search to specific providers. Omit for all.",
        },
        "relative_hint": {
            "type": "string",
            "description": "The original natural-language time expression the user gave.",
        },
    },
    "required": ["query"],
}


# ─────────────────────────────────────────────────────────────────────────────
# Core handler functions — transport-agnostic
# ─────────────────────────────────────────────────────────────────────────────

async def handle_memory_write(
    args: dict,
    driver: AsyncDriver,
    openai_client,
    redis_client,
    user_id: str,
) -> str:
    """
    Save a conversation to persistent memory.

    Normalises the incoming messages via the provider-appropriate adapter
    (dispatched by the normalizer module) and then calls
    write_conversation_to_graph() directly — the same function used by the
    FastAPI BackgroundTask, with the Phase 4 retry logic included.

    Phase 5B: accepts a `provider` field to dispatch to the correct adapter.
    Defaults to "claude" for backward compatibility with existing Claude installs.

    Returns a plain-text confirmation string.
    """
    # ── Validate required fields ──────────────────────────────────────────────
    conversation_id: str = (args.get("conversation_id") or "").strip()
    model: str           = (args.get("model") or "").strip()
    provider: str        = (args.get("provider") or "claude").strip().lower()
    raw_messages: list   = args.get("messages") or []

    if not conversation_id:
        raise ValueError("conversation_id is required")
    if not model:
        raise ValueError("model is required")
    if not raw_messages:
        raise ValueError("messages must contain at least one turn")

    # ── Normalise via the provider-appropriate adapter ───���────────────────────
    # Build the raw dict that the adapter expects.
    # All adapters accept: { "id": "...", "model": "...", "messages": [...] }
    # Claude/ChatGPT/Grok/Copilot use: { "id", "model", "messages": [{role, content}] }
    # Gemini uses: { "id", "model", "contents": [{role, parts: [{text}]}] }
    if provider == "gemini":
        gemini_contents = []
        for msg in raw_messages:
            role = msg.get("role", "user")
            gemini_role = "model" if role == "assistant" else role
            gemini_contents.append({
                "role": gemini_role,
                "parts": [{"text": msg.get("content", "")}],
            })
        raw = {
            "id":       conversation_id,
            "model":    model,
            "contents": gemini_contents,
        }
    else:
        raw = {
            "id":       conversation_id,
            "model":    model,
            "messages": raw_messages,
        }
    normalised = dispatch_normalize(raw, provider=provider)

    if not normalised:
        raise ValueError(
            "No storable messages found after normalisation. "
            "Ensure messages have role 'user' or 'assistant' and non-empty content."
        )

    # ── Build MessageIn objects (Pydantic validates each field) ───────────────
    try:
        message_ins = [MessageIn.model_validate(m) for m in normalised]
    except Exception as exc:
        raise ValueError(f"Message validation failed: {exc}") from exc

    # ── Construct WriteRequest ────────────────────────────────────────────────
    request = WriteRequest(
        userId=user_id,
        conversationId=conversation_id,
        provider=provider,
        model=model,
        messages=message_ins,
    )

    # ── Write (with retry) ────────────────────────────────────────────────────
    # write_conversation_to_graph is the same function used by the HTTP route.
    # For MCP we await it directly (not as a BackgroundTask) so we can confirm
    # success before responding to the caller.
    await write_conversation_to_graph(
        driver=driver,
        request=request,
        openai_client=openai_client,
        redis_client=redis_client,
    )

    logger.info(
        "mcp_write_complete conversation_id=%s messages=%d user_id=%s",
        conversation_id,
        len(message_ins),
        user_id,
    )

    return (
        f"Saved {len(message_ins)} message(s) from conversation "
        f"'{conversation_id}' to memory."
    )


async def handle_memory_query(
    args: dict,
    driver: AsyncDriver,
    openai_client,
    redis_client,
    user_id: str,
) -> str:
    """
    Search persistent memory and return a formatted verbatim context block.

    Calls query_memory() directly — the same service used by POST /memory/query.
    The returned text contains verbatim message content, never summarised or
    modified, preserving the zero-hallucination guarantee.

    Returns a plain-text context block suitable for an LLM to include in its
    response.
    """
    query: str        = (args.get("query") or "").strip()
    top_k: int        = int(args.get("top_k") or 5)
    token_budget: int = int(args.get("token_budget") or 4000)

    if not query:
        raise ValueError("query is required")

    # ── Build QueryRequest ────────────────────────────────────────────────────
    query_request = QueryRequest(
        userId=user_id,
        query=query,
        topK=max(1, min(top_k, 20)),
        tokenBudget=max(100, min(token_budget, 16000)),
        providers=args.get("providers") or None,
        date=args.get("date") or None,
        dateFrom=args.get("date_from") or None,
        dateTo=args.get("date_to") or None,
        relativeHint=args.get("relative_hint") or None,
    )

    # ── Execute retrieval pipeline ────────────────────────────────────────────
    response = await query_memory(
        driver=driver,
        openai_client=openai_client,
        redis_client=redis_client,
        request=query_request,
    )

    logger.info(
        "mcp_query_complete user_id=%s query=%r results=%d "
        "latency_ms=%.1f search_mode=%s",
        user_id,
        query[:60],
        response.totalResults,
        response.queryLatencyMs,
        response.searchMode,
    )

    # ── Format as verbatim context block ──────────────────────────────────────
    if not response.results:
        return f'No memories found matching: "{query}"'

    count = len(response.results)
    lines: list[str] = [
        f"Found {count} relevant memory entr{'y' if count == 1 else 'ies'}:\n"
    ]

    for i, conv in enumerate(response.results, start=1):
        # Header: provider, model, date
        provider_label = conv.provider.upper()
        date_label     = conv.conversationDate
        lines.append(
            f"[Memory {i} — {provider_label} ({conv.model}) — {date_label}]"
        )

        # Verbatim message turns — content is never modified
        for msg in conv.messages:
            role_label = "User" if msg.role == "user" else "Assistant"
            lines.append(f"{role_label}: {msg.content}")

        lines.append("")  # blank line between entries

    return "\n".join(lines).rstrip()
