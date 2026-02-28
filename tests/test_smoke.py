"""End-to-end smoke tests exercising full game flows through the engine.

Each test sets up a game with known roles, then drives the game loop
by feeding actions/signals while asserting state transitions, message
delivery, and rule enforcement.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from server.game_engine import Game
from server.models import Alignment, DaySubPhase, GamePhase, Team
from server.role_data import ROLE_BY_ID

from tests.conftest import FakeManager, make_game, kill


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def personal_msgs(mgr: FakeManager, pid: str, msg_type: str) -> list[dict]:
    return [data for _, p, t, data in mgr.messages if p == pid and t == msg_type]


def broadcast_msgs(mgr: FakeManager, msg_type: str) -> list[dict]:
    return [data for _, t, data in mgr.broadcasts if t == msg_type]


_real_sleep = asyncio.sleep


async def instant_sleep(_seconds=0):
    """Replace asyncio.sleep to speed up game loop."""
    await _real_sleep(0)


async def wait_phase(game: Game, phase: GamePhase, limit=300):
    for _ in range(limit):
        await asyncio.sleep(0)
        if game.phase == phase:
            return
        if game.phase == GamePhase.GAME_OVER and phase != GamePhase.GAME_OVER:
            return
    raise TimeoutError(f"Never reached {phase}, stuck at {game.phase}")


async def wait_sub(game: Game, sub: DaySubPhase, limit=300):
    for _ in range(limit):
        await asyncio.sleep(0)
        if game.day_sub == sub:
            return
    raise TimeoutError(f"Never reached sub-phase {sub}, stuck at {game.day_sub}")


def auto_action_feeder(game: Game, default_target: str = "p1"):
    """Background task that auto-responds to any pending night action."""
    async def _feed():
        while True:
            await asyncio.sleep(0)
            if game._pending_action and not game._pending_action.is_set():
                game.submit_action("", {"chosen_id": default_target, "chosen_ids": [default_target, "p2"]})
    return asyncio.create_task(_feed())


async def auto_speeches(game: Game, limit=200):
    """Auto-complete any speech phases."""
    for _ in range(limit):
        await asyncio.sleep(0)
        if game.day_sub in (DaySubPhase.NOMINATOR_SPEECH, DaySubPhase.NOMINEE_SPEECH):
            game.handle_signal("speech_done")
            await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# 1. Full game: good wins by executing demon
# ---------------------------------------------------------------------------

class TestFullGameGoodWins:

    @patch("asyncio.sleep", new=instant_sleep)
    async def test_execute_demon_good_wins(self, mock_manager: FakeManager):
        game = make_game({
            "p1": "washerwoman",
            "p2": "empath",
            "p3": "slayer",
            "p4": "poisoner",
            "p5": "imp",
        })
        game.phase = GamePhase.SETUP
        loop_task = asyncio.create_task(game._game_loop())
        feeder = auto_action_feeder(game)

        await wait_phase(game, GamePhase.DAY)
        feeder.cancel()

        assert game.phase == GamePhase.DAY
        assert game.day_number == 1
        for pid in game.seat_order:
            assert game.players[pid].role_id != ""

        await wait_sub(game, DaySubPhase.NOMINATION)
        await game.handle_nominate("p1", "p5")

        speech_task = asyncio.create_task(auto_speeches(game))
        await wait_sub(game, DaySubPhase.VOTING)
        speech_task.cancel()

        for pid in ["p1", "p2", "p3", "p4", "p5"]:
            await game.handle_vote(pid, True)

        # After vote tallied, game returns to nomination loop; end nominations
        async def end_day():
            for _ in range(300):
                await _real_sleep(0)
                if game.phase == GamePhase.GAME_OVER:
                    return
                if game.day_sub == DaySubPhase.NOMINATION and game._highest_vote[0]:
                    game._pending_nomination = None
                    game.handle_signal("nomination_submitted")
                    return

        await end_day()
        await wait_phase(game, GamePhase.GAME_OVER)
        loop_task.cancel()

        assert game.phase == GamePhase.GAME_OVER
        assert not game.players["p5"].alive
        go = broadcast_msgs(mock_manager, "game_over")
        assert go[-1]["winner"] == "good"


# ---------------------------------------------------------------------------
# 2. Full game: evil wins when alive <= 2
# ---------------------------------------------------------------------------

class TestFullGameEvilWins:

    @patch("asyncio.sleep", new=instant_sleep)
    async def test_evil_wins_by_execution(self, mock_manager: FakeManager):
        game = make_game({
            "p1": "washerwoman",
            "p2": "empath",
            "p3": "slayer",
            "p4": "poisoner",
            "p5": "imp",
        })
        kill(game, "p2")
        kill(game, "p3")
        game.phase = GamePhase.DAY
        game.day_number = 1
        game.day_sub = DaySubPhase.NOMINATION
        game._nominations_remaining = 5

        day_task = asyncio.create_task(game._run_day())
        await wait_sub(game, DaySubPhase.NOMINATION)

        await game.handle_nominate("p4", "p1")

        speech_task = asyncio.create_task(auto_speeches(game))
        await wait_sub(game, DaySubPhase.VOTING)
        speech_task.cancel()

        await game.handle_vote("p4", True)  # nominator votes too
        await game.handle_vote("p1", True)
        await game.handle_vote("p5", True)
        # Dead players abstain
        await game.handle_vote("p2", False)
        await game.handle_vote("p3", False)

        # After vote, end nominations to trigger execution
        async def end_day():
            for _ in range(300):
                await _real_sleep(0)
                if game.phase == GamePhase.GAME_OVER:
                    return
                if game.day_sub == DaySubPhase.NOMINATION:
                    game._pending_nomination = None
                    game.handle_signal("nomination_submitted")
                    return

        await end_day()
        await wait_phase(game, GamePhase.GAME_OVER)
        day_task.cancel()

        assert game.phase == GamePhase.GAME_OVER
        go = broadcast_msgs(mock_manager, "game_over")
        assert go[-1]["winner"] == "evil"


# ---------------------------------------------------------------------------
# 3. Butler voting rules
# ---------------------------------------------------------------------------

class TestButlerVoting:

    @patch("asyncio.sleep", new=instant_sleep)
    async def test_butler_nominator_auto_vote_voided(self, mock_manager: FakeManager):
        """Butler nominates, master votes no → butler's auto-yes is voided."""
        game = make_game({
            "p1": "washerwoman",
            "p2": "empath",
            "p3": "fortune_teller",
            "p4": "slayer",
            "p5": "butler",
            "p6": "imp",
        })
        game.players["p5"].butler_master_id = "p4"
        kill(game, "p1")
        kill(game, "p3")
        game.phase = GamePhase.DAY
        game.day_number = 1
        game.day_sub = DaySubPhase.NOMINATION
        game._nominations_remaining = 6

        day_task = asyncio.create_task(game._run_day())
        await wait_sub(game, DaySubPhase.NOMINATION)

        await game.handle_nominate("p5", "p2")

        speech_task = asyncio.create_task(auto_speeches(game))
        await wait_sub(game, DaySubPhase.VOTING)
        speech_task.cancel()

        assert "p5" not in game._votes  # nominator no longer auto-votes

        await game.handle_vote("p4", False)   # master votes no
        # Butler p5 is now unblocked; voting yes is rejected (master voted no)
        await game.handle_vote("p5", True)
        assert "p5" not in game._votes  # rejected
        await game.handle_vote("p5", False)  # butler votes no instead
        await game.handle_vote("p2", True)
        await game.handle_vote("p6", True)
        await game.handle_vote("p1", False)
        await game.handle_vote("p3", False)

        await wait_sub(game, DaySubPhase.NOMINATION)

        records = game._nomination_records
        assert len(records) >= 1
        last = records[-1]
        assert last["yes_votes"] == 2  # only p2 and p6

        game._pending_nomination = None
        game.handle_signal("nomination_submitted")
        day_task.cancel()

    async def test_butler_blocked_until_master_votes(self, mock_manager: FakeManager):
        """Butler cannot vote until master has voted."""
        game = make_game({
            "p1": "washerwoman",
            "p2": "butler",
            "p3": "empath",
            "p4": "poisoner",
            "p5": "imp",
        })
        game.players["p2"].butler_master_id = "p3"
        game.phase = GamePhase.DAY
        game.day_sub = DaySubPhase.VOTING
        game._vote_eligible_count = 5
        game._votes = {}
        game._butler_blocked = {"p2"}

        # Butler tries to vote while blocked
        await game.handle_vote("p2", True)
        assert "p2" not in game._votes  # blocked

    async def test_dead_butler_not_restricted(self, mock_manager: FakeManager):
        game = make_game({
            "p1": "washerwoman",
            "p2": "butler",
            "p3": "empath",
            "p4": "poisoner",
            "p5": "imp",
        })
        game.players["p2"].butler_master_id = "p3"
        kill(game, "p2")
        game.phase = GamePhase.DAY
        game.day_sub = DaySubPhase.VOTING
        game._vote_eligible_count = 5
        game._votes = {"p1": True}

        await game.handle_vote("p2", True)
        assert game._votes.get("p2") is True
        assert not game.players["p2"].has_vote_token


