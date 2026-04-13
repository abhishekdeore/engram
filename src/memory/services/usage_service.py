"""
Usage Service — Phase 6 / Phase 6B
====================================
Tracks and enforces daily query limits per user.

Phase 6B: Redis-backed counters when REDIS_URL is set (production).
Falls back to in-memory dict when Redis is unavailable (dev/testing).
Redis key format: engram:daily_query:{userId}:{YYYY-MM-DD}
TTL: 86400 seconds (auto-expires, no cleanup needed).
Atomic increment via INCR command.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from neo4j import AsyncDriver

from ..config import settings

logger = logging.getLogger(__name__)

_DAILY_QUERY_TTL = 86400  # seconds — 24 hours

# ── In-memory daily query counter (fallback) ─────────────────────────────────
# Key: "userId:YYYY-MM-DD" → count
# Reset happens naturally: a new date key is created each day.
_daily_query_counts: dict[str, int] = {}


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _daily_key(user_id: str) -> str:
    """In-memory dict key."""
    return f"{user_id}:{_today_str()}"


def _redis_daily_key(user_id: str) -> str:
    """Redis key for the daily query counter."""
    return f"engram:daily_query:{user_id}:{_today_str()}"


async def _redis_get_count(redis_client: object, user_id: str) -> int:
    """Read the daily query count from Redis. Returns count or raises on failure."""
    key = _redis_daily_key(user_id)
    val = await redis_client.get(key)  # type: ignore[union-attr]
    return int(val) if val is not None else 0


async def _redis_incr(redis_client: object, user_id: str) -> int:
    """Atomically increment the daily query count in Redis. Returns new count."""
    key = _redis_daily_key(user_id)
    new_count = await redis_client.incr(key)  # type: ignore[union-attr]
    # Set TTL only on first increment (count == 1) so key auto-expires
    if new_count == 1:
        await redis_client.expire(key, _DAILY_QUERY_TTL)  # type: ignore[union-attr]
    return int(new_count)


async def get_daily_query_count(user_id: str, *, redis_client: object = None) -> int:
    """Return the number of queries the user has made today (UTC)."""
    if redis_client is not None:
        try:
            return await _redis_get_count(redis_client, user_id)
        except Exception:
            logger.warning(
                "Redis unavailable for get_daily_query_count user=%s, falling back to in-memory",
                user_id,
                exc_info=True,
            )
    return _daily_query_counts.get(_daily_key(user_id), 0)


async def increment_daily_query_count(user_id: str, *, redis_client: object = None) -> int:
    """Increment and return the user's daily query count."""
    if redis_client is not None:
        try:
            count = await _redis_incr(redis_client, user_id)
            # Also update in-memory for consistency if Redis succeeds
            _daily_query_counts[_daily_key(user_id)] = count
            return count
        except Exception:
            logger.warning(
                "Redis unavailable for increment_daily_query_count user=%s, falling back to in-memory",
                user_id,
                exc_info=True,
            )
    key = _daily_key(user_id)
    current = _daily_query_counts.get(key, 0) + 1
    _daily_query_counts[key] = current
    return current


async def check_daily_query_limit(user_id: str, *, redis_client: object = None) -> None:
    """
    Raise RateLimitError if the user has exceeded their daily query limit.
    Called from the query route BEFORE executing the query.
    """
    from ..exceptions import RateLimitError

    limit = settings.free_tier_daily_query_limit
    if limit <= 0:
        return

    current = await get_daily_query_count(user_id, redis_client=redis_client)
    if current >= limit:
        # Calculate reset time (next UTC midnight)
        now = datetime.now(timezone.utc)
        tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow += timedelta(days=1)

        raise RateLimitError(
            f"Daily query limit reached ({current}/{limit}). Resets at {tomorrow.isoformat()}.",
            error_code="DAILY_QUERY_LIMIT_EXCEEDED",
            details={
                "current": current,
                "limit": limit,
                "resets_at": tomorrow.isoformat(),
            },
        )


# ── Usage summary (for GET /memory/usage) ────────────────────────────────────

async def get_usage_summary(
    driver: AsyncDriver,
    user_id: str,
    *,
    redis_client: object = None,
) -> dict[str, Any]:
    """
    Return a usage summary for the user. Used by GET /memory/usage.
    """
    from .write_service import count_user_messages

    message_count = await count_user_messages(driver, user_id)
    daily_queries_used = await get_daily_query_count(user_id, redis_client=redis_client)
    daily_limit = settings.free_tier_daily_query_limit
    message_limit = settings.free_tier_message_limit

    # Calculate reset time
    now = datetime.now(timezone.utc)
    tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow += timedelta(days=1)

    return {
        "messages_stored": message_count,
        "messages_limit": message_limit,
        "daily_queries_used": daily_queries_used,
        "daily_queries_limit": daily_limit,
        "daily_queries_remaining": max(0, daily_limit - daily_queries_used),
        "queries_reset_at": tomorrow.isoformat(),
    }
