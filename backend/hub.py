# -*- coding:utf-8 -*-
"""WebSocket 连接管理与广播。"""

import json
from typing import List, Set

from fastapi import WebSocket


class ConnectionHub:
    """维护所有在线 WebSocket 连接，把 tick 广播给每个客户端。"""

    def __init__(self) -> None:
        self._clients: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._clients.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self._clients.discard(websocket)

    async def broadcast(self, tick: dict) -> None:
        """发送一条 tick；发送失败的连接会被清理。"""
        message = json.dumps(tick, ensure_ascii=False)
        dead: List[WebSocket] = []
        for websocket in list(self._clients):
            try:
                await websocket.send_text(message)
            except Exception:
                dead.append(websocket)
        for websocket in dead:
            self.disconnect(websocket)