# ---------------------------------------------------------------------------
# 4. Imp self-kill (starpass) — no double kill
# ---------------------------------------------------------------------------

class TestImpStarpass:

    async def test_starpass_no_double_kill(self, mock_manager: FakeManager):
        game = make_game({
            "p1": "washerwoman",
            "p2": "empath",
            "p3": "fortune_teller",
            "p4": "monk",
            "p5": "poisoner",
            "p6": "imp",
        })
        game.phase = GamePhase.NIGHT
        game._night_deaths = []
        for p in game.players.values():
            p.poisoned = False
            p.protected = False

        action_log = []

        async def mock_ask(pid, action_type, prompt, options):
            action_log.append((pid, action_type))
            if pid == "p5" and action_type == "poison_target":
                return "p1"
            if pid == "p4" and action_type == "monk_protect":
                return "p2"
            if pid == "p6" and action_type == "imp_kill":
                return "p6"  # self-kill
            return options[0]["id"] if options else None

        with patch.object(game, "_ask_player_choose", side_effect=mock_ask):
            with patch.object(game, "_ask_player_choose_two", return_value=["p1", "p2"]):
                await game._process_night_actions(first_night=False)

        assert not game.players["p6"].alive
        assert game.players["p5"].role_id == "imp"
        assert game._night_deaths == ["p6"]

        # p5 should only have acted once as poisoner, NOT as imp
        p5_actions = [at for pid, at in action_log if pid == "p5"]
        assert p5_actions == ["poison_target"]

    async def test_starpass_became_demon_message(self, mock_manager: FakeManager):
        game = make_game({
            "p1": "washerwoman",
            "p2": "empath",
            "p3": "fortune_teller",
            "p4": "monk",
            "p5": "poisoner",
            "p6": "imp",
        })
        game.phase = GamePhase.NIGHT
        game._night_deaths = []
        for p in game.players.values():
            p.poisoned = False
            p.protected = False

        async def mock_ask(pid, action_type, prompt, options):
            if pid == "p5":
                return "p1"
            if pid == "p4":
                return "p2"
            if pid == "p6":
                return "p6"
            return options[0]["id"]

        with patch.object(game, "_ask_player_choose", side_effect=mock_ask):
            with patch.object(game, "_ask_player_choose_two", return_value=["p1", "p2"]):
                await game._process_night_actions(first_night=False)

        became = [d for d in personal_msgs(mock_manager, "p5", "night_info")
                  if d.get("info_type") == "became_demon"]
        assert len(became) == 1


