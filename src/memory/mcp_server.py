"""
MCP Server — Phase 4
======================
Exposes two tools to Claude Desktop via the Model Context Protocol:

  memory_write  — save a Claude conversation verbatim to Neo4j
  memory_query  — retrieve stored conversations via semantic search

Architecture:
  The server connects to Neo4j and OpenAI using the same Settings as the
  FastAPI service (reads from .env in the project directory).  It calls the
  existing service layer functions directly rather than going via HTTP — this
  avoids the need for a running HTTP server, eliminates JWT round-trips, and
  is architecturally cleaner for a local MCP process.

  The two core handler functions (handle_memory_write / handle_memory_query)
  are pure async functions that accept explicit dependencies, making them
  fully testable without the MCP protocol layer.

Configuration:
  MCP_USER_ID   — required; set in claude_desktop_config.json env block
                  (or in .env during development)
  All other settings come from the shared Settings object (.env / environment).

Usage (Claude Desktop):
  See claude_desktop_config.json in the project root.

Usage (standalone / dev):
  uv run engram-mcp
  uv run python src/memory/mcp_server.py
"""

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

import mcp.server.stdio
import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from neo4j import AsyncGraphDatabase, AsyncDriver
from openai import AsyncOpenAI

from .adapters.claude import normalize as claude_normalize
from .config import settings
from .models.message import MessageIn
from .models.requests import QueryRequest, WriteRequest
from .services.embedding_service import embed_new_content
from .services.query_service import query_memory
from .services.write_service import write_conversation_to_graph

logger = logging.getLogger(__name__)

# ── MCP server instance ───────────────────────────────────────────────────────

server = Server("engram-memory")

# ── Module-level shared state (initialised in main()) ─────────────────────────
# Using module-level state rather than a class because the MCP server is a
# single-process, single-user subprocess — there is no concurrency concern
# between tool calls at this level.

_driver: AsyncDriver | None = None
_openai_client: AsyncOpenAI | None = None
_redis_client = None
_user_id: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# MCP protocol handlers — thin wrappers over the testable core functions
# ─────────────────────────────────────────────────────────────────────────────

@server.list_tools()
async def _list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="memory_write",
            description=(
                "Save the current conversation to persistent memory. "
                "Call this when the user explicitly asks to save, store, keep, "
                "or remember this conversation. "
                "The entire conversation is stored verbatim — no summarisation, "
                "no modification. Confirm to the user once saved."
            ),
            inputSchema={
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
                    "model": {
                        "type": "string",
                        "description": "The model name, e.g. 'claude-sonnet-4-6'.",
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
            },
        ),
        types.Tool(
            name="memory_query",
            description=(
                "Search persistent memory for relevant past conversations. "
                "Call this when the user references something from a previous "
                "session, asks 'do you remember', or needs context from prior "
                "conversations with any LLM. "
                "If the user mentions a time ('last week', 'yesterday'), resolve "
                "the date to YYYY-MM-DD and pass it in the `date` field."
            ),
            inputSchema={
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
            },
        ),
    ]


@server.call_tool()
async def _call_tool(
    name: str,
    arguments: dict | None,
) -> list[types.TextContent]:
    args = arguments or {}

    if name == "memory_write":
        text = await handle_memory_write(
            args, _driver, _openai_client, _redis_client, _user_id
        )
    elif name == "memory_query":
        text = await handle_memory_query(
            args, _driver, _openai_client, _redis_client, _user_id
        )
    else:
        raise ValueError(f"Unknown tool: {name!r}")

    return [types.TextContent(type="text", text=text)]


# ─────────────────────────────────────────────────────────────────────────────
# Core handler functions — accept explicit dependencies so they are testable
# without the MCP protocol layer
# ─────────────────────────────────────────────────────────────────────────────

