"""TencentDB Agent Memory (TDAI) HTTP 客户端 / HTTP Client for TDAI Memory Gateway.

与 TDAI Gateway 通信，提供对话记忆的存取能力。
Communicates with TDAI Gateway for conversation memory storage and recall.

API 文档 / API Reference:
  POST /capture            — 保存对话到记忆系统
  POST /recall             — 召回相关记忆
  POST /search/memories    — 搜索 L1 原子记忆
  POST /search/conversations — 搜索 L0 原始对话
  POST /session/end        — 结束会话
  GET  /health             — 健康检查
"""

from __future__ import annotations

from typing import Any

import httpx
from loguru import logger

from src.config import settings

# 默认超时 / Default timeouts
_CAPTURE_TIMEOUT = 15.0
_RECALL_TIMEOUT = 10.0
_SEARCH_TIMEOUT = 10.0


class TDAIMemoryClient:
    """TDAI Memory Gateway HTTP 客户端。

    线程安全 / Thread-safe（httpx.AsyncClient 在 asyncio 事件循环中使用）。

    用法 / Usage:
        client = TDAIMemoryClient()
        memories = await client.recall("什么是企业知识库?")
        await client.capture("用户问题", "AI回答", session_key="sess-001")
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        enabled: bool | None = None,
    ):
        self.base_url = (base_url or settings.tdai_gateway_url).rstrip("/")
        self.api_key = api_key or ""
        self._enabled = enabled if enabled is not None else settings.tdai_enabled
        self._headers: dict[str, str] = {}
        if self.api_key:
            self._headers["Authorization"] = f"Bearer {self.api_key}"

        if self._enabled:
            logger.info(f"[TDAI] 客户端已配置 / Client configured → {self.base_url}")
        else:
            logger.info("[TDAI] 客户端已禁用 / Client disabled (tdai_enabled=False)")

    # ------------------------------------------------------------------
    # 启用 / 禁用状态 / Enabled / Disabled
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    def disable(self) -> None:
        """临时禁用记忆存取（不修改 settings）。"""
        self._enabled = False

    def enable(self) -> None:
        """启用记忆存取。"""
        self._enabled = True

    # ------------------------------------------------------------------
    # 健康检查 / Health check
    # ------------------------------------------------------------------

    async def health(self) -> dict[str, Any]:
        """检查 TDAI Gateway 健康状态。"""
        if not self._enabled:
            return {"status": "disabled"}
        try:
            async with httpx.AsyncClient(timeout=5.0) as c:
                resp = await c.get(f"{self.base_url}/health")
                if resp.is_success:
                    data = resp.json()
                    logger.debug(f"[TDAI] health={data.get('status')}")
                    return data
                return {"status": "error", "code": resp.status_code}
        except Exception as exc:
            logger.warning(f"[TDAI] 健康检查失败 / Health check failed: {exc}")
            return {"status": "unreachable", "error": str(exc)}

    # ------------------------------------------------------------------
    # 捕获（保存对话到记忆）/ Capture — save a conversation turn
    # ------------------------------------------------------------------

    async def capture(
        self,
        user_content: str,
        assistant_content: str,
        session_key: str = "",
        session_id: str = "",
        messages: list[dict] | None = None,
    ) -> dict[str, Any]:
        """保存一轮对话到记忆系统。

        TDAI 会从 L0 记录自动触发 L1→L2→L3 的异步提取管线。
        返回: { l0_recorded, scheduler_notified }
        """
        if not self._enabled:
            return {"l0_recorded": 0, "scheduler_notified": False}

        body: dict[str, Any] = {
            "user_content": user_content,
            "assistant_content": assistant_content,
            "session_key": session_key or "default",
        }
        if session_id:
            body["session_id"] = session_id
        if messages:
            body["messages"] = messages

        try:
            async with httpx.AsyncClient(timeout=_CAPTURE_TIMEOUT) as c:
                resp = await c.post(
                    f"{self.base_url}/capture",
                    json=body,
                    headers=self._headers,
                )
                if resp.is_success:
                    data = resp.json()
                    logger.debug(
                        f"[TDAI] capture OK: l0={data.get('l0_recorded', 0)}, "
                        f"notified={data.get('scheduler_notified', False)}"
                    )
                    return data
                else:
                    logger.warning(
                        f"[TDAI] capture failed: {resp.status_code} {resp.text[:200]}"
                    )
                    return {"l0_recorded": 0, "scheduler_notified": False, "error": resp.text[:200]}
        except Exception as exc:
            logger.warning(f"[TDAI] capture exception: {exc}")
            return {"l0_recorded": 0, "scheduler_notified": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # 召回（获取与 query 相关的记忆上下文）/ Recall — get memory context
    # ------------------------------------------------------------------

    async def recall(
        self,
        query: str,
        session_key: str = "",
    ) -> dict[str, Any]:
        """召回与 query 相关的记忆。

        返回 TDAI 的 contextual 结果，包含 appendSystemContext（系统提示词片段）。
        返回: { appendSystemContext, strategy, memory_count }
        如果出错或禁用，返回空上下文。
        """
        if not self._enabled:
            return {"appendSystemContext": "", "strategy": "disabled", "memory_count": 0}

        body: dict[str, Any] = {"query": query}
        if session_key:
            body["session_key"] = session_key

        try:
            async with httpx.AsyncClient(timeout=_RECALL_TIMEOUT) as c:
                resp = await c.post(
                    f"{self.base_url}/recall",
                    json=body,
                    headers=self._headers,
                )
                if resp.is_success:
                    data = resp.json()
                    count = data.get("memory_count", 0)
                    if count > 0:
                        logger.info(f"[TDAI] recall: {count} memories found (strategy={data.get('strategy')})")
                    else:
                        logger.debug("[TDAI] recall: no memories found")
                    return data
                else:
                    logger.debug(f"[TDAI] recall failed: {resp.status_code}")
                    return {"appendSystemContext": "", "strategy": "error", "memory_count": 0}
        except Exception as exc:
            logger.debug(f"[TDAI] recall exception: {exc}")
            return {"appendSystemContext": "", "strategy": "error", "memory_count": 0}

    # ------------------------------------------------------------------
    # 搜索记忆（L1 原子记忆）/ Search L1 memories
    # ------------------------------------------------------------------

    async def search_memories(
        self,
        query: str,
        limit: int = 5,
        type_filter: str = "",
        scene: str = "",
    ) -> list[str]:
        """搜索 L1 原子记忆。

        返回文本列表。当 TDAI 禁用或出错时返回空列表。
        """
        if not self._enabled:
            return []

        body: dict[str, Any] = {"query": query, "limit": limit}
        if type_filter:
            body["type"] = type_filter
        if scene:
            body["scene"] = scene

        try:
            async with httpx.AsyncClient(timeout=_SEARCH_TIMEOUT) as c:
                resp = await c.post(
                    f"{self.base_url}/search/memories",
                    json=body,
                    headers=self._headers,
                )
                if resp.is_success:
                    return resp.json().get("results", [])
        except Exception as exc:
            logger.debug(f"[TDAI] search_memories exception: {exc}")
        return []

    # ------------------------------------------------------------------
    # 搜索对话（L0 原始对话）/ Search L0 conversations
    # ------------------------------------------------------------------

    async def search_conversations(
        self,
        query: str,
        limit: int = 5,
        session_key: str = "",
    ) -> list[str]:
        """搜索 L0 原始对话记录。"""
        if not self._enabled:
            return []

        body: dict[str, Any] = {"query": query, "limit": limit}
        if session_key:
            body["session_key"] = session_key

        try:
            async with httpx.AsyncClient(timeout=_SEARCH_TIMEOUT) as c:
                resp = await c.post(
                    f"{self.base_url}/search/conversations",
                    json=body,
                    headers=self._headers,
                )
                if resp.is_success:
                    return resp.json().get("results", [])
        except Exception as exc:
            logger.debug(f"[TDAI] search_conversations exception: {exc}")
        return []

    # ------------------------------------------------------------------
    # 结束会话 / End session
    # ------------------------------------------------------------------

    async def end_session(self, session_key: str) -> bool:
        """通知 TDAI 当前会话已结束，触发记忆管线处理。"""
        if not self._enabled:
            return False
        try:
            async with httpx.AsyncClient(timeout=5.0) as c:
                resp = await c.post(
                    f"{self.base_url}/session/end",
                    json={"session_key": session_key},
                    headers=self._headers,
                )
                return resp.is_success
        except Exception:
            return False

    # ------------------------------------------------------------------
    # 同步方法（供同步 graph invoke 调用）/ Sync methods for graph
    # ------------------------------------------------------------------

    def sync_recall(self, query: str, session_key: str = "") -> dict[str, Any]:
        """同步版的 recall — 供 LangGraph 同步调用。

        返回: { appendSystemContext, strategy, memory_count }
        """
        if not self._enabled:
            return {"appendSystemContext": "", "strategy": "disabled", "memory_count": 0}

        body: dict[str, Any] = {"query": query}
        if session_key:
            body["session_key"] = session_key

        try:
            with httpx.Client(timeout=_RECALL_TIMEOUT) as c:
                resp = c.post(
                    f"{self.base_url}/recall",
                    json=body,
                    headers=self._headers,
                )
                if resp.is_success:
                    data = resp.json()
                    count = data.get("memory_count", 0)
                    if count > 0:
                        logger.info(
                            f"[TDAI] sync_recall: {count} memories found "
                            f"(strategy={data.get('strategy')})"
                        )
                    return data
                logger.debug(f"[TDAI] sync_recall failed: {resp.status_code}")
        except Exception as exc:
            logger.debug(f"[TDAI] sync_recall exception: {exc}")
        return {"appendSystemContext": "", "strategy": "error", "memory_count": 0}

    def sync_capture(
        self,
        user_content: str,
        assistant_content: str,
        session_key: str = "",
        session_id: str = "",
    ) -> dict[str, Any]:
        """同步版的 capture — 供 LangGraph 同步调用。

        返回: { l0_recorded, scheduler_notified }
        """
        if not self._enabled:
            return {"l0_recorded": 0, "scheduler_notified": False}

        body: dict[str, Any] = {
            "user_content": user_content,
            "assistant_content": assistant_content,
            "session_key": session_key or "default",
        }
        if session_id:
            body["session_id"] = session_id

        try:
            with httpx.Client(timeout=_CAPTURE_TIMEOUT) as c:
                resp = c.post(
                    f"{self.base_url}/capture",
                    json=body,
                    headers=self._headers,
                )
                if resp.is_success:
                    data = resp.json()
                    logger.debug(
                        f"[TDAI] sync_capture OK: l0={data.get('l0_recorded', 0)}"
                    )
                    return data
                logger.warning(
                    f"[TDAI] sync_capture failed: {resp.status_code} {resp.text[:200]}"
                )
        except Exception as exc:
            logger.debug(f"[TDAI] sync_capture exception: {exc}")
        return {"l0_recorded": 0, "scheduler_notified": False}


# ------------------------------------------------------------------
# 模块级单例 / Module-level singleton
# ------------------------------------------------------------------
_client: TDAIMemoryClient | None = None


def get_client() -> TDAIMemoryClient:
    """获取 TDAI 客户端单例。"""
    global _client
    if _client is None:
        _client = TDAIMemoryClient()
    return _client
