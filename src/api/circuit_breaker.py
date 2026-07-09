"""简易熔断器 — 防止雪崩效应 / Simple circuit breaker to prevent cascade failures.

三种状态 / Three states:
  CLOSED   — 正常转发请求 / Normal, forwards requests
  OPEN     — 快速失败，不转发 / Fail-fast, does not forward
  HALF_OPEN — 试探性放行一个请求 / Probe with one request
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """简易熔断器 / Simple circuit breaker.

    Args:
        failure_threshold: Consecutive failures before opening (default 5)
        recovery_timeout: Seconds to wait before half-open (default 30)
        success_threshold: Successes in half-open before closing (default 2)
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        success_threshold: int = 2,
    ):
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._success_threshold = success_threshold

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: float = 0
        self._opened_at: float = 0

    @property
    def state(self) -> CircuitState:
        """当前状态，考虑恢复超时 / Current state considering recovery timeout."""
        if self._state == CircuitState.OPEN:
            if time.time() - self._opened_at >= self._recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                self._success_count = 0
                self._opened_at = time.time()  # Reset timer for half-open probing
        return self._state

    def record_success(self) -> None:
        """记录成功 / Record a success."""
        if self._state == CircuitState.HALF_OPEN:
            self._success_count += 1
            if self._success_count >= self._success_threshold:
                self._state = CircuitState.CLOSED
                self._failure_count = 0
                self._success_count = 0
        elif self._state == CircuitState.CLOSED:
            self._failure_count = 0

    def record_failure(self) -> None:
        """记录失败 / Record a failure."""
        self._failure_count += 1
        self._last_failure_time = time.time()
        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN
            self._opened_at = time.time()
            self._success_count = 0
        elif self._state == CircuitState.CLOSED:
            if self._failure_count >= self._failure_threshold:
                self._state = CircuitState.OPEN
                self._opened_at = time.time()
                self._success_count = 0

    def can_execute(self) -> bool:
        """检查是否可以执行 / Check if execution is allowed."""
        current = self.state
        if current == CircuitState.CLOSED:
            return True
        if current == CircuitState.HALF_OPEN:
            return True
        return False

    def get_status(self) -> dict[str, Any]:
        """获取熔断器状态 / Get circuit breaker status."""
        return {
            "state": self.state.value,
            "failure_count": self._failure_count,
            "success_count": self._success_count,
            "last_failure_time": self._last_failure_time,
        }


# 全局熔断器实例 / Global circuit breaker instances
_ollama_circuit = CircuitBreaker(failure_threshold=5, recovery_timeout=30.0, success_threshold=2)


def get_ollama_circuit() -> CircuitBreaker:
    return _ollama_circuit
