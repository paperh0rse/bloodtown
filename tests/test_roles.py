"""Tests for individual role abilities."""

from __future__ import annotations

import random
from unittest.mock import patch

import pytest

from server.models import Alignment, GamePhase
from tests.conftest import make_game, kill, poison, protect


# ======================================================================
# Slayer
# ======================================================================

class TestSlayer:

    async def test_slayer_kills_demon(self, mock_manager):
        game = make_game({"a": "slayer", "b": "imp", "c": "poisoner", "d": "chef", "e": "empath"})
        game.phase = GamePhase.DAY

        await game.handle_slayer("a", "b")

        assert not game.players["b"].alive
        assert game.phase == GamePhase.GAME_OVER
        wins = [b for b in mock_manager.broadcasts if b[1] == "game_over"]
        assert wins[0][2]["winner"] == "good"

    async def test_slayer_misses_non_demon(self, mock_manager):
        game = make_game({"a": "slayer", "b": "imp", "c": "poisoner", "d": "chef", "e": "empath"})
        game.phase = GamePhase.DAY

        await game.handle_slayer("a", "c")

        assert game.players["c"].alive
        assert game.phase != GamePhase.GAME_OVER
        fails = [b for b in mock_manager.broadcasts if b[1] == "slayer_fail"]
        assert len(fails) == 1

    async def test_slayer_one_shot(self, mock_manager):
        game = make_game({"a": "slayer", "b": "imp", "c": "poisoner", "d": "chef", "e": "empath"})
        game.phase = GamePhase.DAY

        await game.handle_slayer("a", "c")  # miss
        assert game.players["a"].used_ability is True

        await game.handle_slayer("a", "b")  # second shot doesn't kill
        assert game.players["b"].alive

    async def test_poisoned_slayer_cant_kill(self, mock_manager):
        game = make_game({"a": "slayer", "b": "imp", "c": "poisoner", "d": "chef", "e": "empath"})
        game.phase = GamePhase.DAY
        poison(game, "a")

        await game.handle_slayer("a", "b")

        assert game.players["b"].alive
        assert game.phase != GamePhase.GAME_OVER

    async def test_drunk_slayer_cant_kill(self, mock_manager):
        game = make_game({"a": "slayer", "b": "imp", "c": "poisoner", "d": "chef", "e": "empath"})
        game.phase = GamePhase.DAY
        game.players["a"].drunk = True

        await game.handle_slayer("a", "b")

        assert game.players["b"].alive

    async def test_fake_slayer_claim_does_nothing(self, mock_manager):
        """Non-slayer claiming slayer does nothing."""
        game = make_game({"a": "chef", "b": "imp", "c": "poisoner", "d": "empath", "e": "washerwoman"})
        game.phase = GamePhase.DAY

        await game.handle_slayer("a", "b")

        assert game.players["b"].alive
        fails = [b for b in mock_manager.broadcasts if b[1] == "slayer_fail"]
        assert len(fails) == 1

    async def test_slayer_dead_cant_shoot(self, mock_manager):
        game = make_game({"a": "slayer", "b": "imp", "c": "poisoner", "d": "chef", "e": "empath"})
        game.phase = GamePhase.DAY
        kill(game, "a")

        await game.handle_slayer("a", "b")
        assert game.players["b"].alive

    async def test_slayer_not_during_night(self, mock_manager):
        game = make_game({"a": "slayer", "b": "imp", "c": "poisoner", "d": "chef", "e": "empath"})
        game.phase = GamePhase.NIGHT

        await game.handle_slayer("a", "b")
        assert game.players["b"].alive

    async def test_slayer_kills_demon_scarlet_takes_over(self, mock_manager):
        """Scarlet Woman becomes Imp when slayer kills demon with 5+ alive."""
        game = make_game({
            "a": "slayer", "b": "imp", "c": "scarlet_woman",
            "d": "chef", "e": "empath", "f": "soldier",
        })
        game.phase = GamePhase.DAY

        await game.handle_slayer("a", "b")

        assert not game.players["b"].alive
        assert game.players["c"].role_id == "imp"
        assert game.phase != GamePhase.GAME_OVER

    async def test_slayer_kills_demon_scarlet_too_few(self, mock_manager):
        """Scarlet Woman can't take over with <5 alive — good wins."""
        game = make_game({
            "a": "slayer", "b": "imp", "c": "scarlet_woman", "d": "chef",
        })
        kill(game, "d")
        game.phase = GamePhase.DAY

        await game.handle_slayer("a", "b")

        assert not game.players["b"].alive
        assert game.phase == GamePhase.GAME_OVER
        wins = [b for b in mock_manager.broadcasts if b[1] == "game_over"]
        assert wins[0][2]["winner"] == "good"

    async def test_slayer_kills_demon_scarlet_dead(self, mock_manager):
        """Dead Scarlet Woman can't take over — good wins."""
        game = make_game({
            "a": "slayer", "b": "imp", "c": "scarlet_woman",
            "d": "chef", "e": "empath",
        })
        kill(game, "c")
        game.phase = GamePhase.DAY

        await game.handle_slayer("a", "b")

        assert not game.players["b"].alive
        assert game.phase == GamePhase.GAME_OVER
        wins = [b for b in mock_manager.broadcasts if b[1] == "game_over"]
        assert wins[0][2]["winner"] == "good"


