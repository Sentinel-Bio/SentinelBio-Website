"""Rate limiting using slowapi.

Admins are exempted. For authenticated users, limit by user_id.
For anonymous, by IP (but anonymous endpoints are rare here).
"""
from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request


def key_func(request: Request) -> str:
    """Prefer user_id from JWT; fall back to IP."""
    user = getattr(request.state, "user", None)
    if user is not None and user.id:
        return f"user:{user.id}"
    return f"ip:{get_remote_address(request)}"


limiter = Limiter(key_func=key_func, default_limits=[])
