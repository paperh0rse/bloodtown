"""Tests for night death resolution and related win conditions."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from server.models import GamePhase
from tests.conftest import make_game, kill


# ======================================================================
# Night death resolution
# ======================================================================

class TestNightDeathResolution:

    async def test_single_death(self, mock_manager):
        game = make_game({"a": "imp", "b": "washerwoman", "c": "chef", "d": "empath", "e": "poisoner"})
        game._night_deaths = ["b"]

        await game._resolve_night_deaths()

        assert not game.players["b"].alive
        results = [b for b in mock_manager.broadcasts if b[1] == "night_result"]
        assert len(results) == 1
        assert "b" in results[0][2]["deaths"]

    async def test_no_deaths(self, mock_manager):
        game = make_game({"a": "imp", "b": "washerwoman", "c": "chef", "d": "empath", "e": "poisoner"})
        game._night_deaths = []

        await game._resolve_night_deaths()

        assert all(p.alive for p in game.players.values())
        results = [b for b in mock_manager.broadcasts if b[1] == "night_result"]
        assert results[0][2]["deaths"] == []

    async def test_duplicate_deaths_deduplicated(self, mock_manager):
        game = make_game({"a": "imp", "b": "washerwoman", "c": "chef", "d": "empath", "e": "poisoner"})
        game._night_deaths = ["b", "b", "b"]

        await game._resolve_night_deaths()

        assert not game.players["b"].alive
        alive_count = sum(1 for p in game.players.values() if p.alive)
        assert alive_count == 4

    async def test_already_dead_player_not_killed_again(self, mock_manager):
        game = make_game({"a": "imp", "b": "washerwoman", "c": "chef", "d": "empath", "e": "poisoner"})
        kill(game, "b")
        game._night_deaths = ["b"]

        await game._resolve_night_deaths()

        alive_count = sum(1 for p in game.players.values() if p.alive)
        assert alive_count == 4  # unchanged


# ======================================================================
# Win conditions after night deaths
# ======================================================================

class TestNightDeathWinConditions:

    async def test_demon_dies_at_night_good_wins(self, mock_manager):
        """If demon dies at night (e.g. imp self-kill with no minion), good wins."""
        game = make_game({"a": "imp", "b": "washerwoman", "c": "chef", "d": "empath", "e": "saint"})
        game._night_deaths = ["a"]

        await game._resolve_night_deaths()

        assert game.phase == GamePhase.GAME_OVER
        wins = [b for b in mock_manager.broadcasts if b[1] == "game_over"]
        assert wins[0][2]["winner"] == "good"

    async def test_demon_dies_scarlet_woman_takes_over(self, mock_manager):
        """With 5+ alive after demon death, Scarlet Woman becomes Imp."""
        game = make_game({
            "a": "imp", "b": "scarlet_woman", "c": "washerwoman",
            "d": "chef", "e": "empath", "f": "soldier",
        })
        game._night_deaths = ["a"]

        await game._resolve_night_deaths()

        assert game.phase != GamePhase.GAME_OVER
        assert game.players["b"].role_id == "imp"

    async def test_two_alive_after_night_evil_wins(self, mock_manager):
        game = make_game({"a": "imp", "b": "poisoner", "c": "washerwoman"})
        game._night_deaths = ["c"]

        await game._resolve_night_deaths()

        assert game.phase == GamePhase.GAME_OVER
        wins = [b for b in mock_manager.broadcasts if b[1] == "game_over"]
        assert wins[0][2]["winner"] == "evil"

    async def test_multiple_deaths_check_win(self, mock_manager):
        """Multiple night deaths can trigger evil win."""
        game = make_game({"a": "imp", "b": "poisoner", "c": "washerwoman", "d": "chef"})
        game._night_deaths = ["c", "d"]

        await game._resolve_night_deaths()

        assert game.phase == GamePhase.GAME_OVER
        wins = [b for b in mock_manager.broadcasts if b[1] == "game_over"]
        assert wins[0][2]["winner"] == "evil"


# ======================================================================
# Ravenkeeper triggers on night death
# ======================================================================

class TestRavenkeeperNightDeath:

    async def test_ravenkeeper_triggers_on_death(self, mock_manager):
        game = make_game({"a": "imp", "b": "ravenkeeper", "c": "chef", "d": "empath", "e": "poisoner"})
        game._night_deaths = ["b"]

        with patch.object(game, "_action_ravenkeeper") as mock_rk:
            await game._resolve_night_deaths()
            mock_rk.assert_called_once_with("b")

    async def test_poisoned_ravenkeeper_no_trigger(self, mock_manager):
        game = make_game({"a": "imp", "b": "ravenkeeper", "c": "chef", "d": "empath", "e": "poisoner"})
        game.players["b"].poisoned = True
        game._night_deaths = ["b"]

        with patch.object(game, "_action_ravenkeeper") as mock_rk:
            await game._resolve_night_deaths()
            mock_rk.assert_not_called()

    async def test_drunk_ravenkeeper_no_trigger(self, mock_manager):
        game = make_game({"a": "imp", "b": "ravenkeeper", "c": "chef", "d": "empath", "e": "poisoner"})
        game.players["b"].drunk = True
        game._night_deaths = ["b"]

        with patch.object(game, "_action_ravenkeeper") as mock_rk:
            await game._resolve_night_deaths()
            mock_rk.assert_not_called()

    async def test_ravenkeeper_alive_no_trigger(self, mock_manager):
        """Ravenkeeper not in _night_deaths → no trigger."""
        game = make_game({"a": "imp", "b": "ravenkeeper", "c": "chef", "d": "empath", "e": "poisoner"})
        game._night_deaths = ["c"]

        with patch.object(game, "_action_ravenkeeper") as mock_rk:
            await game._resolve_night_deaths()
            mock_rk.assert_not_called()


# ======================================================================
# Helper: alive_count, alive_neighbours
# ======================================================================

class TestHelpers:

    def test_alive_count(self):
        game = make_game({"a": "imp", "b": "washerwoman", "c": "chef", "d": "empath", "e": "poisoner"})
        assert game.alive_count() == 5
        kill(game, "b")
        assert game.alive_count() == 4
        kill(game, "c")
        assert game.alive_count() == 3

    def test_alive_neighbours_basic(self):
        game = make_game({"a": "imp", "b": "washerwoman", "c": "chef", "d": "empath", "e": "poisoner"})
        left, right = game._alive_neighbours("c")
        assert left.player_id == "b"
        assert right.player_id == "d"

    def test_alive_neighbours_skip_dead(self):
        game = make_game({"a": "imp", "b": "washerwoman", "c": "chef", "d": "empath", "e": "poisoner"})
        kill(game, "b")
        left, right = game._alive_neighbours("c")
        assert left.player_id == "a"
        assert right.player_id == "d"

    def test_alive_neighbours_wrap(self):
        game = make_game({"a": "imp", "b": "washerwoman", "c": "chef", "d": "empath", "e": "poisoner"})
        left, right = game._alive_neighbours("a")
        assert left.player_id == "e"
        assert right.player_id == "b"

    def test_player_count(self):
        game = make_game({"a": "imp", "b": "washerwoman", "c": "chef"})
        assert game.player_count() == 3
