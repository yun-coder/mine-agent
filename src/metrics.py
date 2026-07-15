"""Prometheus 指标收集 / Prometheus metrics collection.

提供请求延迟直方图、错误计数器、活跃会话数等指标。
Provides request latency histograms, error counters, active session gauges, etc.
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from fastapi import FastAPI, Request, Response
import time
import uuid


# ------------------------------------------------------------------
# 指标定义 / Metric definitions
# ------------------------------------------------------------------

REQUEST_COUNT = Counter(
    "agent_requests_total",
    "Total agent requests by endpoint and method",
    ["method", "endpoint", "status"],
)

REQUEST_LATENCY = Histogram(
    "agent_request_duration_seconds",
    "Agent request latency in seconds",
    ["method", "endpoint"],
    buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0),
)

ACTIVE_SESSIONS = Gauge(
    "agent_active_sessions",
    "Number of active sessions",
)

EMBED_CACHE_HITS = Counter(
    "embed_cache_hits_total",
    "Total embedding cache hits",
)

EMBED_CACHE_MISSES = Counter(
    "embed_cache_misses_total",
    "Total embedding cache misses",
)

ERROR_COUNT = Counter(
    "agent_errors_total",
    "Total agent errors",
    ["type"],
)

# ------------------------------------------------------------------
# RAG 质量指标 / RAG quality metrics
# ------------------------------------------------------------------

RAG_AVG_SCORE = Histogram(
    "rag_result_avg_score",
    "RAG result average score distribution",
    buckets=(0.1, 0.3, 0.5, 0.7, 0.9),
)

RAG_EMPTY_RESULTS = Counter(
    "rag_empty_results_total",
    "Total RAG queries with zero results",
)


def init_metrics(app: FastAPI) -> None:
    """注册指标中间件和端点 / Register metrics middleware and endpoint."""

    @app.middleware("http")
    async def metrics_middleware(request: Request, call_next) -> Response:
        """自动记录请求数和延迟 / Automatically record request count and latency."""
        # 跳过 /metrics 和 /docs 端点自身 / Skip /metrics and /docs endpoints
        if request.url.path in ("/metrics", "/docs", "/openapi.json", "/favicon.ico"):
            return await call_next(request)

        start_time = time.perf_counter()
        correlation_id = str(uuid.uuid4())[:8]

        # 将 correlation_id 注入请求状态 / Inject correlation_id into request state
        request.state.correlation_id = correlation_id

        response = await call_next(request)

        duration = time.perf_counter() - start_time
        REQUEST_COUNT.labels(
            method=request.method,
            endpoint=request.url.path,
            status=response.status_code,
        ).inc()
        REQUEST_LATENCY.labels(
            method=request.method,
            endpoint=request.url.path,
        ).observe(duration)

        # 记录错误 / Log errors
        if response.status_code >= 500:
            ERROR_COUNT.labels(type=f"http_{response.status_code}").inc()

        return response

    @app.get("/metrics")
    async def metrics_endpoint():
        """Prometheus 指标端点 / Prometheus metrics endpoint."""
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
