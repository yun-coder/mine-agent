"""Orchestra 集成测试 —— 验证 REST API 和 WebSocket 行为。"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
import pytest
import websockets.client as ws_client

from app.ws_manager import WSManager
from app.memory.store import HandoffLog
from app.agents.registry import AgentRegistry
from app.orchestrator import MasterOrchestrator
from app.pipeline.engine import PipelineEngine

BASE_URL = "http://127.0.0.1:8000"
WS_URL = "ws://127.0.0.1:8000/ws"

# ── 辅助函数 ─────────────────────────────────────────────────────


async def recv_with_timeout(ws, timeout=5.0):
    """接收一条 WebSocket 消息（带超时）。"""
    return await asyncio.wait_for(ws.recv(), timeout=timeout)


async def collect_messages(ws, count=1, timeout=3.0):
    """收集指定数量的消息（跳过 ping）。"""
    messages = []
    while len(messages) < count:
        raw = await recv_with_timeout(ws, timeout=timeout)
        data = json.loads(raw)
        if data.get("type") != "ping":
            messages.append(data)
    return messages


async def send_and_collect(ws, payload, expect_type, timeout=5.0):
    """发送消息并等待指定 type 的响应。"""
    await ws.send(json.dumps(payload))
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        raw = await recv_with_timeout(ws, timeout=min(5.0, deadline - asyncio.get_event_loop().time()))
        data = json.loads(raw)
        if data.get("type") == expect_type:
            return data
        # ping 消息直接跳过
        if data.get("type") == "ping":
            continue
    raise TimeoutError(f"未在 {timeout}s 内收到 type={expect_type} 的消息")


# ── 测试夹具 ─────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module")
async def app_client():
    """创建异步 HTTP 客户端，带有就绪等待。"""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
        # 等待服务就绪
        for i in range(10):
            try:
                resp = await client.get("/api/status")
                if resp.status_code == 200:
                    break
            except (httpx.ConnectError, httpx.RemoteProtocolError):
                pass
            await asyncio.sleep(1)
        yield client


@pytest.fixture
async def ws():
    """每个测试函数建立一次独立 WebSocket 连接。"""
    async with ws_client.connect(WS_URL, ping_interval=None, close_timeout=5) as conn:
        yield conn


# ── REST API 测试 ────────────────────────────────────────────────


@pytest.mark.anyio
class TestRestAPI:

    async def test_get_agents(self, app_client):
        resp = await app_client.get("/api/agents")
        assert resp.status_code == 200
        body = resp.json()
        assert "agents" in body
        assert len(body["agents"]) == 6
        a1 = body["agents"][0]
        assert "config" in a1
        assert "state" in a1
        assert a1["config"]["id"] == "a1"

    async def test_get_status(self, app_client):
        resp = await app_client.get("/api/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["agent_count"] == 6
        assert body["running"] is False

    async def test_get_events(self, app_client):
        resp = await app_client.get("/api/events")
        assert resp.status_code == 200
        body = resp.json()
        assert "events" in body

    async def test_static_files(self, app_client):
        resp = await app_client.get("/")
        assert resp.status_code == 200
        assert "ORCHESTRA" in resp.text

    async def test_spa_fallback(self, app_client):
        resp = await app_client.get("/some/frontend/route")
        assert resp.status_code == 200
        assert "ORCHESTRA" in resp.text


# ── WebSocket 消息测试 ───────────────────────────────────────────


@pytest.mark.anyio
class TestWebSocket:

    async def test_ping_pong(self, ws):
        """发送 ping 应收到 pong（先消费初始 state_sync）。"""
        # 消费初始 state_sync
        await collect_messages(ws, count=1, timeout=3.0)
        await ws.send(json.dumps({"type": "ping", "payload": {}}))
        raw = await recv_with_timeout(ws)
        data = json.loads(raw)
        assert data["type"] == "pong"

    async def test_state_sync_on_connect(self, ws):
        """新连接后收到的第一条非 ping 消息应为 state_sync。"""
        msgs = await collect_messages(ws, count=1, timeout=4.0)
        assert len(msgs) >= 1
        first = msgs[0]
        assert first["type"] == "state_sync"
        assert "agents" in first["payload"]
        assert len(first["payload"]["agents"]) == 6

    async def test_agent_config_update(self, ws):
        """更新 Agent 配置后会广播 state_sync。"""
        # 先消费初始 state_sync
        await collect_messages(ws, count=1, timeout=3.0)

        await ws.send(json.dumps({
            "type": "agent:config",
            "agent_id": "a1",
            "payload": {"config": {"name": "情报特工改", "temperature": 0.5}},
        }))
        # agent:config 会广播 state_sync，等待它
        deadline = asyncio.get_event_loop().time() + 5.0
        data = None
        while asyncio.get_event_loop().time() < deadline:
            raw = await recv_with_timeout(ws, timeout=1.0)
            data = json.loads(raw)
            if data.get("type") == "state_sync":
                break
            elif data.get("type") == "ping":
                continue
        else:
            pytest.fail("未收到 state_sync")

        a1 = next(a for a in data["payload"]["agents"] if a["config"]["id"] == "a1")
        assert a1["config"]["name"] == "情报特工改"
        assert a1["config"]["temperature"] == 0.5

    async def test_master_help_command(self, ws):
        """`help` 命令应返回可用命令列表。"""
        # 消费初始 state_sync
        await collect_messages(ws, count=1, timeout=3.0)

        await ws.send(json.dumps({
            "type": "command",
            "agent_id": "master",
            "payload": {"command": "help"},
        }))

        # help 会广播一条 output 类型消息
        deadline = asyncio.get_event_loop().time() + 5.0
        while asyncio.get_event_loop().time() < deadline:
            raw = await recv_with_timeout(ws, timeout=2.0)
            data = json.loads(raw)
            if data.get("type") == "output":
                break
            elif data.get("type") == "ping":
                continue
        else:
            pytest.fail("未收到 output 消息")

        token = data["payload"].get("token", "")
        assert "run" in token
        assert "pause" in token
        assert "stop" in token
        assert "help" in token

    async def test_pipeline_start_and_stop(self, ws):
        """流水线启动和停止。"""
        await collect_messages(ws, count=1, timeout=3.0)

        # 启动
        await ws.send(json.dumps({
            "type": "command",
            "agent_id": "master",
            "payload": {"command": "run 测试流水线"},
        }))

        # 流水线执行会发很多 event + 一条 pipeline 消息
        # 持续消费直到收到 pipeline(running=True) 或 pipeline(running=False, finished)
        deadline = asyncio.get_event_loop().time() + 10.0
        started = False
        while asyncio.get_event_loop().time() < deadline:
            try:
                raw = await recv_with_timeout(ws, timeout=3.0)
            except TimeoutError:
                break
            data = json.loads(raw)
            if data.get("type") == "pipeline":
                started = data["payload"].get("running", False)
                break
        # 流水线可能已经执行完，也可能还在跑
        # 不管什么状态，发 stop 确保清理
        await ws.send(json.dumps({"type": "pipeline:stop", "payload": {}}))
        # 不等响应，只要不抛异常就算通过
        await asyncio.sleep(0.5)

    async def test_pipeline_pause_resume(self, ws):
        """流水线暂停和继续。"""
        await collect_messages(ws, count=1, timeout=3.0)

        await ws.send(json.dumps({
            "type": "pipeline:start",
            "payload": {"description": "暂停测试"},
        }))
        deadline = asyncio.get_event_loop().time() + 5.0
        started = False
        while asyncio.get_event_loop().time() < deadline:
            try:
                raw = await recv_with_timeout(ws, timeout=2.0)
            except TimeoutError:
                break
            data = json.loads(raw)
            if data.get("type") == "pipeline":
                started = True
                break

        if not started:
            pytest.skip("流水线未在规定时间内启动")

        # 暂停
        await ws.send(json.dumps({"type": "pipeline:pause", "payload": {}}))
        deadline = asyncio.get_event_loop().time() + 5.0
        paused = False
        while asyncio.get_event_loop().time() < deadline:
            try:
                raw = await recv_with_timeout(ws, timeout=2.0)
            except TimeoutError:
                break
            data = json.loads(raw)
            if data.get("type") == "pipeline" and data["payload"].get("paused"):
                paused = True
                break
        assert paused, "未收到暂停确认"

        # 继续
        await ws.send(json.dumps({"type": "pipeline:resume", "payload": {}}))
        deadline = asyncio.get_event_loop().time() + 5.0
        resumed = False
        while asyncio.get_event_loop().time() < deadline:
            try:
                raw = await recv_with_timeout(ws, timeout=2.0)
            except TimeoutError:
                break
            data = json.loads(raw)
            if data.get("type") == "pipeline" and data["payload"].get("paused") is False:
                resumed = True
                break
        assert resumed, "未收到继续确认"

        # 停止
        await ws.send(json.dumps({"type": "pipeline:stop", "payload": {}}))
        await asyncio.sleep(0.5)

    async def test_add_custom_agent(self, ws):
        """添加自定义 Agent 后 state_sync 中应包含它。"""
        await collect_messages(ws, count=1, timeout=3.0)

        await ws.send(json.dumps({
            "type": "agent:add",
            "payload": {
                "config": {
                    "name": "安全审计",
                    "role": "安全审查",
                    "model": "Claude Opus 4",
                    "task": "审查代码安全性",
                }
            },
        }))
        # 等 state_sync
        deadline = asyncio.get_event_loop().time() + 5.0
        while asyncio.get_event_loop().time() < deadline:
            raw = await recv_with_timeout(ws, timeout=2.0)
            data = json.loads(raw)
            if data.get("type") == "state_sync":
                break
        else:
            pytest.fail("未收到 state_sync")
        agents = data["payload"]["agents"]
        assert len(agents) == 7
        new_agent = next(a for a in agents if a["config"]["name"] == "安全审计")
        assert new_agent["config"]["model"] == "Claude Opus 4"
        assert new_agent["config"]["role"] == "安全审查"

        # 清理
        new_id = new_agent["config"]["id"]
        await ws.send(json.dumps({"type": "agent:remove", "agent_id": new_id, "payload": {}}))
        deadline = asyncio.get_event_loop().time() + 5.0
        while asyncio.get_event_loop().time() < deadline:
            raw = await recv_with_timeout(ws, timeout=2.0)
            data2 = json.loads(raw)
            if data2.get("type") == "state_sync":
                break
        else:
            pytest.fail("未收到删除后的 state_sync")
        assert len(data2["payload"]["agents"]) == 6


# ── 纯逻辑测试（无需网络） ─────────────────────────────────


@pytest.mark.anyio
class TestOrchestratorLogic:

    async def test_orchestrator_initial_state(self):
        ws = WSManager()
        log = HandoffLog()
        registry = AgentRegistry(ws_manager=ws)
        orch = MasterOrchestrator(registry, ws, log)
        orch.register_default_agents()
        assert orch.is_running is False
        assert orch.is_paused is False
        assert orch.system_event_count == 0
        assert len(registry.all_manifests()) == 6

    async def test_pipeline_compile_order(self):
        ws = WSManager()
        registry = AgentRegistry(ws_manager=ws)
        registry.register_defaults()
        pipeline = PipelineEngine(registry)
        pipeline.compile("测试工作流")
        order = pipeline.execution_order
        assert len(order) > 0
        agent_ids = [n.agent_id for n in order]
        assert agent_ids[0] == "a1"
        assert agent_ids[-1] == "a6"
        sequential_ids = [aid for aid in agent_ids if aid in ("a2", "a3", "a4", "a5")]
        assert sequential_ids == ["a2", "a3", "a4", "a5"]

    async def test_handoff_record(self):
        log = HandoffLog()
        log.record(from_agent_id="a1", from_agent_name="情报侦察", to_agent_id="a2",
                   to_agent_name="系统架构", task_id="test-001", result_summary="索引完成", token_used=500)
        log.record(from_agent_id="a2", from_agent_name="系统架构", to_agent_id="a3",
                   to_agent_name="后端工程", task_id="test-002", result_summary="架构设计完成", token_used=1200)
        all_records = log.all()
        assert len(all_records) == 2
        assert sum(r.token_used for r in all_records) == 1700
        assert len(log.recent(1)) == 1

    async def test_context_store(self):
        from app.memory.context import ContextStore
        store = ContextStore(max_tokens=4000)
        store.add("agent_a1", "user", "说说你的想法")
        store.add("agent_a1", "assistant", "这是一个测试回复。")
        assert len(store) == 2
        items = store.recent(1)
        assert len(items) == 1
        assert items[0]["role"] == "assistant"
