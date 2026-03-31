"""
MCP Server — Phase 4 (stdio transport)
========================================
Exposes two tools to Claude Desktop via the Model Context Protocol:

  memory_write  — save a Claude conversation verbatim to Neo4j
  memory_query  — retrieve stored conversations via semantic search

Architecture:
  The server connects to Neo4j and OpenAI using the same Settings as the
  FastAPI service (reads from .env in the project directory).  It calls the
  existing service layer functions directly rather than going via HTTP — this
  avoids the need for a running HTTP server, eliminates JWT round-trips, and
  is architecturally cleaner for a local MCP process.

  Tool logic lives in mcp_tools.py — shared with mcp_server_http.py (the
  HTTP/SSE transport used by ChatGPT Apps).  This file is the stdio-only
  transport wrapper.

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

import mcp.server.stdio
import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from neo4j import AsyncGraphDatabase, AsyncDriver
from openai import AsyncOpenAI

from .config import settings
from .mcp_tools import (
    handle_memory_write,
    handle_memory_query,
    MEMORY_WRITE_DESCRIPTION,
    MEMORY_WRITE_SCHEMA,
    MEMORY_QUERY_DESCRIPTION,
    MEMORY_QUERY_SCHEMA,
)

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
