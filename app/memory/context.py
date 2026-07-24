"""记忆系统 —— 对话历史管理。"""

from __future__ import annotations

from collections import defaultdict


class ContextStore:
    """管理每个 Agent 的对话历史，带 token 预算裁剪。"""

    def __init__(self, max_tokens: int = 16000):
        self._contexts: dict[str, list[dict]] = defaultdict(list)
        self.max_tokens = max_tokens

    def add(self, agent_id: str, role: str, content: str) -> None:
        self._contexts[agent_id].append({"role": role, "content": content})
        self._trim(agent_id)

    def get(self, agent_id: str) -> list[dict]:
        return list(self._contexts.get(agent_id, []))

    def clear(self, agent_id: str) -> None:
        self._contexts[agent_id] = []

    def clear_all(self) -> None:
        self._contexts.clear()

    def __len__(self) -> int:
        return sum(len(v) for v in self._contexts.values())

    def recent(self, n: int = 50) -> list[dict]:
        """获取所有 Agent 的最近 n 条消息。"""
        all_messages = []
        for msgs in self._contexts.values():
            all_messages.extend(msgs)
        return all_messages[-n:]

    def _trim(self, agent_id: str) -> None:
        """超出 max_tokens 时移除最早的消息（粗略字符估计）。"""
        messages = self._contexts[agent_id]
        total = sum(len(m["content"]) for m in messages)
        # 粗略：~4 chars per token
        while total > self.max_tokens * 4 and len(messages) > 4:
            removed = messages.pop(0)
            total -= len(removed["content"])