# ---------------------------------------------------------------------------
# 5. Dead players should NOT receive night info
# ---------------------------------------------------------------------------

class TestDeadPlayersNoNightInfo:

    async def test_dead_fortune_teller_no_action(self, mock_manager: FakeManager):
        game = make_game({
            "p1": "fortune_teller",
            "p2": "empath",
            "p3": "washerwoman",
            "p4": "poisoner",
            "p5": "imp",
        })
        kill(game, "p1")
        game.phase = GamePhase.NIGHT
        game._night_deaths = []
        for p in game.players.values():
            p.poisoned = False
            p.protected = False

        asked_players = []

        async def mock_ask(pid, action_type, prompt, options):
            asked_players.append(pid)
            if pid == "p4":
                return "p2"
            if pid == "p5":
                return "p2"
            return options[0]["id"]

        with patch.object(game, "_ask_player_choose", side_effect=mock_ask):
            await game._process_night_actions(first_night=False)

        assert "p1" not in asked_players
        assert len(personal_msgs(mock_manager, "p1", "night_info")) == 0

    async def test_dead_empath_no_info(self, mock_manager: FakeManager):
        game = make_game({
            "p1": "empath",
            "p2": "washerwoman",
            "p3": "slayer",
            "p4": "poisoner",
            "p5": "imp",
        })
        kill(game, "p1")
        game.phase = GamePhase.NIGHT
        game._night_deaths = []
        for p in game.players.values():
            p.poisoned = False
            p.protected = False

        async def mock_ask(pid, action_type, prompt, options):
            if pid == "p4":
                return "p2"
            if pid == "p5":
                return "p3"
            return options[0]["id"]

        with patch.object(game, "_ask_player_choose", side_effect=mock_ask):
            await game._process_night_actions(first_night=False)

        assert len(personal_msgs(mock_manager, "p1", "night_info")) == 0

    async def test_dead_butler_no_master_selection(self, mock_manager: FakeManager):
        game = make_game({
            "p1": "butler",
            "p2": "empath",
            "p3": "washerwoman",
            "p4": "poisoner",
            "p5": "imp",
        })
        kill(game, "p1")
        game.phase = GamePhase.NIGHT
        game._night_deaths = []
        for p in game.players.values():
            p.poisoned = False
            p.protected = False

        asked_players = []

        async def mock_ask(pid, action_type, prompt, options):
            asked_players.append(pid)
            if pid == "p4":
                return "p2"
            if pid == "p5":
                return "p3"
            return options[0]["id"]

        with patch.object(game, "_ask_player_choose", side_effect=mock_ask):
            await game._process_night_actions(first_night=False)

        assert "p1" not in asked_players


