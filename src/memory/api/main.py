"""
FastAPI application — Cross-LLM Memory Service (Engram)

Start with:
    uv run uvicorn memory.api.main:app --reload --host 0.0.0.0 --port 8000

Or for production:
    uv run uvicorn memory.api.main:app --host 0.0.0.0 --port 8000 --workers 1

NOTE: slowapi uses an in-memory counter store.  With --workers > 1 each worker
has an independent counter so the per-minute limit becomes limit × workers.
Keep workers=1 for exact rate limiting, or switch to a Redis-backed limiter.
"""

import logging
import re
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from neo4j import AsyncGraphDatabase
from openai import AsyncOpenAI
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from ..config import settings
from ..exceptions import EngramError
from .limiter import limiter
from .routes import health, auth, memory, query, chatgpt

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.DEBUG if settings.is_development else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


# ── Lifespan — startup / shutdown ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Initialises and tears down all shared clients.

    Neo4j (required): Fails fast on startup if unreachable.
    OpenAI (optional): Created only when OPENAI_API_KEY is set.
    Redis (optional): Created only when REDIS_URL is set.
    """
    # ── Neo4j ─────────────────────────────────────────────────────────────────
    logger.info("startup: connecting to Neo4j at %s", settings.neo4j_uri)
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_username, settings.neo4j_password),
        max_connection_pool_size=settings.neo4j_max_connection_pool_size,
        connection_timeout=settings.neo4j_connection_timeout_seconds,
    )
    try:
        await driver.verify_connectivity()
        logger.info("startup: Neo4j connected — database=%s", settings.neo4j_database)
    except Exception as exc:
        logger.critical("startup: Neo4j connection failed — %s", exc)
        await driver.close()
        raise
    app.state.neo4j_driver = driver

    # ── OpenAI ────────────────────────────────────────────────────────────────
    openai_client = None
    if settings.openai_api_key:
        openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
        logger.info(
            "startup: OpenAI client ready — model=%s", settings.embedding_model
        )
    else:
        logger.warning(
            "startup: OPENAI_API_KEY not set — "
            "embedding pipeline disabled, full-text search only"
        )
    app.state.openai_client = openai_client

    # ── Redis (optional) ──────────────────────────────────────────────────────
    redis_client = None
    if settings.redis_url:
        try:
            import redis.asyncio as aioredis
            redis_client = aioredis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
            await redis_client.ping()
            logger.info("startup: Redis connected — %s", settings.redis_url)
        except Exception as exc:
            logger.warning(
                "startup: Redis unavailable (%s) — embedding cache disabled", exc
            )
            redis_client = None
    else:
        logger.info("startup: REDIS_URL not set — embedding cache disabled")
    app.state.redis_client = redis_client

    yield   # ← application runs here

    # ── Shutdown ──────────────────────────────────────────────────────────────
    logger.info("shutdown: closing clients")
    await driver.close()
    if redis_client is not None:
        await redis_client.aclose()
    logger.info("shutdown: complete")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Engram — Cross-LLM Memory Service",
    description=(
        "Verbatim conversation storage and retrieval across LLM providers. "
        "Conversations are stored word-for-word in Neo4j and retrieved via "
        "semantic vector search. Zero hallucination — the memory pipeline "
        "never calls a generative model."
    ),
    version="0.5.0",
    lifespan=lifespan,
    # Phase 6: disable Swagger/ReDoc in production to reduce attack surface
    docs_url="/docs" if settings.is_development else None,
    redoc_url="/redoc" if settings.is_development else None,
)

# Attach limiter to app state (required by slowapi)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ── Engram structured error handler ─────────────────────────────────────────

@app.exception_handler(EngramError)
async def engram_error_handler(request: Request, exc: EngramError):
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error_code": exc.error_code,
            "message": exc.message,
            "details": exc.details,
        },
    )


# ── Security headers + correlation ID middleware ─────────────────────────────

@app.middleware("http")
async def security_and_correlation_middleware(request: Request, call_next):
    """
    Phase 6 hardened middleware:
    1. Attach a sanitised correlation ID (alphanumeric + dash/underscore, max 36 chars).
    2. Add security response headers (HSTS, X-Frame-Options, etc.).
    """
    # ── Correlation ID ────────────────────────────────────────────────────
    raw_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    # Phase 6: strict sanitisation — alphanumeric, dash, underscore only
    request_id = re.sub(r"[^a-zA-Z0-9\-_]", "", raw_id)[:36]
    if not request_id:
        request_id = str(uuid.uuid4())

    request.state.request_id = request_id
    response = await call_next(request)

    # ── Correlation ID echo ───────────────────────────────────────────────
    response.headers["X-Request-ID"] = request_id

    # ── Security headers (Phase 6) ───────────────────────────────────────
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    if settings.is_production:
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )

    return response


# ── CORS (Phase 6: restricted in production) ─────────────────────────────────

_DEV_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:8000",
    "http://localhost:8001",
]

_PROD_ORIGINS = [
    origin for origin in (settings.cors_allowed_origins or "").split(",")
    if origin.strip()
] if hasattr(settings, "cors_allowed_origins") and settings.cors_allowed_origins else []

app.add_middleware(
    CORSMiddleware,
    allow_origins=_DEV_ORIGINS if settings.is_development else _PROD_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    max_age=600,
)


# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(memory.router)
app.include_router(query.router)
app.include_router(chatgpt.router)


# ── Root ──────────────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def root():
    return {
        "service": "engram",
        "version": "0.5.0",
        "docs": "/docs",
        "health": "/health",
    }
