import os

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage

from src.agent import graph as agent_graph
from src.agent.graph import AgentExecutionError
from src.api import routes
from src.main import create_app


class FailingAgent:
    def invoke(self, _input):
        raise RuntimeError("model backend failed")


def test_orchestrator_propagates_execution_failure(monkeypatch):
    monkeypatch.setattr(agent_graph, "_build_react_agent", lambda: FailingAgent())
    monkeypatch.setattr(agent_graph.settings, "max_agent_retries", 0)
    state = {
        "messages": [HumanMessage(content="test")],
        "query": "test",
        "session_id": "test",
        "final_answer": "",
        "tool_log": [],
        "iteration_count": 0,
    }

    with pytest.raises(AgentExecutionError, match="execution failed"):
        agent_graph.agent_orchestrator(state)


@pytest.mark.parametrize(
    "path,payload",
    [
        (
            "/api/v1/agent/ask",
            {"question": "test", "session_id": "failure-test"},
        ),
        (
            "/v1/chat/completions",
            {
                "model": "langgraph-agent",
                "messages": [{"role": "user", "content": "test"}],
            },
        ),
    ],
)
def test_non_streaming_agent_failure_returns_503(monkeypatch, path, payload):
    os.environ["API_KEY"] = "failure-test-key"

    def fail_run_agent(**_kwargs):
        raise AgentExecutionError("backend detail must not leak")

    monkeypatch.setattr(routes, "run_agent", fail_run_agent)
    client = TestClient(create_app())
    response = client.post(
        path,
        json=payload,
        headers={"Authorization": "Bearer failure-test-key"},
    )

    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    assert "backend detail" not in response.text