# ---------------------------------------------------------------------------
# 6. Dead player vote token consumption
# ---------------------------------------------------------------------------

class TestDeadPlayerVoteToken:

    async def test_dead_yes_consumes_token(self, mock_manager: FakeManager):
        game = make_game({"p1": "washerwoman", "p2": "empath", "p3": "poisoner", "p4": "imp"})
        kill(game, "p1")
        game.phase = GamePhase.DAY
        game.day_sub = DaySubPhase.VOTING
        game._vote_eligible_count = 4
        game._votes = {}

        await game.handle_vote("p1", True)
        assert game._votes["p1"] is True
        assert game.players["p1"].has_vote_token is False

    async def test_dead_no_preserves_token(self, mock_manager: FakeManager):
        game = make_game({"p1": "washerwoman", "p2": "empath", "p3": "poisoner", "p4": "imp"})
        kill(game, "p1")
        game.phase = GamePhase.DAY
        game.day_sub = DaySubPhase.VOTING
        game._vote_eligible_count = 4
        game._votes = {}

        await game.handle_vote("p1", False)
        assert game._votes["p1"] is False
        assert game.players["p1"].has_vote_token is True

    async def test_dead_no_token_cannot_vote(self, mock_manager: FakeManager):
        game = make_game({"p1": "washerwoman", "p2": "empath", "p3": "poisoner", "p4": "imp"})
        kill(game, "p1")
        game.players["p1"].has_vote_token = False
        game.phase = GamePhase.DAY
        game.day_sub = DaySubPhase.VOTING
        game._vote_eligible_count = 3
        game._votes = {}

        await game.handle_vote("p1", True)
        assert "p1" not in game._votes


# ---------------------------------------------------------------------------
# 7. Night info history & reconnect replay
# ---------------------------------------------------------------------------

class TestNightInfoReconnect:

    async def test_night_info_stored(self, mock_manager: FakeManager):
        game = make_game({
            "p1": "chef",
            "p2": "empath",
            "p3": "washerwoman",
            "p4": "poisoner",
            "p5": "imp",
        })
        game.phase = GamePhase.FIRST_NIGHT
        for p in game.players.values():
            p.poisoned = False
            p.protected = False

        async def mock_ask(pid, action_type, prompt, options):
            return options[0]["id"]

        with patch.object(game, "_ask_player_choose", side_effect=mock_ask):
            await game._process_night_actions(first_night=True)

        stored = game.get_player_night_info("p1")
        assert len(stored) >= 1
        assert stored[0]["info_type"] == "chef_result"

    async def test_night_info_cleared_on_restart(self, mock_manager: FakeManager):
        game = make_game({"p1": "chef", "p2": "empath", "p3": "poisoner", "p4": "imp"})
        game._player_night_info = {"p1": [{"info_type": "chef_result", "message": "test"}]}
        await game.restart_game()
        assert game.get_player_night_info("p1") == []

    async def test_get_player_night_info_empty_for_unknown(self, mock_manager: FakeManager):
        game = make_game({"p1": "washerwoman", "p2": "imp"})
        assert game.get_player_night_info("p999") == []

    async def test_minion_demon_info_stored(self, mock_manager: FakeManager):
        game = make_game({
            "p1": "washerwoman",
            "p2": "empath",
            "p3": "chef",
            "p4": "poisoner",
            "p5": "imp",
        })
        game.phase = GamePhase.FIRST_NIGHT

        await game._send_minion_info()
        await game._send_demon_info()

        minion_info = game.get_player_night_info("p4")
        assert any(d["info_type"] == "minion_info" for d in minion_info)

        demon_info = game.get_player_night_info("p5")
        assert any(d["info_type"] == "demon_info" for d in demon_info)


