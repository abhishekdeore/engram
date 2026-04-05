"""
Token revocation via Redis-backed deny-list.

Each revoked token's jti is stored as a Redis key with a TTL matching
the token's remaining lifetime. Once the token would have expired naturally,
the deny-list entry auto-deletes — no infinite keys.

Redis key format: engram:revoked:{jti}
Redis value: "1" (simple flag)

If Redis is unavailable, revocation checks fail open (tokens are NOT revoked)
and a CRITICAL warning is logged. This is a deliberate trade-off: availability
over security during Redis outages. Operators must monitor for these logs.
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_KEY_PREFIX = "engram:revoked:"


async def revoke_token(redis_client, jti: str, expires_at: datetime) -> bool:
    """
    Add a token's jti to the deny-list with TTL.

    Args:
        redis_client: async Redis client (may be None).
        jti: The JWT ID claim from the token to revoke.
        expires_at: The token's expiry datetime (used to calculate TTL).

    Returns:
        True if successfully added to deny-list, False otherwise.
    """
    if redis_client is None:
        logger.critical(
            "REVOCATION_FAILED jti=%s reason=redis_unavailable — "
            "token cannot be revoked without Redis",
            jti,
        )
        return False

    now = datetime.now(timezone.utc)
    ttl_seconds = int((expires_at - now).total_seconds())

    if ttl_seconds <= 0:
        # Token already expired — no need to revoke
        logger.info("revoke_token jti=%s already_expired — skipping", jti)
        return True

    try:
        key = f"{_KEY_PREFIX}{jti}"
        await redis_client.setex(key, ttl_seconds, "1")
        logger.info("token_revoked jti=%s ttl_seconds=%d", jti, ttl_seconds)
        return True
    except Exception as exc:
        logger.critical(
            "REVOCATION_FAILED jti=%s error=%s — "
            "token revocation could not be persisted to Redis",
            jti,
            exc,
        )
        return False


async def is_token_revoked(redis_client, jti: str) -> bool:
    """
    Check if a token's jti is in the deny-list.

    Args:
        redis_client: async Redis client (may be None).
        jti: The JWT ID claim to check.

    Returns:
        True if the token is revoked, False otherwise.
        Returns False if Redis is unavailable (fail-open).
    """
    if redis_client is None:
        # No Redis — cannot check deny-list, fail open
        return False

    try:
        key = f"{_KEY_PREFIX}{jti}"
        result = await redis_client.get(key)
        return result is not None
    except Exception as exc:
        logger.critical(
            "REVOCATION_CHECK_FAILED jti=%s error=%s — "
            "failing open (token treated as valid). "
            "Monitor this: revoked tokens may be accepted during Redis outage.",
            jti,
            exc,
        )
        return False
