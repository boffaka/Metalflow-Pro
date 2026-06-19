"""Tests for the circuit breaker pattern."""
import time
from circuit_breaker import CircuitBreaker, CircuitState, get_breaker


def test_initial_state_closed():
    cb = CircuitBreaker("test_init", failure_threshold=3, recovery_timeout=1)
    assert cb.state == CircuitState.CLOSED


def test_opens_after_threshold():
    cb = CircuitBreaker("test_open", failure_threshold=3, recovery_timeout=60)
    for _ in range(3):
        cb.record_failure()
    assert cb.state == CircuitState.OPEN


def test_stays_closed_below_threshold():
    cb = CircuitBreaker("test_below", failure_threshold=3, recovery_timeout=60)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute() is True


def test_blocks_when_open():
    cb = CircuitBreaker("test_block", failure_threshold=2, recovery_timeout=60)
    cb.record_failure()
    cb.record_failure()
    assert cb.can_execute() is False


def test_half_open_after_recovery():
    cb = CircuitBreaker("test_half", failure_threshold=2, recovery_timeout=0.1)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    time.sleep(0.15)
    assert cb.state == CircuitState.HALF_OPEN
    assert cb.can_execute() is True


def test_closes_on_success_in_half_open():
    cb = CircuitBreaker("test_close", failure_threshold=2, recovery_timeout=0.1)
    cb.record_failure()
    cb.record_failure()
    time.sleep(0.15)
    cb.record_success()
    assert cb.state == CircuitState.CLOSED


def test_success_resets_failure_count():
    cb = CircuitBreaker("test_reset", failure_threshold=3, recovery_timeout=60)
    cb.record_failure()
    cb.record_failure()
    cb.record_success()
    cb.record_failure()
    assert cb.state == CircuitState.CLOSED


def test_global_registry():
    b1 = get_breaker("registry_test", failure_threshold=5)
    b2 = get_breaker("registry_test")
    assert b1 is b2