# ---------------------------------------------------------------------------
# 8. Vote progress broadcast (check marks)
# ---------------------------------------------------------------------------

class TestVoteProgressBroadcast:

    async def test_voted_players_in_state(self, mock_manager: FakeManager):
        game = make_game({"p1": "washerwoman", "p2": "empath", "p3": "poisoner", "p4": "imp"})
        game.phase = GamePhase.DAY
        game.day_sub = DaySubPhase.VOTING
        game._vote_eligible_count = 4
        game._votes = {"p1": True}

        mock_manager.broadcasts.clear()
        await game.handle_vote("p2", True)

        states = broadcast_msgs(mock_manager, "game_state")
        assert len(states) >= 1
        voted = states[-1].get("voted_players", [])
        assert "p1" in voted
        assert "p2" in voted

    async def test_voted_players_empty_outside_voting(self, mock_manager: FakeManager):
        game = make_game({"p1": "washerwoman", "p2": "empath", "p3": "poisoner", "p4": "imp"})
        game.phase = GamePhase.DAY
        game.day_sub = DaySubPhase.NOMINATION
        game._votes = {"p1": True}

        state = game.public_state()
        assert state["voted_players"] == []


# ---------------------------------------------------------------------------
# 9. Role snapshot prevents double-action after starpass
# ---------------------------------------------------------------------------

class TestRoleSnapshotNightActions:

    async def test_role_snapshot_prevents_minion_imp_action(self, mock_manager: FakeManager):
        game = make_game({
            "p1": "washerwoman",
            "p2": "empath",
            "p3": "monk",
            "p4": "fortune_teller",
            "p5": "poisoner",
            "p6": "imp",
        })
        game.phase = GamePhase.NIGHT
        game._night_deaths = []
        for p in game.players.values():
            p.poisoned = False
            p.protected = False

        action_log = []

        async def mock_ask(pid, action_type, prompt, options):
            action_log.append((pid, action_type))
            if pid == "p5" and action_type == "poison_target":
                return "p1"
            if pid == "p3" and action_type == "monk_protect":
                return "p2"
            if pid == "p6" and action_type == "imp_kill":
                return "p6"
            return options[0]["id"]

        with patch.object(game, "_ask_player_choose", side_effect=mock_ask):
            with patch.object(game, "_ask_player_choose_two", return_value=["p1", "p2"]):
                await game._process_night_actions(first_night=False)

        p5_actions = [(pid, at) for pid, at in action_log if pid == "p5"]
        assert len(p5_actions) == 1
        assert p5_actions[0][1] == "poison_target"
        assert not any(at == "imp_kill" for pid, at in action_log if pid == "p5")


# ---------------------------------------------------------------------------
# 10. Nomination flow: speech phases and voting
# ---------------------------------------------------------------------------

class TestNominationFlow:

    @patch("asyncio.sleep", new=instant_sleep)
    async def test_nomination_speech_vote_cycle(self, mock_manager: FakeManager):
        game = make_game({
            "p1": "washerwoman",
            "p2": "empath",
            "p3": "slayer",
            "p4": "poisoner",
            "p5": "imp",
        })
        game.phase = GamePhase.DAY
        game.day_number = 1
        game.day_sub = DaySubPhase.NOMINATION
        game._nominations_remaining = 5

        day_task = asyncio.create_task(game._run_day())
        await wait_sub(game, DaySubPhase.NOMINATION)

        await game.handle_nominate("p1", "p5")

        # Nominator speech
        await wait_sub(game, DaySubPhase.NOMINATOR_SPEECH)
        assert game._speech_player == "p1"
        game.handle_signal("speech_done")

        # Nominee speech
        await wait_sub(game, DaySubPhase.NOMINEE_SPEECH)
        assert game._speech_player == "p5"
        game.handle_signal("speech_done")

        await wait_sub(game, DaySubPhase.VOTING)
        assert len(game._votes) == 0  # no auto-votes

        # All vote no → nomination fails
        for pid in ["p1", "p2", "p3", "p4", "p5"]:
            await game.handle_vote(pid, False)

        await wait_sub(game, DaySubPhase.NOMINATION)
        assert game._nomination_records[-1]["passed"] is False

        game._pending_nomination = None
        game.handle_signal("nomination_submitted")
        day_task.cancel()


