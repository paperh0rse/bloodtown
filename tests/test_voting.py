"""Tests for voting mechanics, butler restriction, and dead player votes."""

from __future__ import annotations

import pytest

from server.models import DaySubPhase, GamePhase
from tests.conftest import make_game, kill


def setup_voting(game):
    """Put game into VOTING state with empty votes."""
    game.phase = GamePhase.DAY
    game.day_sub = DaySubPhase.VOTING
    game._vote_eligible_count = sum(
        1 for p in game.players.values() if p.alive or p.has_vote_token
    )
    game._votes = {}
    game._butler_blocked = set()


# ======================================================================
# Basic voting
# ======================================================================

class TestBasicVoting:

    async def test_alive_player_votes_yes(self, mock_manager):
        game = make_game({"a": "washerwoman", "b": "chef", "c": "imp", "d": "empath", "e": "poisoner"})
        setup_voting(game)

        await game.handle_vote("b", True)

        assert game._votes["b"] is True

    async def test_alive_player_votes_no(self, mock_manager):
        game = make_game({"a": "washerwoman", "b": "chef", "c": "imp", "d": "empath", "e": "poisoner"})
        setup_voting(game)

        await game.handle_vote("b", False)

        assert game._votes["b"] is False

    async def test_cannot_vote_twice(self, mock_manager):
        game = make_game({"a": "washerwoman", "b": "chef", "c": "imp", "d": "empath", "e": "poisoner"})
        setup_voting(game)

        await game.handle_vote("b", True)
        await game.handle_vote("b", False)  # should be ignored

        assert game._votes["b"] is True

    async def test_wrong_phase_ignored(self, mock_manager):
        game = make_game({"a": "washerwoman", "b": "chef", "c": "imp", "d": "empath", "e": "poisoner"})
        game.phase = GamePhase.NIGHT
        game.day_sub = DaySubPhase.VOTING
        game._votes = {}

        await game.handle_vote("b", True)

        assert "b" not in game._votes

    async def test_wrong_subphase_ignored(self, mock_manager):
        game = make_game({"a": "washerwoman", "b": "chef", "c": "imp", "d": "empath", "e": "poisoner"})
        game.phase = GamePhase.DAY
        game.day_sub = DaySubPhase.NOMINATION
        game._votes = {}

        await game.handle_vote("b", True)

        assert "b" not in game._votes


# ======================================================================
# Nomination (each player can be nominated at most once per day)
# ======================================================================

class TestNominationOncePerPlayer:

    async def test_nominate_rejects_already_nominated_player(self, mock_manager):
        """每轮同一玩家只能被提名一次；已被提名过的玩家再次被提名时返回错误。"""
        game = make_game({"a": "washerwoman", "b": "chef", "c": "imp", "d": "empath", "e": "poisoner"})
        game.phase = GamePhase.DAY
        game.day_sub = DaySubPhase.NOMINATION
        game._nominated_today = {"b"}
        game._nominations_remaining = 5

        await game.handle_nominate("a", "b")

        errors = [m for m in mock_manager.messages if m[2] == "error" and m[1] == "a"]
        assert len(errors) == 1
        assert "已被提名过" in errors[0][3].get("message", "")
        assert "a" not in game._nominators_today
        assert "b" in game._nominated_today


# ======================================================================
# Dead player voting
# ======================================================================

class TestDeadVoting:

    async def test_dead_with_token_can_vote_yes(self, mock_manager):
        game = make_game({"a": "washerwoman", "b": "chef", "c": "imp", "d": "empath", "e": "poisoner"})
        kill(game, "b")
        game.players["b"].has_vote_token = True
        setup_voting(game)

        await game.handle_vote("b", True)

        assert game._votes["b"] is True
        assert game.players["b"].has_vote_token is False  # consumed

    async def test_dead_abstain_keeps_token(self, mock_manager):
        """Dead player voting 'no' (abstain) does NOT consume their token."""
        game = make_game({"a": "washerwoman", "b": "chef", "c": "imp", "d": "empath", "e": "poisoner"})
        kill(game, "b")
        game.players["b"].has_vote_token = True
        setup_voting(game)

        await game.handle_vote("b", False)

        assert game._votes["b"] is False
        assert game.players["b"].has_vote_token is True  # NOT consumed

    async def test_dead_without_token_cannot_vote(self, mock_manager):
        game = make_game({"a": "washerwoman", "b": "chef", "c": "imp", "d": "empath", "e": "poisoner"})
        kill(game, "b")
        game.players["b"].has_vote_token = False
        setup_voting(game)

        await game.handle_vote("b", True)

        assert "b" not in game._votes


# ======================================================================
# Butler restriction
# ======================================================================

