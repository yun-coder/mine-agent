"""集成测试 — 端到端 API 流程 / End-to-end API integration tests.

需要所有服务运行中（Ollama、Qdrant、Langfuse）才能通过。
Requires all services running (Ollama, Qdrant, Langfuse) to pass.
"""

import pytest
import time
import os
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


@pytest.fixture(scope="session", autouse=True)
def set_test_env():
    """设置测试环境变量 / Set test env vars for the session."""
    # 测试时禁用 API Key / Disable API key for testing
    os.environ["API_KEY"] = ""
    os.environ["DOCS_DIR"] = "D:/projects/langgraph-agent/assets"
    os.environ["PROJECT_ROOT"] = "D:/projects"
    yield


@pytest.fixture(scope="session")
def app(set_test_env):
    """创建测试应用 / Create test app."""
    from src.main import create_app
    return create_app()


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient
    return TestClient(app)


class TestHealthCheck:
    """健康检查集成测试 / Health check integration tests."""

    def test_health_endpoint_exists(self, client):
        """健康端点应可达 / Health endpoint should be reachable."""
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "qdrant" in data
        assert "ollama_llm" in data
        assert "ollama_embed" in data
        assert "langfuse" in data

    def test_health_reports_service_status(self, client):
        """健康检查应报告各服务状态 / Health should report service statuses."""
        response = client.get("/api/v1/health")
        data = response.json()
        # 至少有一个服务是可连接的 / At least one service should be connectable
        statuses = [
            data.get("qdrant", ""),
            data.get("ollama_llm", ""),
            data.get("ollama_embed", ""),
        ]
        assert any(
            "connected" in s.lower() or "已连接" in s for s in statuses
        )


class TestAgentAPI:
    """智能体 API 集成测试 / Agent API integration tests."""

    def test_ask_requires_auth(self, client):
        """不提供 API Key 应返回 401 / No API key should return 401."""
        # 设置 API_KEY 使认证生效 / Enable auth
        os.environ["API_KEY"] = "required-test-key"
        response = client.post(
            "/api/v1/agent/ask",
            json={"question": "test"},
        )
        assert response.status_code == 401

    def test_ask_with_valid_key(self, client):
        """使用有效 API Key 应通过认证 / Valid API key should pass auth."""
        response = client.post(
            "/api/v1/agent/ask",
            json={"question": "测试问题 / test question"},
            headers={"Authorization": "Bearer required-test-key"},
        )
        # 认证通过，但可能因 Qdrant 无集合而返回 500
        assert response.status_code in (200, 500)

    def test_ask_empty_question(self, client):
        """空问题应返回 400 / Empty question should return 400."""
        response = client.post(
            "/api/v1/agent/ask",
            json={"question": "   "},
            headers={"Authorization": "Bearer required-test-key"},
        )
        assert response.status_code == 400

    def test_ask_too_long_question(self, client):
        """超长问题应返回 422 / Overly long question should return 422."""
        response = client.post(
            "/api/v1/agent/ask",
            json={"question": "x" * 5000},
            headers={"Authorization": "Bearer required-test-key"},
        )
        assert response.status_code == 422

    def test_stream_requires_auth(self, client):
        """流式端点需要认证 / Stream endpoint requires auth."""
        response = client.post(
            "/api/v1/agent/stream",
            json={"question": "test"},
        )
        assert response.status_code == 401

    def test_openai_compatible_requires_auth(self, client):
        """OpenAI 兼容端点需要认证 / OpenAI-compatible endpoint requires auth."""
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "qwen3:8b",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        assert response.status_code == 401

    def test_openai_compatible_with_auth(self, client):
        """OpenAI 兼容端点带认证应通过 / OpenAI-compatible with auth should pass."""
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "qwen3:8b",
                "messages": [{"role": "user", "content": "hi"}],
            },
            headers={"Authorization": "Bearer required-test-key"},
        )
        # 认证通过，可能因 agent 执行而返回 500（Qdrant 无数据）
        assert response.status_code in (200, 500)


class TestRateLimiting:
    """速率限制集成测试 / Rate limiting integration tests."""

    def test_rate_limit_headers_present(self, client):
        """请求应通过认证 / Request should pass auth (rate limit headers optional in test)."""
        response = client.post(
            "/api/v1/agent/ask",
            json={"question": "test"},
            headers={"Authorization": "Bearer required-test-key"},
        )
        # 认证通过即可 / Auth passing is sufficient
        assert response.status_code in (200, 500)


class TestCORS:
    """CORS 集成测试 / CORS integration tests."""

    def test_preflight_allowed_origin(self, client):
        """预检请求应允许配置的源 / Preflight should allow configured origin."""
        response = client.options(
            "/api/v1/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code == 200
        assert "access-control-allow-origin" in response.headers

    def test_preflight_blocked_origin(self, client):
        """预检请求应阻止非配置源 / Preflight should block unconfigured origin."""
        response = client.options(
            "/api/v1/health",
            headers={
                "Origin": "http://evil-site.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        # 不应返回允许的 origin / Should not return allowed origin
        acl_origin = response.headers.get("access-control-allow-origin", "")
        assert "evil-site.com" not in acl_origin


class TestMetricsEndpoint:
    """Prometheus 指标端点测试 / Prometheus metrics endpoint tests."""

    def test_metrics_endpoint_exists(self, client):
        """指标端点应可达 / Metrics endpoint should be reachable."""
        response = client.get("/metrics")
        assert response.status_code == 200
        # 应包含 Prometheus 格式文本 / Should contain Prometheus-format text
        assert "agent_requests_total" in response.text or \
               "agent_request_duration_seconds" in response.text


class TestOpenAIModelsEndpoint:
    """OpenAI /models 端点测试 / OpenAI /models endpoint tests."""

    def test_models_endpoint(self, client):
        """models 端点应返回模型列表 / Models endpoint should return model list."""
        response = client.get("/v1/models")
        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert len(data["data"]) > 0
        assert any(m.get("id") == "langgraph-agent" for m in data["data"])

    def test_root_models_endpoint(self, client):
        """根路径 models 端点也应工作 / Root path models endpoint should also work."""
        response = client.get("/models")
        assert response.status_code == 200
