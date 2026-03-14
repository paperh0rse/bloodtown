"""WebSocket connection manager."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)

_STALE_THRESHOLD = 30  # seconds before a failed connection is removed


class ConnectionManager:
    """Manages per-room WebSocket connections."""

    def __init__(self) -> None:
        self._rooms: dict[str, dict[str, WebSocket]] = {}
        self._failed_at: dict[str, dict[str, float]] = {}

    def add(self, room_code: str, player_id: str, ws: WebSocket) -> None:
        self._rooms.setdefault(room_code, {})[player_id] = ws
        self._failed_at.get(room_code, {}).pop(player_id, None)

    def remove(self, room_code: str, player_id: str) -> None:
        room = self._rooms.get(room_code)
        if room:
            room.pop(player_id, None)
            if not room:
                del self._rooms[room_code]
        failed = self._failed_at.get(room_code)
        if failed:
            failed.pop(player_id, None)

    def get(self, room_code: str, player_id: str) -> WebSocket | None:
        return self._rooms.get(room_code, {}).get(player_id)

    def _mark_failed(self, room_code: str, player_id: str) -> None:
        failed = self._failed_at.setdefault(room_code, {})
        if player_id not in failed:
            failed[player_id] = time.monotonic()
            logger.warning("ws send failed for %s/%s, will remove after %ds", room_code, player_id, _STALE_THRESHOLD)

    def _collect_stale(self, room_code: str) -> None:
        failed = self._failed_at.get(room_code)
        if not failed:
            return
        now = time.monotonic()
        stale = [pid for pid, ts in failed.items() if now - ts > _STALE_THRESHOLD]
        room = self._rooms.get(room_code, {})
        for pid in stale:
            room.pop(pid, None)
            failed.pop(pid, None)
            logger.info("removed stale connection %s/%s", room_code, pid)
        if not failed:
            self._failed_at.pop(room_code, None)

    async def send_personal(
        self, room_code: str, player_id: str, msg_type: str, data: dict[str, Any] | None = None
    ) -> None:
        ws = self.get(room_code, player_id)
        if ws:
            try:
                await ws.send_text(json.dumps({"type": msg_type, "data": data or {}}, ensure_ascii=False))
                self._failed_at.get(room_code, {}).pop(player_id, None)
            except Exception:
                self._mark_failed(room_code, player_id)

    async def broadcast(
        self, room_code: str, msg_type: str, data: dict[str, Any] | None = None
    ) -> None:
        self._collect_stale(room_code)
        payload = json.dumps({"type": msg_type, "data": data or {}}, ensure_ascii=False)
        for pid, ws in list(self._rooms.get(room_code, {}).items()):
            try:
                await ws.send_text(payload)
                self._failed_at.get(room_code, {}).pop(pid, None)
            except Exception:
                self._mark_failed(room_code, pid)

    async def broadcast_except(
        self, room_code: str, exclude_id: str, msg_type: str, data: dict[str, Any] | None = None
    ) -> None:
        self._collect_stale(room_code)
        payload = json.dumps({"type": msg_type, "data": data or {}}, ensure_ascii=False)
        for pid, ws in list(self._rooms.get(room_code, {}).items()):
            if pid == exclude_id:
                continue
            try:
                await ws.send_text(payload)
                self._failed_at.get(room_code, {}).pop(pid, None)
            except Exception:
                self._mark_failed(room_code, pid)


manager = ConnectionManager()