# ======================================================================
# Imp (night kill)
# ======================================================================

class TestImpNightKill:

    async def test_imp_kills_normal_target(self, mock_manager):
        game = make_game({"a": "imp", "b": "washerwoman", "c": "chef", "d": "empath", "e": "poisoner"})
        game._night_deaths = []

        with patch.object(game, "_ask_player_choose", return_value="b"):
            await game._action_imp("a")

        assert "b" in game._night_deaths

    async def test_imp_soldier_immune(self, mock_manager):
        game = make_game({"a": "imp", "b": "soldier", "c": "chef", "d": "empath", "e": "poisoner"})
        game._night_deaths = []

        with patch.object(game, "_ask_player_choose", return_value="b"):
            await game._action_imp("a")

        assert "b" not in game._night_deaths

    async def test_imp_poisoned_soldier_dies(self, mock_manager):
        game = make_game({"a": "imp", "b": "soldier", "c": "chef", "d": "empath", "e": "poisoner"})
        game._night_deaths = []
        poison(game, "b")

        with patch.object(game, "_ask_player_choose", return_value="b"):
            await game._action_imp("a")

        assert "b" in game._night_deaths

    async def test_imp_monk_protection(self, mock_manager):
        game = make_game({"a": "imp", "b": "washerwoman", "c": "monk", "d": "empath", "e": "poisoner"})
        game._night_deaths = []
        protect(game, "b")

        with patch.object(game, "_ask_player_choose", return_value="b"):
            await game._action_imp("a")

        assert "b" not in game._night_deaths

    async def test_imp_mayor_redirect(self, mock_manager):
        """Imp attacks Mayor → death redirects to another player."""
        game = make_game({"a": "imp", "b": "mayor", "c": "chef", "d": "empath", "e": "poisoner"})
        game._night_deaths = []

        with patch.object(game, "_ask_player_choose", return_value="b"):
            with patch("random.choice", return_value=game.players["c"]):
                await game._action_imp("a")

        assert "b" not in game._night_deaths
        assert "c" in game._night_deaths

    async def test_imp_poisoned_mayor_dies(self, mock_manager):
        game = make_game({"a": "imp", "b": "mayor", "c": "chef", "d": "empath", "e": "poisoner"})
        game._night_deaths = []
        poison(game, "b")

        with patch.object(game, "_ask_player_choose", return_value="b"):
            await game._action_imp("a")

        assert "b" in game._night_deaths

    async def test_imp_self_kill_minion_becomes_imp(self, mock_manager):
        """Imp kills self → a living minion becomes the new Imp."""
        game = make_game({"a": "imp", "b": "poisoner", "c": "washerwoman", "d": "chef", "e": "empath"})
        game._night_deaths = []

        with patch.object(game, "_ask_player_choose", return_value="a"):
            with patch("random.choice", return_value=game.players["b"]):
                await game._action_imp("a")

        assert "a" in game._night_deaths
        assert game.players["b"].role_id == "imp"

    async def test_imp_dead_cant_act(self, mock_manager):
        game = make_game({"a": "imp", "b": "washerwoman", "c": "chef", "d": "empath", "e": "poisoner"})
        game._night_deaths = []
        kill(game, "a")

        with patch.object(game, "_ask_player_choose", return_value="b"):
            await game._action_imp("a")

        assert "b" not in game._night_deaths


