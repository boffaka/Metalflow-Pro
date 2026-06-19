"""
Centralized rate limiter for heavy computation endpoints.
Uses slowapi backed by Redis when available; falls back to in-memory storage.

Usage:
    from rate_limiter import limiter, RateLimitExceeded, _rate_limit_handler
    # in main.py:
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)

    # on an endpoint:
    from rate_limiter import limiter
    @router.post("/heavy")
    @limiter.limit("10/minute")
    async def heavy_endpoint(request: Request, ...):
        ...
"""
from __future__ import annotations
import logging
import os

logger = logging.getLogger("mpdpms.rate_limiter")

try:
    from slowapi import Limiter, _rate_limit_exceeded_handler as _rate_limit_handler  # type: ignore
    from slowapi.util import get_remote_address  # type: ignore
    from slowapi.errors import RateLimitExceeded  # type: ignore

    _redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    try:
        import redis as _redis_probe
        _r = _redis_probe.from_url(_redis_url, socket_connect_timeout=1)
        _r.ping()
        limiter = Limiter(
            key_func=get_remote_address,
            storage_uri=_redis_url,
            default_limits=["200/minute"],
        )
        logger.info("Rate limiter initialised (Redis backend: %s)", _redis_url)
    except Exception as _e:
        logger.warning("Rate limiter Redis backend unavailable (%s); using in-memory", _e)
        limiter = Limiter(key_func=get_remote_address, storage_uri="memory://")

    _SLOWAPI_AVAILABLE = True

except ImportError:
    logger.warning("slowapi not installed; rate limiting disabled")
    _SLOWAPI_AVAILABLE = False

    # Provide no-op stubs so imports never crash
    class _NoopLimiter:  # type: ignore
        def limit(self, *args, **kwargs):
            def decorator(fn):
                return fn
            return decorator
        def limit_and_check(self, *args, **kwargs):
            pass

    limiter = _NoopLimiter()  # type: ignore

    class RateLimitExceeded(Exception):  # type: ignore
        pass

    def _rate_limit_handler(request, exc):  # type: ignore
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})


# Named limit tiers used across the app
HEAVY_COMPUTE_LIMIT = "10/minute"   # NSGA-II, Monte Carlo, GADE auto-domain
MEDIUM_COMPUTE_LIMIT = "30/minute"  # blend optimizer, recovery forecast
STANDARD_LIMIT = "60/minute"        # most API endpoints (default)
