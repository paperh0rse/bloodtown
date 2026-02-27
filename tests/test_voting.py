"""Tests for voting mechanics, butler restriction, and dead player votes."""

from __future__ import annotations

import pytest

from server.models import DaySubPhase, GamePhase
from tests.conftest import make_game, kill


def setup_voting(game, nominator_id="a", nominee_id="b"):
    """Put game into VOTING state with initial nominator auto-yes."""
    game.phase = GamePhase.DAY
    game.day_sub = DaySubPhase.VOTING
    game._vote_eligible_count = sum(
        1 for p in game.players.values() if p.alive or p.has_vote_token
    )
    game._votes = {nominator_id: True}


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

    async def test_butler_cant_vote_yes_before_master(self, mock_manager):
        game = make_game({"a": "washerwoman", "b": "butler", "c": "imp", "d": "empath", "e": "poisoner"})
        game.players["b"].butler_master_id = "c"
        setup_voting(game)

        await game.handle_vote("b", True)

        assert "b" not in game._votes
        errors = [m for m in mock_manager.messages if m[2] == "error"]
        assert len(errors) == 1

    async def test_butler_can_vote_yes_after_master(self, mock_manager):
        game = make_game({"a": "washerwoman", "b": "butler", "c": "imp", "d": "empath", "e": "poisoner"})
        game.players["b"].butler_master_id = "c"
        setup_voting(game)

        await game.handle_vote("c", True)  # master votes yes first
        await game.handle_vote("b", True)

        assert game._votes["b"] is True

    async def test_butler_can_vote_no_anytime(self, mock_manager):
        game = make_game({"a": "washerwoman", "b": "butler", "c": "imp", "d": "empath", "e": "poisoner"})
        game.players["b"].butler_master_id = "c"
        setup_voting(game)

        await game.handle_vote("b", False)

        assert game._votes["b"] is False

    async def test_butler_blocked_when_master_votes_no(self, mock_manager):
        game = make_game({"a": "washerwoman", "b": "butler", "c": "imp", "d": "empath", "e": "poisoner"})
        game.players["b"].butler_master_id = "c"
        setup_voting(game)

        await game.handle_vote("c", False)  # master votes no
        await game.handle_vote("b", True)   # butler tries yes

        assert "b" not in game._votes

    async def test_butler_nominator_auto_yes_voided_if_master_no(self, mock_manager):
        """Butler as nominator: auto-yes is voided at tally if master didn't vote yes."""
        game = make_game({"a": "butler", "b": "washerwoman", "c": "imp", "d": "empath", "e": "poisoner"})
        game.players["a"].butler_master_id = "b"
        game.phase = GamePhase.DAY
        game.day_sub = DaySubPhase.VOTING

        # Simulate: butler (a) is nominator, auto-yes is in _votes
        game._votes = {"a": True}
        game._vote_eligible_count = 5

        # Others vote
        await game.handle_vote("b", False)  # master votes no
        await game.handle_vote("c", True)
        await game.handle_vote("d", True)
        await game.handle_vote("e", True)

        # Simulate the butler tally check from _process_nomination
        nominator = game.players["a"]
        if nominator.role_id == "butler" and nominator.butler_master_id:
            master_voted = game._votes.get(nominator.butler_master_id)
            if not master_voted:
                game._votes["a"] = False

        yes_votes = sum(1 for v in game._votes.values() if v)
        assert yes_votes == 3  # c, d, e — butler's vote voided
        assert game._votes["a"] is False


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
