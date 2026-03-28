"""
Rate limiter singleton — isolated to break the circular import between
main.py (which imports routes) and routes/ (which need the limiter).

Import this module from both main.py and route files.

Key strategy:
  1. If request.state.authenticated_user_id is already set (post-auth path),
     use it — this is the most precise key.
  2. Otherwise try to decode the Bearer JWT from the Authorization header
     so the bucket is per-user even before the FastAPI dependency runs.
     This also prevents all TestClient tests from sharing one IP bucket.
  3. Fall back to remote IP for unauthenticated requests (/health, etc.).
"""

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address


def _rate_limit_key(request: Request) -> str:
    # Already resolved by an earlier middleware or the route handler
    user_id = getattr(request.state, "authenticated_user_id", None)
    if user_id:
        return f"user:{user_id}"

    # Try to extract from JWT without verifying — the route will reject bad
    # tokens before any expensive work happens.  We only need the sub claim.
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
        try:
            # Lazy import to avoid coupling at module load time
            from ..auth.jwt_handler import decode_access_token
            uid = decode_access_token(token)
            return f"user:{uid}"
        except Exception:
            pass

    return get_remote_address(request)


limiter = Limiter(key_func=_rate_limit_key)