# ---------------------------------------------------------------------------
# 11. End nomination proposal
# ---------------------------------------------------------------------------

class TestEndNominationProposal:

    @patch("asyncio.sleep", new=instant_sleep)
    async def test_end_nom_all_agree(self, mock_manager: FakeManager):
        game = make_game({"p1": "washerwoman", "p2": "empath", "p3": "poisoner", "p4": "imp"})
        game.phase = GamePhase.DAY
        game.day_number = 1
        game.day_sub = DaySubPhase.NOMINATION
        game._nominations_remaining = 4

        # Only run the day, not the full game loop (which would enter night)
        day_task = asyncio.create_task(game._run_day())
        await wait_sub(game, DaySubPhase.NOMINATION)

        await game.handle_propose_end_nominations("p1")
        await game.handle_vote_end_nominations("p2", True)
        await game.handle_vote_end_nominations("p3", True)
        await game.handle_vote_end_nominations("p4", True)

        # _run_day should exit after end nominations (no execution)
        for _ in range(300):
            await _real_sleep(0)
            if day_task.done():
                break

        assert day_task.done()
        # No execution happened
        assert game._executed_today == ""
        day_task.cancel()

    @patch("asyncio.sleep", new=instant_sleep)
    async def test_end_nom_rejected(self, mock_manager: FakeManager):
        game = make_game({"p1": "washerwoman", "p2": "empath", "p3": "poisoner", "p4": "imp"})
        game.phase = GamePhase.DAY
        game.day_number = 1
        game.day_sub = DaySubPhase.NOMINATION
        game._nominations_remaining = 4

        day_task = asyncio.create_task(game._run_day())
        await wait_sub(game, DaySubPhase.NOMINATION)

        await game.handle_propose_end_nominations("p1")
        await game.handle_vote_end_nominations("p2", False)

        await asyncio.sleep(0)
        assert game.day_sub == DaySubPhase.NOMINATION
        assert game._end_nom_proposer == ""

        game._pending_nomination = None
        game.handle_signal("nomination_submitted")
        day_task.cancel()


# ---------------------------------------------------------------------------
# 12. Private state
# ---------------------------------------------------------------------------

class TestPrivateState:

    async def test_good_player(self, mock_manager: FakeManager):
        game = make_game({"p1": "washerwoman", "p2": "imp"})
        game.phase = GamePhase.DAY
        s = game.private_state("p1")
        assert s["role_id"] == "washerwoman"
        assert s["team"] == "townsfolk"
        assert s["alignment"] == "good"

    async def test_evil_player(self, mock_manager: FakeManager):
        game = make_game({"p1": "washerwoman", "p2": "imp"})
        game.phase = GamePhase.DAY
        s = game.private_state("p2")
        assert s["role_id"] == "imp"
        assert s["team"] == "demon"
        assert s["alignment"] == "evil"


# ---------------------------------------------------------------------------
# 13. Slayer ability
# ---------------------------------------------------------------------------

class TestSlayerAbility:

    async def test_real_slayer_kills_demon(self, mock_manager: FakeManager):
        game = make_game({"p1": "slayer", "p2": "empath", "p3": "poisoner", "p4": "imp"})
        game.phase = GamePhase.DAY
        await game.handle_slayer("p1", "p4")
        assert not game.players["p4"].alive
        assert game.players["p1"].used_ability is True
        assert game.phase == GamePhase.GAME_OVER

    async def test_slayer_misses_non_demon(self, mock_manager: FakeManager):
        game = make_game({"p1": "slayer", "p2": "empath", "p3": "poisoner", "p4": "imp"})
        game.phase = GamePhase.DAY
        await game.handle_slayer("p1", "p2")
        assert game.players["p2"].alive
        assert game.phase != GamePhase.GAME_OVER

    async def test_fake_slayer_does_nothing(self, mock_manager: FakeManager):
        game = make_game({"p1": "empath", "p2": "slayer", "p3": "poisoner", "p4": "imp"})
        game.phase = GamePhase.DAY
        await game.handle_slayer("p1", "p4")
        assert game.players["p4"].alive

    async def test_poisoned_slayer_fails(self, mock_manager: FakeManager):
        game = make_game({"p1": "slayer", "p2": "empath", "p3": "poisoner", "p4": "imp"})
        game.players["p1"].poisoned = True
        game.phase = GamePhase.DAY
        await game.handle_slayer("p1", "p4")
        assert game.players["p4"].alive


