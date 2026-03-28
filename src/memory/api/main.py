"""
FastAPI application — Cross-LLM Memory Service (Engram)

Start with:
    uv run uvicorn memory.api.main:app --reload --host 0.0.0.0 --port 8000

Or for production:
    uv run uvicorn memory.api.main:app --host 0.0.0.0 --port 8000 --workers 1
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from neo4j import AsyncGraphDatabase
from openai import AsyncOpenAI

from ..config import settings
from .routes import health, auth, memory, query

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

    Neo4j (required):
      Fails fast on startup if unreachable.

    OpenAI (optional):
      Created only when OPENAI_API_KEY is set.
      If absent, the embedding pipeline is a no-op and full-text search
      is used as the sole retrieval path.

    Redis (optional):
      Created only when REDIS_URL is set.  Connection errors during startup
      are logged and silenced — Redis is an embedding cache, not a hard
      dependency.  If unavailable, embeddings are computed fresh on every call.
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
    # AsyncOpenAI has no explicit close method; the underlying httpx client
    # is cleaned up automatically.
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
    version="0.2.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)


# ── CORS ──────────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.is_development else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(memory.router)
app.include_router(query.router)


# ── Root ──────────────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def root():
    return {
        "service": "engram",
        "version": "0.2.0",
        "docs": "/docs",
        "health": "/health",
    }
