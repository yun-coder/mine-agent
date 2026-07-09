"""Phase 2: Rate Limiting & Retry Tests — 速率限制、重试逻辑、熔断器"""

import asyncio
import pytest
import time
from unittest.mock import patch, MagicMock


class TestRateLimiter:
    """速率限制器测试 / Rate limiter tests."""

    @pytest.mark.asyncio
    async def test_allow_within_limit(self):
        """限制内的请求应被允许 / Requests within limit should be allowed."""
        from src.api.rate_limit import RateLimiter
        limiter = RateLimiter()

        for _ in range(5):
            assert await limiter.allow("test-key", limit=10, window_seconds=60) is True

    @pytest.mark.asyncio
    async def test_block_over_limit(self):
        """超过限制的请求应被拒绝 / Requests over limit should be blocked."""
        from src.api.rate_limit import RateLimiter
        limiter = RateLimiter()

        for _ in range(5):
            assert await limiter.allow("test-key", limit=5, window_seconds=60) is True

        # 第 6 次应该被拒绝 / 6th request should be denied
        assert await limiter.allow("test-key", limit=5, window_seconds=60) is False

    @pytest.mark.asyncio
    async def test_window_expiry(self):
        """窗口过期后应允许新请求 / Expired window should allow new requests."""
        from src.api.rate_limit import RateLimiter
        limiter = RateLimiter()

        # 填满限制 / Fill the limit
        for _ in range(3):
            await limiter.allow("test-key", limit=3, window_seconds=1)

        # 等待窗口过期 / Wait for window to expire
        await asyncio.sleep(1.1)

        # 应该再次允许 / Should be allowed again
        assert await limiter.allow("test-key", limit=3, window_seconds=1) is True

    @pytest.mark.asyncio
    async def test_different_keys_independent(self):
        """不同 key 应有独立的限制 / Different keys should have independent limits."""
        from src.api.rate_limit import RateLimiter
        limiter = RateLimiter()

        # Key A 用满限制 / Key A uses its limit
        for _ in range(3):
            assert await limiter.allow("key-a", limit=3, window_seconds=60) is True
        assert await limiter.allow("key-a", limit=3, window_seconds=60) is False

        # Key B 应该仍然可用 / Key B should still work
        assert await limiter.allow("key-b", limit=3, window_seconds=60) is True

    @pytest.mark.asyncio
    async def test_remaining_count(self):
        """剩余请求数应正确 / Remaining count should be accurate."""
        from src.api.rate_limit import RateLimiter
        limiter = RateLimiter()

        await limiter.allow("test-key", limit=5, window_seconds=60)
        remaining = await limiter.get_remaining("test-key", limit=5, window_seconds=60)
        assert remaining == 4


class TestRetryDecorator:
    """重试装饰器测试 / Retry decorator tests."""

    @pytest.mark.asyncio
    async def test_retry_succeeds_on_third(self):
        """第三次尝试成功 / Succeeds on third attempt."""
        from src.api.retry import retry

        call_count = 0

        @retry(max_retries=3, base_delay=0.01, max_delay=0.01, jitter=False)
        async def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("Service unavailable")
            return "success"

        result = await flaky_func()
        assert result == "success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_retry_exhausted_raises(self):
        """重试耗尽后抛出异常 / Raises after retries exhausted."""
        from src.api.retry import retry

        @retry(max_retries=2, base_delay=0.01, max_delay=0.01, jitter=False)
        async def always_fails():
            raise ConnectionError("Always fails")

        with pytest.raises(ConnectionError):
            await always_fails()

    @pytest.mark.asyncio
    async def test_retry_does_not_retry_non_retryable(self):
        """非可重试异常不应重试 / Non-retryable exceptions should not retry."""
        from src.api.retry import retry

        call_count = 0

        @retry(
            max_retries=3,
            base_delay=0.01,
            max_delay=0.01,
            jitter=False,
            retryable_exceptions=(ConnectionError,),
        )
        async def fails_with_value_error():
            nonlocal call_count
            call_count += 1
            raise ValueError("Not retryable")

        with pytest.raises(ValueError):
            await fails_with_value_error()
        assert call_count == 1  # 不重试 / No retry


class TestCircuitBreaker:
    """熔断器测试 / Circuit breaker tests."""

    def test_initially_closed(self):
        """初始状态应为 CLOSED / Initially should be CLOSED."""
        from src.api.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=1.0, success_threshold=1)
        assert cb.state.value == "closed"

    def test_opens_after_failures(self):
        """连续失败应打开熔断器 / Consecutive failures should open the circuit."""
        from src.api.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=1.0, success_threshold=1)

        cb.record_failure()
        cb.record_failure()
        assert cb.state.value == "closed"  # 还没到阈值 / Not yet threshold

        cb.record_failure()
        assert cb.state.value == "open"  # 达到阈值 / Threshold reached

    def test_closes_after_successes_in_half_open(self):
        """半开放状态下成功应关闭熔断器 / Successes in half-open should close the circuit."""
        from src.api.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1, success_threshold=1)

        cb.record_failure()
        cb.record_failure()
        assert cb.state.value == "open"

        time.sleep(0.15)  # 等待恢复超时 / Wait for recovery timeout
        assert cb.state.value == "half_open"

        cb.record_success()
        assert cb.state.value == "closed"

    def test_stays_open_on_failure_in_half_open(self):
        """半开放状态下失败应保持打开 / Failure in half-open should stay open."""
        from src.api.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=5.0, success_threshold=1)

        cb.record_failure()
        cb.record_failure()
        assert cb.state.value == "open"

        # 短暂等待后进入半开放 / Wait briefly to enter half-open
        time.sleep(0.05)
        # 此时仍在恢复超时内，仍为 open
        assert cb.state.value == "open"

        # 等待恢复超时进入半开放 / Wait for recovery timeout to enter half-open
        time.sleep(5.1)
        assert cb.state.value == "half_open"

        # 在半开放状态下失败 / Fail while half-open
        cb.record_failure()
        # 由于 recovery_timeout=5s 很长，state 应该回到 open
        assert cb.state.value == "open"
