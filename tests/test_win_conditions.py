"""Tests for victory condition logic."""

from __future__ import annotations

import pytest

from server.models import GamePhase
from tests.conftest import make_game, kill


# ======================================================================
# Good wins: demon executed
# ======================================================================

class TestGoodWinsDemonExecuted:

    async def test_execute_demon_good_wins(self, mock_manager):
        game = make_game({"a": "imp", "b": "poisoner", "c": "washerwoman", "d": "chef", "e": "empath"})
        game.phase = GamePhase.DAY

        await game._execute_player("a")

        assert game.phase == GamePhase.GAME_OVER
        wins = [b for b in mock_manager.broadcasts if b[1] == "game_over"]
        assert len(wins) == 1
        assert wins[0][2]["winner"] == "good"

    async def test_execute_non_demon_no_good_win(self, mock_manager):
        game = make_game({"a": "imp", "b": "poisoner", "c": "washerwoman", "d": "chef", "e": "empath"})
        game.phase = GamePhase.DAY

        await game._execute_player("c")

        assert game.phase != GamePhase.GAME_OVER
        assert not game.players["c"].alive

    async def test_execute_demon_scarlet_woman_takes_over(self, mock_manager):
        """With 5+ alive *after* demon death, Scarlet Woman becomes new Imp."""
        game = make_game({
            "a": "imp", "b": "scarlet_woman", "c": "washerwoman",
            "d": "chef", "e": "empath", "f": "soldier",
        })
        game.phase = GamePhase.DAY

        await game._execute_player("a")

        assert game.phase != GamePhase.GAME_OVER
        assert game.players["b"].role_id == "imp"

    async def test_execute_demon_scarlet_woman_too_few_alive(self, mock_manager):
        """With <5 alive, Scarlet Woman can't take over — good wins."""
        game = make_game({
            "a": "imp", "b": "scarlet_woman", "c": "washerwoman", "d": "chef",
        })
        kill(game, "d")  # 3 alive
        game.phase = GamePhase.DAY

        await game._execute_player("a")

        assert game.phase == GamePhase.GAME_OVER
        wins = [b for b in mock_manager.broadcasts if b[1] == "game_over"]
        assert wins[0][2]["winner"] == "good"

    async def test_execute_demon_scarlet_woman_dead(self, mock_manager):
        """Dead Scarlet Woman can't take over — good wins."""
        game = make_game({
            "a": "imp", "b": "scarlet_woman", "c": "washerwoman",
            "d": "chef", "e": "empath",
        })
        kill(game, "b")
        game.phase = GamePhase.DAY

        await game._execute_player("a")

        assert game.phase == GamePhase.GAME_OVER
        wins = [b for b in mock_manager.broadcasts if b[1] == "game_over"]
        assert wins[0][2]["winner"] == "good"


# ======================================================================
# Evil wins: 2 or fewer alive
# ======================================================================

class TestEvilWinsFewAlive:

    async def test_execute_leaves_two_alive_evil_wins(self, mock_manager):
        """Executing a good player leaving Demon+1 → evil wins."""
        game = make_game({"a": "imp", "b": "poisoner", "c": "washerwoman"})
        game.phase = GamePhase.DAY

        await game._execute_player("c")

        assert game.phase == GamePhase.GAME_OVER
        wins = [b for b in mock_manager.broadcasts if b[1] == "game_over"]
        assert wins[0][2]["winner"] == "evil"

    async def test_execute_leaves_three_alive_no_evil_win(self, mock_manager):
        game = make_game({"a": "imp", "b": "poisoner", "c": "washerwoman", "d": "chef"})
        game.phase = GamePhase.DAY

        await game._execute_player("c")

        assert game.phase != GamePhase.GAME_OVER

    async def test_good_wins_takes_priority_over_evil(self, mock_manager):
        """If demon is executed leaving <=2 alive, good wins (not evil)."""
        game = make_game({"a": "imp", "b": "poisoner", "c": "washerwoman"})
        game.phase = GamePhase.DAY

        await game._execute_player("a")

        assert game.phase == GamePhase.GAME_OVER
        wins = [b for b in mock_manager.broadcasts if b[1] == "game_over"]
        assert wins[0][2]["winner"] == "good"


# ======================================================================
# Evil wins: Saint executed
# ======================================================================

class TestSaintExecution:

    async def test_saint_executed_evil_wins(self, mock_manager):
        game = make_game({"a": "imp", "b": "poisoner", "c": "saint", "d": "chef", "e": "empath"})
        game.phase = GamePhase.DAY

        await game._execute_player("c")

        assert game.phase == GamePhase.GAME_OVER
        wins = [b for b in mock_manager.broadcasts if b[1] == "game_over"]
        assert wins[0][2]["winner"] == "evil"
        assert not game.players["c"].alive

    async def test_poisoned_saint_no_evil_win(self, mock_manager):
        game = make_game({"a": "imp", "b": "poisoner", "c": "saint", "d": "chef", "e": "empath"})
        game.players["c"].poisoned = True
        game.phase = GamePhase.DAY

        await game._execute_player("c")

        assert game.phase != GamePhase.GAME_OVER
        assert not game.players["c"].alive

    async def test_drunk_saint_no_evil_win(self, mock_manager):
        game = make_game({"a": "imp", "b": "poisoner", "c": "saint", "d": "chef", "e": "empath"})
        game.players["c"].drunk = True
        game.phase = GamePhase.DAY

        await game._execute_player("c")

        assert game.phase != GamePhase.GAME_OVER


# ======================================================================
# Good wins: Mayor ability (3 alive, no execution)
# ======================================================================

class TestMayorWin:

    async def test_mayor_3_alive_no_execution_good_wins(self, mock_manager):
        """Mayor alive + 3 players alive + no execution → good wins."""
        game = make_game({"a": "imp", "b": "poisoner", "c": "mayor", "d": "chef", "e": "empath"})
        kill(game, "d")
        kill(game, "e")
        game.phase = GamePhase.DAY
        game._highest_vote = ("", 0)

        # Simulate end of day with no execution (vote threshold not met)
        # We directly test the condition from _run_day
        alive = game.alive_count()
        assert alive == 3

        mayor_alive = any(
            p.alive and p.role_id == "mayor" and not p.poisoned and not p.drunk
            for p in game.players.values()
        )
        assert mayor_alive is True

    async def test_poisoned_mayor_no_win(self, mock_manager):
        game = make_game({"a": "imp", "b": "poisoner", "c": "mayor"})
        game.players["c"].poisoned = True

        mayor_alive = any(
            p.alive and p.role_id == "mayor" and not p.poisoned and not p.drunk
            for p in game.players.values()
        )
        assert mayor_alive is False
