from neo4j import GraphDatabase, Driver, AsyncGraphDatabase, AsyncDriver
from neo4j.exceptions import ServiceUnavailable, AuthError

from ..config import settings


# ── Sync driver (used by scripts and setup tasks) ────────────────────────────

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


# ── Async driver (used by FastAPI in Phase 1) ────────────────────────────────

_async_driver: AsyncDriver | None = None


def get_async_driver() -> AsyncDriver:
    global _async_driver
    if _async_driver is None:
        _async_driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_username, settings.neo4j_password),
            max_connection_pool_size=settings.neo4j_max_connection_pool_size,
            connection_timeout=settings.neo4j_connection_timeout_seconds,
        )
    return _async_driver


async def close_async_driver() -> None:
    global _async_driver
    if _async_driver is not None:
        await _async_driver.close()
        _async_driver = None


async def verify_async_connectivity() -> bool:
    driver = get_async_driver()
    await driver.verify_connectivity()
    return True
