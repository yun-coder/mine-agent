"""MessageBus —— 进程内异步发布/订阅消息总线。

用于 Agent 之间、Agent 与 Orchestrator 之间的异步通信。
参考学习项目的 async MessageBus 模式，但适配 Orchestra 的现有架构。

设计要点：
- asyncio.Queue 作为每个订阅者的收件箱，协程安全
- 支持广播（BROADCAST）和点对点路由
- 订阅可过滤消息类型
- Agent 可以发布事件（finding_complete, error, phase_transition 等）
- Orchestrator 订阅事件做全局决策

使用方式:
    bus = MessageBus()
    sub = bus.subscribe("agent_a1", types=["task", "status"])
    await bus.publish(Envelope(sender="agent_a2", target="agent_a1",
                               type="task", payload={"action": "review"}))
    env = await sub.get()  # 接收消息
"""

from __future__ import annotations

import asyncio
import itertools
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# 广播目标常量
BROADCAST = "*"

# 全局单调递增消息序号
_seq_counter = itertools.count(1)

# 总线启动时间（用于相对时间戳）
_START_TIME = time.monotonic()


@dataclass
class Envelope:
    """消息信封：总线里流动的最小单元。"""
    sender: str
    target: str
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    seq: int = field(default_factory=lambda: next(_seq_counter))
    ts: float = field(default_factory=lambda: time.monotonic() - _START_TIME)

    def __repr__(self) -> str:
        tgt = "ALL" if self.target == BROADCAST else self.target
        return f"<Env #{self.seq} {self.sender}→{tgt} [{self.type}]>"


class Subscription:
    """订阅句柄：收件箱队列 + 关心的消息类型。"""

    def __init__(self, owner_id: str, types: Optional[list[str]] = None):
        self.owner_id = owner_id
        self.types = set(types) if types else None  # None = 接收所有类型
        self.inbox: asyncio.Queue[Envelope] = asyncio.Queue()

    def accepts(self, env: Envelope) -> bool:
        return self.types is None or env.type in self.types

    async def get(self) -> Envelope:
        """阻塞直到收到一条消息。"""
        return await self.inbox.get()

    def get_nowait(self) -> Optional[Envelope]:
        """非阻塞获取，无消息返回 None。"""
        try:
            return self.inbox.get_nowait()
        except asyncio.QueueEmpty:
            return None


class MessageBus:
    """进程内异步消息总线。

    管理发布/订阅路由，每条消息封装在 Envelope 里。
    """

    def __init__(self):
        self._subscriptions: dict[str, list[Subscription]] = {}
        self._broadcast_subscriptions: list[Subscription] = []
        self._lock = asyncio.Lock()

    async def subscribe(
        self,
        owner_id: str,
        types: Optional[list[str]] = None,
    ) -> Subscription:
        """创建一个订阅，返回 Subscription 句柄。

        Args:
            owner_id: 订阅者标识（Agent ID / topic）
            types: 感兴趣的消息类型列表，None 表示接收所有类型
        """
        sub = Subscription(owner_id, types)
        async with self._lock:
            if owner_id not in self._subscriptions:
                self._subscriptions[owner_id] = []
            self._subscriptions[owner_id].append(sub)
        return sub

    async def unsubscribe(self, sub: Subscription) -> None:
        """移除一个订阅。"""
        async with self._lock:
            subs = self._subscriptions.get(sub.owner_id, [])
            if sub in subs:
                subs.remove(sub)

    async def publish(self, envelope: Envelope) -> int:
        """发布一条消息到总线。

        Args:
            envelope: 消息信封

        Returns:
            送达的订阅者数量
        """
        delivered = 0
        async with self._lock:
            # 1) 点对点：发送给指定 target
            if envelope.target != BROADCAST:
                targets = [envelope.target]
            else:
                targets = list(self._subscriptions.keys())

            for target in targets:
                subs = self._subscriptions.get(target, [])
                for sub in subs:
                    if sub.accepts(envelope):
                        try:
                            sub.inbox.put_nowait(envelope)
                            delivered += 1
                        except asyncio.QueueFull:
                            pass  # 队列满则丢消息，不阻塞

            # 2) BROADCAST 也发送给所有广播订阅
            if envelope.target == BROADCAST:
                for sub in self._broadcast_subscriptions:
                    if sub.accepts(envelope):
                        try:
                            sub.inbox.put_nowait(envelope)
                            delivered += 1
                        except asyncio.QueueFull:
                            pass

        return delivered

    async def publish_event(
        self,
        sender: str,
        type: str,
        payload: dict[str, Any] | None = None,
        target: str = BROADCAST,
    ) -> int:
        """便捷方法：创建并发布一个事件型消息。"""
        envelope = Envelope(
            sender=sender,
            target=target,
            type=type,
            payload=payload or {},
        )
        return await self.publish(envelope)

    @property
    def subscriber_count(self) -> int:
        """当前活跃订阅者总数。"""
        total = 0
        for subs in self._subscriptions.values():
            total += len(subs)
        total += len(self._broadcast_subscriptions)
        return total