# ---------------------------------------------------------------------------
# 14. Saint execution → evil wins
# ---------------------------------------------------------------------------

class TestSaintExecution:

    async def test_saint_executed_evil_wins(self, mock_manager: FakeManager):
        game = make_game({"p1": "saint", "p2": "empath", "p3": "washerwoman", "p4": "poisoner", "p5": "imp"})
        game.phase = GamePhase.DAY
        await game._execute_player("p1")
        assert not game.players["p1"].alive
        assert game.phase == GamePhase.GAME_OVER
        go = broadcast_msgs(mock_manager, "game_over")
        assert go[-1]["winner"] == "evil"


# ---------------------------------------------------------------------------
# 15. Bot butler voting respects master rule
# ---------------------------------------------------------------------------

class TestBotButlerVoting:

    async def test_bot_butler_vote_recorded_freely(self, mock_manager: FakeManager):
        """Bot butler votes freely; tally handles voiding."""
        game = make_game({"p1": "washerwoman", "p2": "empath", "p3": "butler", "p4": "poisoner", "p5": "imp"})
        game.players["p3"].is_bot = True
        game.players["p3"].butler_master_id = "p2"
        game.phase = GamePhase.DAY
        game.day_sub = DaySubPhase.VOTING
        game._vote_eligible_count = 5
        game._votes = {"p1": True}
        game._votes["p2"] = False  # master votes no

        await game._bot_auto_vote()

        # Bot butler's vote is recorded (True or False depending on random)
        assert "p3" in game._votes


# ---------------------------------------------------------------------------
# 16. Scarlet Woman becomes Imp on demon death
# ---------------------------------------------------------------------------

class TestScarletWoman:

    async def test_scarlet_woman_becomes_imp_on_execution(self, mock_manager: FakeManager):
        game = make_game({
            "p1": "washerwoman",
            "p2": "empath",
            "p3": "slayer",
            "p4": "fortune_teller",
            "p5": "scarlet_woman",
            "p6": "imp",
        })
        game.phase = GamePhase.DAY

        await game._execute_player("p6")

        assert game.players["p5"].role_id == "imp"
        assert game.phase != GamePhase.GAME_OVER  # game continues

    async def test_scarlet_woman_no_trigger_below_5(self, mock_manager: FakeManager):
        game = make_game({
            "p1": "washerwoman",
            "p2": "empath",
            "p3": "slayer",
            "p4": "scarlet_woman",
            "p5": "imp",
        })
        kill(game, "p1")  # 4 alive, then execute imp → 3 alive < 5
        game.phase = GamePhase.DAY

        await game._execute_player("p5")

        assert game.players["p4"].role_id == "scarlet_woman"  # NOT triggered
        assert game.phase == GamePhase.GAME_OVER
        go = broadcast_msgs(mock_manager, "game_over")
        assert go[-1]["winner"] == "good"


# ---------------------------------------------------------------------------
# 17. Virgin ability
# ---------------------------------------------------------------------------

class TestVirginAbility:

    @patch("asyncio.sleep", new=instant_sleep)
    async def test_townsfolk_nominates_virgin(self, mock_manager: FakeManager):
        game = make_game({
            "p1": "washerwoman",
            "p2": "virgin",
            "p3": "empath",
            "p4": "poisoner",
            "p5": "imp",
        })
        game.phase = GamePhase.DAY
        game.day_number = 1
        game.day_sub = DaySubPhase.NOMINATION
        game._nominations_remaining = 5

        day_task = asyncio.create_task(game._run_day())
        await wait_sub(game, DaySubPhase.NOMINATION)

        # Townsfolk (p1) nominates virgin (p2) → p1 is executed
        await game.handle_nominate("p1", "p2")

        # Wait a bit for the signal to propagate
        for _ in range(50):
            await asyncio.sleep(0)
            if not game.players["p1"].alive:
                break

        assert not game.players["p1"].alive
        assert game._executed_today == "p1"
        assert game.players["p2"].used_ability is True

        day_task.cancel()
