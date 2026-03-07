"""WebSocket connection manager."""

from __future__ import annotations

import json
from typing import Any

from fastapi import WebSocket


class ConnectionManager:
    """Manages per-room WebSocket connections."""

    def __init__(self) -> None:
        # room_code -> {player_id: websocket}
        self._rooms: dict[str, dict[str, WebSocket]] = {}

    def add(self, room_code: str, player_id: str, ws: WebSocket) -> None:
        self._rooms.setdefault(room_code, {})[player_id] = ws

    def remove(self, room_code: str, player_id: str) -> None:
        room = self._rooms.get(room_code)
        if room:
            room.pop(player_id, None)
            if not room:
                del self._rooms[room_code]

    def get(self, room_code: str, player_id: str) -> WebSocket | None:
        return self._rooms.get(room_code, {}).get(player_id)

    async def send_personal(
        self, room_code: str, player_id: str, msg_type: str, data: dict[str, Any] | None = None
    ) -> None:
        ws = self.get(room_code, player_id)
        if ws:
            try:
                await ws.send_text(json.dumps({"type": msg_type, "data": data or {}}, ensure_ascii=False))
            except Exception:
                self._rooms.get(room_code, {}).pop(player_id, None)

    async def broadcast(
        self, room_code: str, msg_type: str, data: dict[str, Any] | None = None
    ) -> None:
        payload = json.dumps({"type": msg_type, "data": data or {}}, ensure_ascii=False)
        dead: list[str] = []
        for pid, ws in list(self._rooms.get(room_code, {}).items()):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(pid)
        for pid in dead:
            self._rooms.get(room_code, {}).pop(pid, None)

    async def broadcast_except(
        self, room_code: str, exclude_id: str, msg_type: str, data: dict[str, Any] | None = None
    ) -> None:
        payload = json.dumps({"type": msg_type, "data": data or {}}, ensure_ascii=False)
        dead: list[str] = []
        for pid, ws in list(self._rooms.get(room_code, {}).items()):
            if pid == exclude_id:
                continue
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(pid)
        for pid in dead:
            self._rooms.get(room_code, {}).pop(pid, None)


manager = ConnectionManager()