async def handle_memory_write(
    args: dict,
    driver: AsyncDriver,
    openai_client,
    redis_client,
    user_id: str,
) -> str:
    """
    Save a Claude conversation to persistent memory.

    Normalises the incoming messages via the claude.py adapter (identical to
    the server-side adapter used for POST /memory/write) and then calls
    write_conversation_to_graph() directly — the same function used by the
    FastAPI BackgroundTask, with the Phase 4 retry logic included.

    Returns a plain-text confirmation string.
    """
    # ── Validate required fields ──────────────────────────────────────────────
    conversation_id: str = (args.get("conversation_id") or "").strip()
    model: str           = (args.get("model") or "").strip()
    raw_messages: list   = args.get("messages") or []

    if not conversation_id:
        raise ValueError("conversation_id is required")
    if not model:
        raise ValueError("model is required")
    if not raw_messages:
        raise ValueError("messages must contain at least one turn")

    # ── Normalise via the claude adapter ──────────────────────────────────────
    # Build the Format B dict that claude.py expects:
    #   { "id": "...", "model": "...", "messages": [{role, content, created_at?}] }
    raw = {
        "id":       conversation_id,
        "model":    model,
        "messages": raw_messages,
    }
    normalised = claude_normalize(raw)  # pure function — no I/O

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
        provider="claude",
        model=model,
        messages=message_ins,
    )

    # ── Write (with retry) ────────────────────────────────────────────────────
    # write_conversation_to_graph is the same function used by the HTTP route.
    # For MCP we await it directly (not as a BackgroundTask) so we can confirm
    # success before responding to Claude.
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

    Returns a plain-text context block suitable for Claude to include in its
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


# ─────────────────────────────────────────────────────────────────────────────
# Server lifecycle
# ─────────────────────────────────────────────────────────────────────────────

async def main() -> None:
    """
    Initialise shared clients, start the MCP server over stdio.

    Called by the `engram-mcp` CLI entry point (via run()) or directly.
    """
    global _driver, _openai_client, _redis_client, _user_id

    # ── Resolve user identity ─────────────────────────────────────────────────
    _user_id = os.environ.get("MCP_USER_ID", "").strip()
    if not _user_id:
        print(
            "ERROR: MCP_USER_ID environment variable is not set.\n"
            "Set it in claude_desktop_config.json or in your .env file.",
            file=sys.stderr,
        )
        sys.exit(1)

    # ── Configure logging ─────────────────────────────────────────────────────
    # MCP uses stdio for protocol messages — log to stderr so it doesn't
    # corrupt the protocol stream.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )

    # ── Neo4j ─────────────────────────────────────────────────────────────────
    logger.info("engram-mcp: connecting to Neo4j at %s", settings.neo4j_uri)
    _driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_username, settings.neo4j_password),
        max_connection_pool_size=settings.neo4j_max_connection_pool_size,
        connection_timeout=settings.neo4j_connection_timeout_seconds,
    )
    try:
        await _driver.verify_connectivity()
        logger.info("engram-mcp: Neo4j connected")
    except Exception as exc:
        logger.critical("engram-mcp: Neo4j connection failed — %s", exc)
        await _driver.close()
        sys.exit(1)

    # ── OpenAI (optional) ─────────────────────────────────────────────────────
    if settings.openai_api_key:
        _openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
        logger.info(
            "engram-mcp: OpenAI ready — model=%s", settings.embedding_model
        )
    else:
        logger.warning(
            "engram-mcp: OPENAI_API_KEY not set — "
            "embedding disabled, full-text search only"
        )

    # ── Redis (optional) ──────────────────────────────────────────────────────
    if settings.redis_url:
        try:
            import redis.asyncio as aioredis
            _redis_client = aioredis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
            await _redis_client.ping()
            logger.info("engram-mcp: Redis connected")
        except Exception as exc:
            logger.warning("engram-mcp: Redis unavailable (%s) — cache disabled", exc)
            _redis_client = None

    logger.info("engram-mcp: starting server for user_id=%s", _user_id)

    # ── Run MCP server ────────────────────────────────────────────────────────
    try:
        async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                InitializationOptions(
                    server_name="engram-memory",
                    server_version="0.4.0",
                    capabilities=server.get_capabilities(
                        notification_options=NotificationOptions(),
                        experimental_capabilities={},
                    ),
                ),
            )
    finally:
        logger.info("engram-mcp: shutting down")
        await _driver.close()
        if _redis_client is not None:
            await _redis_client.aclose()


def run() -> None:
    """Synchronous entry point used by the `engram-mcp` CLI script."""
    asyncio.run(main())


if __name__ == "__main__":
    run()
