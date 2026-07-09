"""重试装饰器 — 指数退避 / Retry decorator with exponential backoff.

适用于网络调用不稳定的外部服务（Ollama、Qdrant、Langfuse）。
Designed for external services with unstable networks (Ollama, Qdrant, Langfuse).
"""

from __future__ import annotations

import asyncio
import functools
import random
import time
from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


def retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 10.0,
    backoff_factor: float = 2.0,
    jitter: bool = True,
    retryable_exceptions: tuple[type[Exception], ...] | None = None,
):
    """重试装饰器 / Retry decorator.

    Args:
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay in seconds
        max_delay: Maximum delay cap in seconds
        backoff_factor: Multiplier for exponential backoff
        jitter: Add random jitter to prevent thundering herd
        retryable_exceptions: Tuple of exception types to retry on (default: all)
    """
    if retryable_exceptions is None:
        retryable_exceptions = (Exception,)

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Exception | None = None
            delay = base_delay

            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except retryable_exceptions as exc:
                    last_exc = exc
                    if attempt == max_retries:
                        raise
                    jittered = delay + random.uniform(0, delay * 0.1) if jitter else delay
                    await asyncio.sleep(jittered)
                    delay = min(delay * backoff_factor, max_delay)

            raise AssertionError("Unreachable")

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Exception | None = None
            delay = base_delay

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except retryable_exceptions as exc:
                    last_exc = exc
                    if attempt == max_retries:
                        raise
                    jittered = delay + random.uniform(0, delay * 0.1) if jitter else delay
                    time.sleep(jittered)
                    delay = min(delay * backoff_factor, max_delay)

            raise AssertionError("Unreachable")

        # 自动选择 sync/async / Auto-detect sync/async
        if asyncio.iscoroutinefunction(func):
            return async_wrapper  # type: ignore[return-value]
        else:
            return sync_wrapper  # type: ignore[return-value]

    return decorator
