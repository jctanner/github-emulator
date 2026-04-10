"""Rate limiting middleware that adds GitHub-compatible rate limit headers."""

import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

# Rate limit configuration
RATE_LIMIT = 5000
RATE_WINDOW_SECONDS = 3600  # 1 hour


class RateLimitState:
    """In-memory rate limit tracking per client key."""

    def __init__(self):
        self._counters: dict[str, dict] = defaultdict(
            lambda: {"used": 0, "reset_at": 0.0}
        )

    def get_or_reset(self, key: str) -> dict:
        """Get the current counter for a key, resetting if the window expired."""
        now = time.time()
        state = self._counters[key]
        if now >= state["reset_at"]:
            # Reset the window
            state["used"] = 0
            state["reset_at"] = now + RATE_WINDOW_SECONDS
        return state

    def increment(self, key: str) -> dict:
        """Increment the counter for a key and return the updated state."""
        state = self.get_or_reset(key)
        state["used"] += 1
        return state


# Singleton rate limit state
_rate_limit_state = RateLimitState()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Middleware that adds GitHub-compatible rate limit headers to all API responses.

    Headers added:
      - X-RateLimit-Limit: 5000
      - X-RateLimit-Remaining: <remaining>
      - X-RateLimit-Reset: <unix-timestamp>
      - X-RateLimit-Used: <used>
      - X-RateLimit-Resource: core
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Determine the client key (token or IP)
        key = self._get_client_key(request)

        # Increment usage
        state = _rate_limit_state.increment(key)

        response = await call_next(request)

        # Add rate limit headers
        used = state["used"]
        remaining = max(0, RATE_LIMIT - used)
        reset_at = int(state["reset_at"])

        response.headers["X-RateLimit-Limit"] = str(RATE_LIMIT)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(reset_at)
        response.headers["X-RateLimit-Used"] = str(used)
        response.headers["X-RateLimit-Resource"] = "core"

        return response

    @staticmethod
    def _get_client_key(request: Request) -> str:
        """Derive a rate limit key from the request.

        Uses the Authorization token if present, otherwise falls back
        to the client IP address.
        """
        auth_header = request.headers.get("Authorization", "")
        if auth_header:
            return f"auth:{auth_header}"
        # Fall back to client IP
        client = request.client
        if client:
            return f"ip:{client.host}"
        return "ip:unknown"
