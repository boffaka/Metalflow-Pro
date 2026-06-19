"""MPDPMS — Circuit breaker pattern for computation engines."""
from __future__ import annotations

import time
import logging
from enum import Enum
from threading import Lock

logger = logging.getLogger("mpdpms.circuit_breaker")


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """
    Thread-safe circuit breaker.

    - CLOSED: normal operation, failures counted
    - OPEN: requests blocked, after recovery_timeout -> HALF_OPEN
    - HALF_OPEN: one request allowed, success -> CLOSED, failure -> OPEN
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._failures = 0
        self._last_failure_time: float = 0
        self._state = CircuitState.CLOSED
        self._lock = Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            if self._state == CircuitState.OPEN:
                if time.monotonic() - self._last_failure_time >= self.recovery_timeout:
                    self._state = CircuitState.HALF_OPEN
            return self._state

    def can_execute(self) -> bool:
        s = self.state
        return s in (CircuitState.CLOSED, CircuitState.HALF_OPEN)

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._state = CircuitState.CLOSED

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            self._last_failure_time = time.monotonic()
            if self._failures >= self.failure_threshold:
                self._state = CircuitState.OPEN
                logger.warning(
                    "circuit breaker '%s' OPENED after %d failures",
                    self.name, self._failures,
                )


# Global registry
_breakers: dict[str, CircuitBreaker] = {}
_registry_lock = Lock()


def get_breaker(name: str, **kwargs) -> CircuitBreaker:
    """Get or create a named circuit breaker."""
    with _registry_lock:
        if name not in _breakers:
            _breakers[name] = CircuitBreaker(name, **kwargs)
        return _breakers[name]
