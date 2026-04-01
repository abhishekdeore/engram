"""
MCP Server — HTTP/SSE transport (Streamable HTTP)
===================================================
Exposes the same memory_write and memory_query tools as mcp_server.py (stdio),
but over the MCP Streamable HTTP transport.  This is the production integration
path for ChatGPT Apps, Gemini Extensions, and any MCP-over-HTTP client.

Tool logic is imported from mcp_tools.py — zero duplication with the stdio
server.

Architecture:
  - Uses the low-level mcp.server.Server class (same as the stdio server) with
    StreamableHTTPSessionManager as the transport layer.
  - Runs as a standalone Starlette + Uvicorn process on MCP_HTTP_PORT (default
    8001), separate from the FastAPI service on port 8000.
  - Auth: Bearer token verified per-request via decode_access_token().  The
    userId from the token is stored in a contextvars.ContextVar so the tool
    dispatch can read it without threading through the MCP call stack.
  - stateless=True: each HTTP request gets a fresh transport instance.  No
    session state is kept between requests — correct for multi-user, multi-LLM
    deployments.

Configuration (env vars or .env):
  MCP_HTTP_PORT      — TCP port to listen on (default: 8001, via settings)
  MCP_HTTP_HOST      — bind address (default: 0.0.0.0, via settings)
  All Neo4j / OpenAI / Redis / JWT settings from the shared Settings object.

Usage (standalone):
  uv run engram-mcp-http
  uv run python src/memory/mcp_server_http.py

Entry point:
  engram-mcp-http  →  memory.mcp_server_http:run  (in pyproject.toml)
"""

import asyncio
import contextvars
import logging
import sys
from contextlib import asynccontextmanager
from http import HTTPStatus

import mcp.types as types
import uvicorn
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.fastmcp.server import StreamableHTTPASGIApp
from neo4j import AsyncGraphDatabase, AsyncDriver
from openai import AsyncOpenAI
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .auth.jwt_handler import decode_access_token
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

# ── Per-request context: user identity injected by auth middleware ─────────────
# ContextVar is the standard mechanism for passing request-scoped data through
# async call chains without explicit parameter threading.
_request_user_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_request_user_id", default=""
)

# ── Shared driver / client — initialised in Starlette lifespan ────────────────
_driver: AsyncDriver | None = None
_openai_client: AsyncOpenAI | None = None
_redis_client = None


# ─────────────────────────────────────────────────────────────────────────────
# MCP server instance + tool handlers
# ─────────────────────────────────────────────────────────────────────────────

server = Server("engram-memory-http")


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
    user_id = _request_user_id.get()

    if not user_id:
        # Should never happen — auth middleware always sets this before the
        # MCP session starts.  Guard defensively.
        raise ValueError("Authenticated user_id not available in request context")

    if name == "memory_write":
        text = await handle_memory_write(
            args, _driver, _openai_client, _redis_client, user_id
        )
    elif name == "memory_query":
        text = await handle_memory_query(
            args, _driver, _openai_client, _redis_client, user_id
        )
    else:
        raise ValueError(f"Unknown tool: {name!r}")

    return [types.TextContent(type="text", text=text)]


# ─────────────────────────────────────────────────────────────────────────────
# Auth middleware
# ─────────────────────────────────────────────────────────────────────────────

