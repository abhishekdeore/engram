"""
MCP Server — stdio transport (API client mode)
================================================
Exposes two tools to Claude Desktop via the Model Context Protocol:

  memory_write  — save a Claude conversation verbatim
  memory_query  — retrieve stored conversations via semantic search

Architecture:
  This server is a lightweight HTTP client that forwards all requests to the
  deployed Engram API server.  Users only need two environment variables:

    ENGRAM_API_URL  — the base URL of the Engram server (e.g. https://engram-production-d6d1.up.railway.app)
    ENGRAM_API_KEY  — a Bearer token issued by POST /auth/apikey

  No database credentials, no OpenAI key, no direct Neo4j connection.
  The API server handles all storage, embedding, and retrieval.

Configuration (set in claude_desktop_config.json env block):
  ENGRAM_API_URL  — required
  ENGRAM_API_KEY  — required (JWT from /auth/apikey, encodes the userId)

Usage (Claude Desktop):
  See claude_desktop_config.json in the project root.

Usage (standalone / dev):
  uv run engram-mcp
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone

import httpx
import mcp.server.stdio
import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions

# Tool descriptions and schemas are inlined here to avoid importing
# mcp_tools.py, which pulls in the full service layer and config.py.
# In API client mode, we don't need any of that — just the schemas
# for tool registration and the HTTP client for forwarding requests.

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

logger = logging.getLogger(__name__)

# ── MCP server instance ───────────────────────────────────────────────────────

server = Server("engram-memory")

# ── Module-level config (initialised in main()) ─────────────────────────────

_api_url: str = ""
_api_key: str = ""
_user_id: str = ""
_http_client: httpx.AsyncClient | None = None


# ─────────────────────────────────────────────────────────────────────────────
# API client helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _api_write(args: dict) -> str:
    """Call POST /memory/write on the Engram API server."""
    conversation_id = (args.get("conversation_id") or "").strip()
    model = (args.get("model") or "").strip()
    provider = (args.get("provider") or "claude").strip().lower()
    raw_messages = args.get("messages") or []

    if not conversation_id:
        raise ValueError("conversation_id is required")
    if not model:
        raise ValueError("model is required")
    if not raw_messages:
        raise ValueError("messages must contain at least one turn")

    # Build the CMF payload expected by POST /memory/write
    messages = []
    for i, msg in enumerate(raw_messages):
        role = msg.get("role", "user")
        content = msg.get("content", "")
        timestamp = msg.get("created_at") or datetime.now(timezone.utc).isoformat()

        messages.append({
            "messageId": f"{conversation_id}-msg-{i}",
            "role": role,
            "content": content,
            "timestamp": timestamp,
            "tokenCount": len(content.split()),
        })

    payload = {
        "userId": _user_id,
        "conversationId": conversation_id,
        "provider": provider,
        "model": model,
        "messages": messages,
    }

    resp = await _http_client.post(
        f"{_api_url}/memory/write",
        json=payload,
        headers={"Authorization": f"Bearer {_api_key}"},
        timeout=30.0,
    )

    if resp.status_code == 202:
        return f"Saved {len(messages)} message(s) from conversation '{conversation_id}' to memory."

    # Error handling
    try:
        detail = resp.json().get("detail", resp.text)
    except Exception:
        detail = resp.text
    raise RuntimeError(f"Write failed (HTTP {resp.status_code}): {detail}")


async def _api_query(args: dict) -> str:
    """Call POST /memory/query on the Engram API server."""
    query = (args.get("query") or "").strip()
    if not query:
        raise ValueError("query is required")

    payload = {
        "userId": _user_id,
        "query": query,
        "topK": max(1, min(int(args.get("top_k") or 5), 20)),
        "tokenBudget": max(100, min(int(args.get("token_budget") or 4000), 16000)),
    }

    # Optional filters
    if args.get("providers"):
        payload["providers"] = args["providers"]
    if args.get("date"):
        payload["date"] = args["date"]
    if args.get("date_from"):
        payload["dateFrom"] = args["date_from"]
    if args.get("date_to"):
        payload["dateTo"] = args["date_to"]
    if args.get("relative_hint"):
        payload["relativeHint"] = args["relative_hint"]

    resp = await _http_client.post(
        f"{_api_url}/memory/query",
        json=payload,
        headers={"Authorization": f"Bearer {_api_key}"},
        timeout=30.0,
    )

    if resp.status_code != 200:
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        raise RuntimeError(f"Query failed (HTTP {resp.status_code}): {detail}")

    data = resp.json()
    results = data.get("results", [])

    if not results:
        return f'No memories found matching: "{query}"'

    count = len(results)
    lines = [f"Found {count} relevant memory entr{'y' if count == 1 else 'ies'}:\n"]

    for i, conv in enumerate(results, start=1):
        provider_label = conv.get("provider", "unknown").upper()
        model_label = conv.get("model", "unknown")
        date_label = conv.get("conversationDate", "unknown")
        lines.append(f"[Memory {i} -- {provider_label} ({model_label}) -- {date_label}]")

        for msg in conv.get("messages", []):
            role_label = "User" if msg.get("role") == "user" else "Assistant"
            lines.append(f"{role_label}: {msg.get('content', '')}")

        lines.append("")

    return "\n".join(lines).rstrip()


# ─────────────────────────────────────────────────────────────────────────────
# MCP protocol handlers
# ─────────────────────────────────────────────────────────────────────────────

@server.list_tools()
async def _list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="memory_write",
            description=MEMORY_WRITE_DESCRIPTION,
            inputSchema=MEMORY_WRITE_SCHEMA,
        ),
        types.Tool(
            name="memory_query",
            description=MEMORY_QUERY_DESCRIPTION,
            inputSchema=MEMORY_QUERY_SCHEMA,
        ),
    ]


@server.call_tool()
async def _call_tool(
    name: str,
    arguments: dict | None,
) -> list[types.TextContent]:
    args = arguments or {}

    if name == "memory_write":
        text = await _api_write(args)
    elif name == "memory_query":
        text = await _api_query(args)
    else:
        raise ValueError(f"Unknown tool: {name!r}")

    return [types.TextContent(type="text", text=text)]


# ─────────────────────────────────────────────────────────────────────────────
# Server lifecycle
# ─────────────────────────────────────────────────────────────────────────────

def _decode_user_id_from_jwt(token: str) -> str:
    """Extract userId from JWT without verification (server verifies it)."""
    import base64
    # JWT is header.payload.signature -- decode the payload
    parts = token.split(".")
    if len(parts) != 3:
        return ""
    # Add padding
    payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return payload.get("sub", "")
    except Exception:
        return ""


async def main() -> None:
    """
    Initialise HTTP client, start the MCP server over stdio.
    """
    global _api_url, _api_key, _user_id, _http_client

    # ── Resolve config ────────────────────────────────────────────────────────
    _api_url = os.environ.get("ENGRAM_API_URL", "").strip().rstrip("/")
    _api_key = os.environ.get("ENGRAM_API_KEY", "").strip()

    if not _api_url:
        print(
            "ERROR: ENGRAM_API_URL environment variable is not set.\n"
            "Set it in claude_desktop_config.json env block.\n"
            "Example: https://engram-production-d6d1.up.railway.app",
            file=sys.stderr,
        )
        sys.exit(1)

    if not _api_key:
        print(
            "ERROR: ENGRAM_API_KEY environment variable is not set.\n"
            "Set it in claude_desktop_config.json env block.\n"
            "Get one from: POST /auth/apikey on the Engram server.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Extract userId from the API key (JWT sub claim)
    _user_id = _decode_user_id_from_jwt(_api_key)
    if not _user_id:
        print(
            "ERROR: Could not extract userId from ENGRAM_API_KEY.\n"
            "Ensure the key is a valid JWT from POST /auth/apikey.",
            file=sys.stderr,
        )
        sys.exit(1)

    # ── Configure logging ─────────────────────────────────────────────────────
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )

    # ── HTTP client ───────────────────────────────────────────────────────────
    _http_client = httpx.AsyncClient()

    # ── Verify connectivity ───────────────────────────────────────────────────
    try:
        resp = await _http_client.get(f"{_api_url}/health", timeout=10.0)
        health = resp.json()
        logger.info(
            "engram-mcp: connected to %s (version=%s, neo4j=%s)",
            _api_url,
            health.get("version", "?"),
            health.get("neo4j", "?"),
        )
    except Exception as exc:
        logger.critical("engram-mcp: cannot reach %s -- %s", _api_url, exc)
        await _http_client.aclose()
        sys.exit(1)

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
        await _http_client.aclose()


def run() -> None:
    """Synchronous entry point used by the `engram-mcp` CLI script."""
    asyncio.run(main())


if __name__ == "__main__":
    run()
