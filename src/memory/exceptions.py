"""
Engram structured exception hierarchy.

All service-layer exceptions inherit from EngramError and carry:
  - error_code  : stable machine-readable identifier (e.g. "WRITE_STORAGE_CAP")
  - message     : human-readable explanation
  - status_code : HTTP status to return (used by FastAPI exception handler)
  - details     : optional dict with context (current count, limits, etc.)

FastAPI exception handlers in api/main.py convert these to JSON responses.
"""

from __future__ import annotations

from typing import Any


class EngramError(Exception):
    """Base exception for all Engram service errors."""

    status_code: int = 500
    error_code: str = "INTERNAL_ERROR"

    def __init__(
        self,
        message: str,
        *,
        error_code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.message = message
        if error_code is not None:
            self.error_code = error_code
        if status_code is not None:
            self.status_code = status_code
        self.details = details or {}


# ── Auth errors ──────────────────────────────────────────────────────────────

class AuthError(EngramError):
    """Authentication or authorization failure."""

    status_code = 401
    error_code = "AUTH_INVALID_TOKEN"


class ForbiddenError(EngramError):
    """Caller lacks permission for the requested operation."""

    status_code = 403
    error_code = "FORBIDDEN"


# ── Not found ────────────────────────────────────────────────────────────────

class NotFoundError(EngramError):
    """Requested resource does not exist (or caller lacks access)."""

    status_code = 404
    error_code = "NOT_FOUND"


# ── Validation ───────────────────────────────────────────────────────────────

class ValidationError(EngramError):
    """Request payload failed validation beyond Pydantic checks."""

    status_code = 422
    error_code = "VALIDATION_ERROR"


# ── Rate limiting / usage caps ───────────────────────────────────────────────

class RateLimitError(EngramError):
    """Per-minute or per-day rate limit exceeded."""

    status_code = 429
    error_code = "RATE_LIMIT_EXCEEDED"


class StorageCapError(EngramError):
    """User has reached their storage limit."""

    status_code = 403
    error_code = "STORAGE_CAP_EXCEEDED"


# ── Write errors ─────────────────────────────────────────────────────────────

class WriteError(EngramError):
    """Failure during the write pipeline (Neo4j, retry exhaustion, etc.)."""

    status_code = 502
    error_code = "WRITE_FAILED"


# ── Query errors ─────────────────────────────────────────────────────────────

class QueryError(EngramError):
    """Failure during the query pipeline (embedding, vector search, etc.)."""

    status_code = 502
    error_code = "QUERY_FAILED"


# ── Service unavailable ─────────────────────────────────────────────────────

class ServiceUnavailableError(EngramError):
    """A required backend (Neo4j, OpenAI, Redis) is temporarily unavailable."""

    status_code = 503
    error_code = "SERVICE_UNAVAILABLE"