# ======================================================================
# Monk
# ======================================================================

class TestMonk:

    async def test_monk_protects_target(self, mock_manager):
        game = make_game({"a": "monk", "b": "washerwoman", "c": "imp", "d": "empath", "e": "poisoner"})

        with patch.object(game, "_ask_player_choose", return_value="b"):
            await game._action_monk("a")

        assert game.players["b"].protected is True

    async def test_poisoned_monk_no_protection(self, mock_manager):
        """Poisoned monk early-returns without asking for a target."""
        game = make_game({"a": "monk", "b": "washerwoman", "c": "imp", "d": "empath", "e": "poisoner"})
        poison(game, "a")

        with patch.object(game, "_ask_player_choose", return_value="b") as mock_ask:
            await game._action_monk("a")
            mock_ask.assert_not_called()

        assert game.players["b"].protected is False


# ======================================================================
# Empath
# ======================================================================

class TestEmpath:

    async def test_empath_detects_evil_neighbours(self, mock_manager):
        """Empath between two evil players → count = 2."""
        game = make_game({"a": "poisoner", "b": "empath", "c": "imp"})

        await game._action_empath("b")

        msgs = [m for m in mock_manager.messages if m[2] == "night_info"]
        assert len(msgs) == 1
        assert msgs[0][3]["count"] == 2

    async def test_empath_no_evil_neighbours(self, mock_manager):
        game = make_game({"a": "washerwoman", "b": "empath", "c": "chef"})

        await game._action_empath("b")

        msgs = [m for m in mock_manager.messages if m[2] == "night_info"]
        assert msgs[0][3]["count"] == 0

    async def test_empath_skips_dead_neighbours(self, mock_manager):
        """Dead evil neighbour is skipped; next alive neighbour is checked."""
        game = make_game({"a": "poisoner", "b": "empath", "c": "washerwoman", "d": "chef"})
        kill(game, "a")  # dead evil neighbour

        await game._action_empath("b")

        msgs = [m for m in mock_manager.messages if m[2] == "night_info"]
        assert msgs[0][3]["count"] == 0


# ======================================================================
# Chef
# ======================================================================

class TestChef:

    async def test_chef_adjacent_evil_pair(self, mock_manager):
        """Two evil players sitting next to each other → count = 1."""
        game = make_game({"a": "imp", "b": "poisoner", "c": "washerwoman", "d": "chef", "e": "empath"})
        # a(evil) - b(evil) are adjacent → 1 pair

        with patch.object(game, "_registers_as_evil", side_effect=lambda p: p.alignment.value == "evil"):
            await game._action_chef("d")

        msgs = [m for m in mock_manager.messages if m[2] == "night_info"]
        assert msgs[0][3]["count"] == 1

    async def test_chef_no_adjacent_evil(self, mock_manager):
        """Evil players not adjacent → count = 0."""
        game = make_game({"a": "imp", "b": "washerwoman", "c": "poisoner", "d": "chef", "e": "empath"})
        # a(evil), c(evil) separated by b(good)

        with patch.object(game, "_registers_as_evil", side_effect=lambda p: p.alignment.value == "evil"):
            await game._action_chef("d")

        msgs = [m for m in mock_manager.messages if m[2] == "night_info"]
        assert msgs[0][3]["count"] == 0

    async def test_chef_wrap_around(self, mock_manager):
        """Evil pair wrapping around the circle (last + first seat)."""
        game = make_game({"a": "imp", "b": "washerwoman", "c": "chef", "d": "empath", "e": "poisoner"})
        # seat order: a(evil), b, c, d, e(evil) → e-a wraps → 1 pair

        with patch.object(game, "_registers_as_evil", side_effect=lambda p: p.alignment.value == "evil"):
            await game._action_chef("c")

        msgs = [m for m in mock_manager.messages if m[2] == "night_info"]
        assert msgs[0][3]["count"] == 1


