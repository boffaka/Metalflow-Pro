from __future__ import annotations

from collections import defaultdict
from typing import Protocol


class SecurityStore(Protocol):
    def record_login_attempt(self, key: str, now: float, window_seconds: int) -> int: ...
    def reset_login_attempts(self, key: str) -> None: ...


class MemorySecurityStore:
    def __init__(self) -> None:
        self._attempts: dict[str, list[float]] = defaultdict(list)

    def record_login_attempt(self, key: str, now: float, window_seconds: int) -> int:
        window = [ts for ts in self._attempts[key] if now - ts < window_seconds]
        window.append(now)
        self._attempts[key] = window
        return len(window)

    def reset_login_attempts(self, key: str) -> None:
        self._attempts.pop(key, None)

    def snapshot(self) -> dict[str, list[float]]:
        return dict(self._attempts)


class RedisSecurityStore:
    def __init__(self, redis_url: str) -> None:
        try:
            import redis
        except ImportError as exc:  # pragma: no cover - depends on optional dependency
            raise RuntimeError("redis package is required when SECURITY_STORE_BACKEND=redis") from exc
        self._client = redis.Redis.from_url(redis_url, decode_responses=True)

    def record_login_attempt(self, key: str, now: float, window_seconds: int) -> int:
        pipe = self._client.pipeline()
        pipe.rpush(key, now)
        pipe.expire(key, window_seconds)
        pipe.lrange(key, 0, -1)
        _, _, values = pipe.execute()
        valid = [float(v) for v in values if now - float(v) < window_seconds]
        pipe = self._client.pipeline()
        pipe.delete(key)
        if valid:
            pipe.rpush(key, *valid)
            pipe.expire(key, window_seconds)
        pipe.execute()
        return len(valid)

    def reset_login_attempts(self, key: str) -> None:
        self._client.delete(key)


def build_security_store(backend: str, redis_url: str | None) -> SecurityStore:
    if backend == "memory":
        return MemorySecurityStore()
    if backend == "redis":
        if not redis_url:
            raise ValueError("REDIS_URL is required when SECURITY_STORE_BACKEND=redis")
        return RedisSecurityStore(redis_url)
    raise ValueError(f"Unsupported SECURITY_STORE_BACKEND: {backend}")