class BearerAuthMiddleware(BaseHTTPMiddleware):
    """
    Validates the Authorization: Bearer <token> header on every MCP request.

    On success: sets _request_user_id ContextVar and forwards to the MCP handler.
    On failure: returns 401 JSON immediately — the MCP session is never started.

    The /health path is exempted so operators can probe liveness without a token.
    """

    EXEMPT_PATHS = {"/health"}

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path in self.EXEMPT_PATHS:
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.lower().startswith("bearer "):
            return JSONResponse(
                status_code=HTTPStatus.UNAUTHORIZED.value,
                content={"error": "Missing or malformed Authorization header"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = auth_header[len("bearer "):].strip()
        try:
            user_id = decode_access_token(token)
        except ValueError as exc:
            return JSONResponse(
                status_code=HTTPStatus.UNAUTHORIZED.value,
                content={"error": str(exc)},
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Inject user_id into the ContextVar for this request's async context
        token_var = _request_user_id.set(user_id)
        try:
            return await call_next(request)
        finally:
            _request_user_id.reset(token_var)


# ─────────────────────────────────────────────────────────────────────────────
# Starlette application factory
# ─────────────────────────────────────────────────────────────────────────────

def _build_starlette_app(session_manager: StreamableHTTPSessionManager) -> Starlette:
    """
    Build the Starlette ASGI app that wraps the MCP session manager.

    Routes:
      /mcp     — MCP Streamable HTTP endpoint (all MCP traffic goes here)
      /health  — plain liveness probe (no auth required)
    """
    mcp_asgi = StreamableHTTPASGIApp(session_manager)

    async def health(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "engram-mcp-http"})

    @asynccontextmanager
    async def lifespan(_app: Starlette):
        async with session_manager.run():
            yield

    app = Starlette(
        routes=[
            Route("/mcp", endpoint=mcp_asgi),
            Route("/health", endpoint=health),
        ],
        middleware=[
            # Starlette applies middleware outermost-first on the way in.
            # BearerAuthMiddleware runs before every request reaches the MCP
            # handler — auth failures are short-circuited here.
            Middleware(BearerAuthMiddleware),
        ],
        lifespan=lifespan,
    )
    return app


# ─────────────────────────────────────────────────────────────────────────────
# Server lifecycle
# ─────────────────────────────────────────────────────────────────────────────

async def _init_clients() -> None:
    """Initialise and verify Neo4j, OpenAI, and Redis clients."""
    global _driver, _openai_client, _redis_client

    # ── Neo4j ─────────────────────────────────────────────────────────────────
    logger.info("engram-mcp-http: connecting to Neo4j at %s", settings.neo4j_uri)
    _driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_username, settings.neo4j_password),
        max_connection_pool_size=settings.neo4j_max_connection_pool_size,
        connection_timeout=settings.neo4j_connection_timeout_seconds,
    )
    try:
        await _driver.verify_connectivity()
        logger.info("engram-mcp-http: Neo4j connected")
    except Exception as exc:
        logger.critical("engram-mcp-http: Neo4j connection failed — %s", exc)
        await _driver.close()
        raise

    # ── OpenAI (optional) ─────────────────────────────────────────────────────
    if settings.openai_api_key:
        _openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
        logger.info(
            "engram-mcp-http: OpenAI ready — model=%s", settings.embedding_model
        )
    else:
        logger.warning(
            "engram-mcp-http: OPENAI_API_KEY not set — "
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
            logger.info("engram-mcp-http: Redis connected")
        except Exception as exc:
            logger.warning(
                "engram-mcp-http: Redis unavailable (%s) — cache disabled", exc
            )
            _redis_client = None


async def _shutdown_clients() -> None:
    """Close Neo4j and Redis connections."""
    if _driver is not None:
        await _driver.close()
    if _redis_client is not None:
        await _redis_client.aclose()
    logger.info("engram-mcp-http: clients closed")


async def main() -> None:
    """
    Initialise clients, build the Starlette app, and serve via uvicorn.

    Called by the `engram-mcp-http` CLI entry point (via run()) or directly.
    """
    # ── Logging ───────────────────────────────────────────────────────────────
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )

    # ── Clients ───────────────────────────────────────────────────────────────
    try:
        await _init_clients()
    except Exception:
        logger.critical("engram-mcp-http: failed to initialise clients, exiting")
        sys.exit(1)

    # ── Session manager ───────────────────────────────────────────────────────
    # stateless=True: each HTTP request creates a fresh transport.  Correct for
    # a multi-user server where each request carries its own auth token.
    session_manager = StreamableHTTPSessionManager(
        app=server,
        json_response=False,   # use SSE streams (standard MCP over HTTP)
        stateless=True,
    )

    # ── Starlette app ─────────────────────────────────────────────────────────
    starlette_app = _build_starlette_app(session_manager)

    logger.info(
        "engram-mcp-http: starting on %s:%d",
        settings.mcp_http_host,
        settings.mcp_http_port,
    )

    # ── Uvicorn ───────────────────────────────────────────────────────────────
    config = uvicorn.Config(
        starlette_app,
        host=settings.mcp_http_host,
        port=settings.mcp_http_port,
        log_level="info",
    )
    uv_server = uvicorn.Server(config)
    try:
        await uv_server.serve()
    finally:
        await _shutdown_clients()


def run() -> None:
    """Synchronous entry point used by the `engram-mcp-http` CLI script."""
    asyncio.run(main())


if __name__ == "__main__":
    run()
