"""交接审计日志 —— 记录所有 Agent 间数据传递。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..models import HandoffRecord


class HandoffLog:
    """所有 Agent 间交接的结构化日志。"""

    def __init__(self):
        self._entries: list[HandoffRecord] = []

    def record(
        self,
        from_agent_id: str,
        from_agent_name: str,
        to_agent_id: str,
        to_agent_name: str,
        task_id: str = "",
        result_summary: str = "",
        token_used: int = 0,
    ) -> HandoffRecord:
        entry = HandoffRecord(
            from_agent_id=from_agent_id,
            from_agent_name=from_agent_name,
            to_agent_id=to_agent_id,
            to_agent_name=to_agent_name,
            task_id=task_id,
            result_summary=result_summary,
            token_used=token_used,
        )
        self._entries.append(entry)
        return entry

    def recent(self, n: int = 50) -> list[HandoffRecord]:
        return self._entries[-n:]

    def all(self) -> list[HandoffRecord]:
        return list(self._entries)

    def clear(self) -> None:
        self._entries.clear()
