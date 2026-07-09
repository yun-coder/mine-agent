"""速率限制 — 滑动窗口算法 / Rate limiting with sliding window algorithm.

简单的内存实现，适用于单实例部署。多实例场景应替换为 Redis 后端。
Simple in-memory implementation for single-instance deployment.
Use Redis backend for multi-instance scenarios.
"""

from __future__ import annotations

import time
import asyncio
from collections import defaultdict
from typing import Optional


class RateLimiter:
    """滑动窗口速率限制器 / Sliding window rate limiter."""

    def __init__(self):
        # key -> list of timestamps
        self._requests: dict[str, list[float]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        """检查请求是否允许 / Check if request is allowed.

        Args:
            key: Rate limit key (e.g., IP address or API key)
            limit: Max requests in window
            window_seconds: Window size in seconds

        Returns:
            True if allowed, False if rate limited
        """
        now = time.time()
        window_start = now - window_seconds

        async with self._lock:
            # 清理过期记录 / Prune expired entries
            self._requests[key] = [
                ts for ts in self._requests[key] if ts > window_start
            ]

            if len(self._requests[key]) >= limit:
                return False

            self._requests[key].append(now)
            return True

    async def get_remaining(self, key: str, limit: int, window_seconds: int) -> int:
        """获取剩余请求数 / Get remaining requests in window."""
        now = time.time()
        window_start = now - window_seconds

        async with self._lock:
            self._requests[key] = [
                ts for ts in self._requests[key] if ts > window_start
            ]
            return max(0, limit - len(self._requests[key]))


# 全局速率限制器实例 / Global rate limiter instance
_limiter = RateLimiter()


# 默认速率限制规则 / Default rate limit rules (key, limit, window_seconds)
_DEFAULT_RULES: dict[str, tuple[int, int]] = {
    "/api/v1/agent/ask": (30, 60),       # 30 req/min
    "/api/v1/agent/stream": (10, 60),    # 10 req/min (streaming is expensive)
    "/api/v1/health": (60, 60),          # 60 req/min
    "/v1/chat/completions": (20, 60),    # 20 req/min
    "/chat/completions": (20, 60),       # 20 req/min
    "/query": (60, 60),                  # 60 req/min
    "/query/stream": (20, 60),           # 20 req/min
    "/ingest": (5, 60),                  # 5 req/min (expensive operation)
}


async def check_rate_limit(
    path: str,
    key: str = "anonymous",
    rules: Optional[dict[str, tuple[int, int]]] = None,
) -> tuple[bool, int, int]:
    """检查速率限制 / Check rate limit.

    Returns:
        (allowed, limit, remaining)
    """
    rules = rules or _DEFAULT_RULES
    rule = rules.get(path)
    if not rule:
        return True, 0, 0  # 无限制 / No limit

    limit, window = rule
    allowed = await _limiter.allow(key, limit, window)
    remaining = await _limiter.get_remaining(key, limit, window)
    return allowed, limit, remaining