# ======================================================================
# Fortune Teller
# ======================================================================

class TestFortuneTeller:

    async def test_ft_detects_demon(self, mock_manager):
        game = make_game({"a": "fortune_teller", "b": "imp", "c": "poisoner", "d": "chef", "e": "empath"})

        with patch.object(game, "_ask_player_choose_two", return_value=["b", "c"]):
            await game._action_fortune_teller("a")

        msgs = [m for m in mock_manager.messages if m[2] == "night_info"]
        assert msgs[0][3]["has_demon"] is True

    async def test_ft_no_demon(self, mock_manager):
        game = make_game({"a": "fortune_teller", "b": "imp", "c": "poisoner", "d": "chef", "e": "empath"})

        with patch.object(game, "_ask_player_choose_two", return_value=["c", "d"]):
            await game._action_fortune_teller("a")

        msgs = [m for m in mock_manager.messages if m[2] == "night_info"]
        assert msgs[0][3]["has_demon"] is False

    async def test_ft_red_herring_triggers(self, mock_manager):
        game = make_game({"a": "fortune_teller", "b": "imp", "c": "washerwoman", "d": "chef", "e": "empath"})
        game.players["a"].fortune_teller_red_herring = "c"

        with patch.object(game, "_ask_player_choose_two", return_value=["c", "d"]):
            await game._action_fortune_teller("a")

        msgs = [m for m in mock_manager.messages if m[2] == "night_info"]
        assert msgs[0][3]["has_demon"] is True

    async def test_ft_poisoned_random_result(self, mock_manager):
        game = make_game({"a": "fortune_teller", "b": "imp", "c": "poisoner", "d": "chef", "e": "empath"})
        poison(game, "a")

        with patch.object(game, "_ask_player_choose_two", return_value=["c", "d"]):
            with patch("random.choice", return_value=False):
                await game._action_fortune_teller("a")

        msgs = [m for m in mock_manager.messages if m[2] == "night_info"]
        assert msgs[0][3]["has_demon"] is False


# ======================================================================
# Poisoner
# ======================================================================

class TestPoisoner:

    async def test_poisoner_poisons_target(self, mock_manager):
        game = make_game({"a": "poisoner", "b": "washerwoman", "c": "imp", "d": "chef", "e": "empath"})

        with patch.object(game, "_ask_player_choose", return_value="b"):
            await game._action_poisoner("a", first_night=True)

        assert game.players["b"].poisoned is True

    async def test_poisoner_no_target(self, mock_manager):
        game = make_game({"a": "poisoner", "b": "washerwoman", "c": "imp", "d": "chef", "e": "empath"})

        with patch.object(game, "_ask_player_choose", return_value=None):
            await game._action_poisoner("a", first_night=True)

        assert not any(p.poisoned for p in game.players.values())


# ======================================================================
# Butler
# ======================================================================

class TestButler:

    async def test_butler_sets_master(self, mock_manager):
        game = make_game({"a": "butler", "b": "washerwoman", "c": "imp", "d": "chef", "e": "empath"})

        with patch.object(game, "_ask_player_choose", return_value="b"):
            await game._action_butler("a")

        assert game.players["a"].butler_master_id == "b"
