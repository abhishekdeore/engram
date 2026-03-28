"""
Neo4j sync driver — used by scripts and tests only.

The async driver used by FastAPI is created in the lifespan function in
api/main.py and stored in app.state.neo4j_driver.  It is never instantiated
here — the old async singleton functions have been removed (P3-PRE-10).
"""

from neo4j import GraphDatabase, Driver
from neo4j.exceptions import ServiceUnavailable, AuthError  # noqa: F401 — re-exported for callers

from ..config import settings


# ── Sync driver (scripts, tests, setup tasks) ────────────────────────────────

_sync_driver: Driver | None = None


def get_driver() -> Driver:
    global _sync_driver
    if _sync_driver is None:
        _sync_driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_username, settings.neo4j_password),
            max_connection_pool_size=settings.neo4j_max_connection_pool_size,
            connection_timeout=settings.neo4j_connection_timeout_seconds,
        )
    return _sync_driver


def close_driver() -> None:
    global _sync_driver
    if _sync_driver is not None:
        _sync_driver.close()
        _sync_driver = None


def verify_connectivity() -> bool:
    """
    Confirm Neo4j is reachable and credentials are valid.
    Raises ServiceUnavailable or AuthError on failure.
    """
    driver = get_driver()
    driver.verify_connectivity()
    return True
