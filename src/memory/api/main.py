"""
FastAPI application — Cross-LLM Memory Service

Start with:
    uv run uvicorn memory.api.main:app --reload --host 0.0.0.0 --port 8000

Or for production (no reload):
    uv run uvicorn memory.api.main:app --host 0.0.0.0 --port 8000 --workers 1
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from neo4j import AsyncGraphDatabase

from ..config import settings
from .routes import health, auth, memory

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
    Manages the async Neo4j driver for the application lifetime.

    Startup:
      - Create the async driver
      - Verify connectivity (fails fast if Neo4j is unreachable)
      - Store in app.state so routes can access it via get_neo4j_driver()

    Shutdown:
      - Close the driver (Neo4j 6.x: __del__ no longer closes — must be explicit)
    """
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
        raise   # crash fast; don't serve requests with no DB

    app.state.neo4j_driver = driver

    yield   # ← application runs here

    logger.info("shutdown: closing Neo4j driver")
    await driver.close()
    logger.info("shutdown: complete")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Cross-LLM Memory Service",
    description=(
        "Verbatim conversation storage and retrieval across LLM providers. "
        "Conversations are stored word-for-word in Neo4j and retrieved via "
        "semantic vector search. Zero hallucination — the memory pipeline "
        "never calls a generative model."
    ),
    version="0.1.0",
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


# ── Root ──────────────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def root():
    return {
        "service": "cross-llm-memory",
        "version": "0.1.0",
        "docs": "/docs",
        "health": "/health",
    }
