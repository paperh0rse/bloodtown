"""Shared fixtures and helpers for game engine tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from server.models import Alignment, DaySubPhase, GamePhase, PlayerState
from server.game_engine import Game


class FakeManager:
    """Drop-in replacement for ConnectionManager that records calls."""

    def __init__(self):
        self.messages: list[tuple[str, str, str, dict]] = []
        self.broadcasts: list[tuple[str, str, dict]] = []

    async def send_personal(self, room_code, player_id, msg_type, data=None):
        self.messages.append((room_code, player_id, msg_type, data or {}))

    async def broadcast(self, room_code, msg_type, data=None):
        self.broadcasts.append((room_code, msg_type, data or {}))

    async def broadcast_except(self, room_code, exclude_id, msg_type, data=None):
        self.broadcasts.append((room_code, msg_type, data or {}))

    def add(self, *a, **kw):
        pass

    def remove(self, *a, **kw):
        pass

    def get(self, *a, **kw):
        return None


@pytest.fixture(autouse=True)
def mock_manager(monkeypatch):
    """Patch the global `manager` singleton with a FakeManager for every test."""
    fake = FakeManager()
    monkeypatch.setattr("server.game_engine.manager", fake)
    return fake


def make_game(roles: dict[str, str], *, room_code: str = "TEST") -> Game:
    """Create a Game with pre-assigned roles.

    Args:
        roles: mapping of player_id -> role_id.
               Special prefixes in role_id:
                 "evil:" forces evil alignment (e.g. "evil:recluse")
    """
    game = Game(room_code)
    for i, (pid, role_spec) in enumerate(roles.items()):
        forced_evil = role_spec.startswith("evil:")
        role_id = role_spec.removeprefix("evil:")

        p = PlayerState(player_id=pid, name=f"P{i+1}", seat=i)
        from server.role_data import ROLE_BY_ID
        from server.models import Team
        rd = ROLE_BY_ID.get(role_id)
        if rd:
            if forced_evil:
                p.alignment = Alignment.EVIL
            elif rd.team in (Team.MINION, Team.DEMON):
                p.alignment = Alignment.EVIL
            else:
                p.alignment = Alignment.GOOD
        p.role_id = role_id
        game.players[pid] = p
        game.seat_order.append(pid)

    game.host_id = game.seat_order[0]
    return game


def kill(game: Game, pid: str) -> None:
    """Kill a player (set alive=False)."""
    game.players[pid].alive = False


def poison(game: Game, pid: str) -> None:
    """Poison a player."""
    game.players[pid].poisoned = True


def protect(game: Game, pid: str) -> None:
    """Monk-protect a player."""
    game.players[pid].protected = True
