"""WebSocket 连接管理器 —— 广播、订阅、连接跟踪。"""

from fastapi import WebSocket


class WSManager:
    """管理所有浏览器客户端 WebSocket 连接。"""

    def __init__(self):
        self._sockets: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._sockets.add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        self._sockets.discard(ws)

    async def broadcast(self, message: dict) -> None:
        """向所有已连接的客户端广播消息。"""
        dead: list[WebSocket] = []
        for ws in self._sockets:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._sockets.discard(ws)

    async def send_to(self, ws: WebSocket, message: dict) -> None:
        """向单个客户端发送消息。"""
        try:
            await ws.send_json(message)
        except Exception:
            self._sockets.discard(ws)

    def is_empty(self) -> bool:
        return len(self._sockets) == 0

    @property
    def client_count(self) -> int:
        return len(self._sockets)