class TestButlerVoting:

    async def test_butler_blocked_while_waiting(self, mock_manager):
        """Butler in _butler_blocked set cannot vote."""
        game = make_game({"a": "washerwoman", "b": "butler", "c": "imp", "d": "empath", "e": "poisoner"})
        game.players["b"].butler_master_id = "c"
        setup_voting(game)
        game._butler_blocked = {"b"}

        await game.handle_vote("b", True)
        assert "b" not in game._votes

    async def test_butler_yes_rejected_when_master_no(self, mock_manager):
        """Butler votes yes after master voted no → rejected."""
        game = make_game({"a": "washerwoman", "b": "butler", "c": "imp", "d": "empath", "e": "poisoner"})
        game.players["b"].butler_master_id = "c"
        setup_voting(game)

        await game.handle_vote("c", False)  # master votes no
        await game.handle_vote("b", True)   # butler tries yes
        assert "b" not in game._votes       # rejected

    async def test_butler_yes_allowed_when_master_yes(self, mock_manager):
        """Butler votes yes after master voted yes → allowed."""
        game = make_game({"a": "washerwoman", "b": "butler", "c": "imp", "d": "empath", "e": "poisoner"})
        game.players["b"].butler_master_id = "c"
        setup_voting(game)

        await game.handle_vote("c", True)  # master votes yes
        await game.handle_vote("b", True)  # butler votes yes
        assert game._votes["b"] is True

    async def test_butler_can_vote_no_anytime(self, mock_manager):
        game = make_game({"a": "washerwoman", "b": "butler", "c": "imp", "d": "empath", "e": "poisoner"})
        game.players["b"].butler_master_id = "c"
        setup_voting(game)

        await game.handle_vote("b", False)
        assert game._votes["b"] is False

    async def test_butler_unlock_sends_message(self, mock_manager):
        """When master votes, butler is unblocked and notified."""
        game = make_game({"a": "washerwoman", "b": "butler", "c": "imp", "d": "empath", "e": "poisoner"})
        game.players["b"].butler_master_id = "c"
        setup_voting(game)
        game._butler_blocked = {"b"}

        await game.handle_vote("c", True)  # master votes
        assert "b" not in game._butler_blocked
        unlocked = [m for m in mock_manager.messages if m[2] == "butler_unlocked"]
        assert len(unlocked) >= 1


# ======================================================================
# Vote tally threshold
# ======================================================================

class TestVoteTally:

    def test_needed_votes_odd_count(self):
        """5 alive → need ceil(5/2) = 3."""
        game = make_game({"a": "imp", "b": "poisoner", "c": "washerwoman", "d": "chef", "e": "empath"})
        needed = (game.alive_count() + 1) // 2
        assert needed == 3

    def test_needed_votes_even_count(self):
        """4 alive → need ceil(4/2) = 2."""
        game = make_game({"a": "imp", "b": "poisoner", "c": "washerwoman", "d": "chef"})
        needed = (game.alive_count() + 1) // 2
        assert needed == 2

    def test_needed_votes_three(self):
        """3 alive → need ceil(3/2) = 2."""
        game = make_game({"a": "imp", "b": "poisoner", "c": "washerwoman"})
        needed = (game.alive_count() + 1) // 2
        assert needed == 2


# ======================================================================
# Restart vote
# ======================================================================

class TestRestartVote:

    async def test_propose_restart_sets_state(self, mock_manager):
        game = make_game({"a": "imp", "b": "poisoner", "c": "washerwoman", "d": "chef", "e": "empath"})
        game.phase = GamePhase.DAY

        await game.handle_propose_restart("c")

        assert game._restart_proposer == "c"
        assert game._restart_votes["c"] is True
        state = game.public_state()
        assert state["restart_vote"] is not None
        assert state["restart_vote"]["proposer_id"] == "c"

    async def test_propose_restart_ignored_in_lobby(self, mock_manager):
        game = make_game({"a": "imp", "b": "poisoner", "c": "washerwoman"})
        game.phase = GamePhase.LOBBY

        await game.handle_propose_restart("c")

        assert game._restart_proposer == ""

    async def test_all_agree_triggers_restart(self, mock_manager):
        game = make_game({"a": "imp", "b": "poisoner", "c": "washerwoman"})
        game.phase = GamePhase.DAY

        await game.handle_propose_restart("a")
        await game.handle_vote_restart("b", True)
        await game.handle_vote_restart("c", True)

        assert game.phase == GamePhase.LOBBY
        restarted = [b for b in mock_manager.broadcasts if b[1] == "game_restarted"]
        assert len(restarted) == 1

    async def test_one_reject_cancels(self, mock_manager):
        game = make_game({"a": "imp", "b": "poisoner", "c": "washerwoman", "d": "chef"})
        game.phase = GamePhase.DAY

        await game.handle_propose_restart("a")
        await game.handle_vote_restart("b", False)

        assert game._restart_proposer == ""
        assert game._restart_votes == {}
        rejected = [b for b in mock_manager.broadcasts if b[1] == "restart_rejected"]
        assert len(rejected) == 1

    async def test_duplicate_propose_blocked(self, mock_manager):
        game = make_game({"a": "imp", "b": "poisoner", "c": "washerwoman"})
        game.phase = GamePhase.DAY

        await game.handle_propose_restart("a")
        await game.handle_propose_restart("b")

        errors = [m for m in mock_manager.messages if m[2] == "error"]
        assert len(errors) == 1
        assert game._restart_proposer == "a"

    async def test_bots_auto_agree(self, mock_manager):
        game = make_game({"a": "imp", "b": "poisoner", "c": "washerwoman"})
        game.players["b"].is_bot = True
        game.phase = GamePhase.DAY

        await game.handle_propose_restart("a")

        assert game._restart_votes.get("b") is True

    async def test_duplicate_vote_ignored(self, mock_manager):
        game = make_game({"a": "imp", "b": "poisoner", "c": "washerwoman", "d": "chef"})
        game.phase = GamePhase.DAY

        await game.handle_propose_restart("a")
        await game.handle_vote_restart("b", True)
        await game.handle_vote_restart("b", False)  # should be ignored

        assert game._restart_votes["b"] is True
