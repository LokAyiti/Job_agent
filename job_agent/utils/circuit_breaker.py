"""Circuit breaker and structured retry utilities."""
from __future__ import annotations

import time
from enum import Enum
from functools import wraps
from typing import Callable, Optional, Type, Union

from loguru import logger


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpenError(Exception):
    """Raised when a call is blocked because the circuit is open."""


class CircuitBreaker:
    """Simple circuit breaker for external service calls."""

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_max_calls: int = 1,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._last_failure_time: Optional[float] = None
        self._half_open_calls = 0

    @property
    def state(self) -> CircuitState:
        if self._state == CircuitState.OPEN:
            if self._last_failure_time and (time.time() - self._last_failure_time) >= self.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                self._half_open_calls = 0
        return self._state

    def call(self, fn: Callable, *args, **kwargs):
        state = self.state
        if state == CircuitState.OPEN:
            raise CircuitBreakerOpenError(f"Circuit breaker '{self.name}' is OPEN")
        if state == CircuitState.HALF_OPEN and self._half_open_calls >= self.half_open_max_calls:
            raise CircuitBreakerOpenError(f"Circuit breaker '{self.name}' is half-open and saturated")

        try:
            if state == CircuitState.HALF_OPEN:
                self._half_open_calls += 1
            result = fn(*args, **kwargs)
            self._on_success()
            return result
        except Exception as exc:
            self._on_failure()
            raise

    async def async_call(self, async_fn: Callable, *args, **kwargs):
        state = self.state
        if state == CircuitState.OPEN:
            raise CircuitBreakerOpenError(f"Circuit breaker '{self.name}' is OPEN")
        if state == CircuitState.HALF_OPEN and self._half_open_calls >= self.half_open_max_calls:
            raise CircuitBreakerOpenError(f"Circuit breaker '{self.name}' is half-open and saturated")

        try:
            if state == CircuitState.HALF_OPEN:
                self._half_open_calls += 1
            result = await async_fn(*args, **kwargs)
            self._on_success()
            return result
        except Exception as exc:
            self._on_failure()
            raise

    def _on_success(self) -> None:
        self._failures = 0
        self._state = CircuitState.CLOSED
        self._half_open_calls = 0

    def _on_failure(self) -> None:
        self._failures += 1
        self._last_failure_time = time.time()
        if self._failures >= self.failure_threshold:
            logger.warning(f"Circuit breaker '{self.name}' opened after {self._failures} failures")
            self._state = CircuitState.OPEN


def with_circuit_breaker(breaker: CircuitBreaker):
    """Decorator that wraps a function with a circuit breaker."""

    def decorator(fn: Callable):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            return breaker.call(fn, *args, **kwargs)

        return wrapper

    return decorator


def with_async_circuit_breaker(breaker: CircuitBreaker):
    """Decorator that wraps an async function with a circuit breaker."""

    def decorator(fn: Callable):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            return await breaker.async_call(fn, *args, **kwargs)

        return wrapper

    return decorator


# Shared circuit breakers for common external services.
captcha_breaker = CircuitBreaker("2captcha", failure_threshold=3, recovery_timeout=300.0)
gmail_breaker = CircuitBreaker("gmail", failure_threshold=3, recovery_timeout=120.0)
outlook_breaker = CircuitBreaker("outlook", failure_threshold=3, recovery_timeout=120.0)
