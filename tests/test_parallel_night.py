"""Tests for the parallel night action system.

Covers: collect/wait/resolve pipeline, night_waiting messages,
timeout auto-pick, poisoner→info ordering, first-night parallel,
imp self-kill in parallel, ravenkeeper serial trigger, submit_action routing.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch, AsyncMock

import pytest

from server.game_engine import Game, MIN_NIGHT_WAIT, NIGHT_ACTION_TIMEOUT
from server.models import GamePhase
from tests.conftest import FakeManager, make_game, kill, poison


def personal_msgs(mgr: FakeManager, pid: str, msg_type: str) -> list[dict]:
    return [data for _, p, t, data in mgr.messages if p == pid and t == msg_type]


def all_personal(mgr: FakeManager, msg_type: str) -> list[tuple[str, dict]]:
    return [(p, data) for _, p, t, data in mgr.messages if t == msg_type]


class _PatchNight:
    """Patches _collect_player_choose and _wait_all_parallel for instant tests."""

    def __init__(self, game: Game, choice_fn):
        self.game = game
        self.choice_fn = choice_fn
        self._patches = []

    async def _patched_collect(self, pid, action_type, prompt, options, choose_count=1):
        if not options or (choose_count > 1 and len(options) < choose_count):
            return
        result = self.choice_fn(pid, action_type, prompt, options)
        if result is None:
            if choose_count > 1:
                result = [o["id"] for o in options[:choose_count]]
            else:
                result = options[0]["id"] if options else None

        evt = asyncio.Event()
        evt.set()
        self.game._pending_actions[pid] = evt
        if choose_count > 1:
            ids = result if isinstance(result, list) else [result]
            self.game._action_responses[pid] = {"chosen_ids": ids}
        else:
            self.game._action_responses[pid] = {"chosen_id": result}

    async def _patched_wait(self, min_wait=0):
        self.game._pending_actions.clear()

    def __enter__(self):
        p1 = patch.object(self.game, "_collect_player_choose", side_effect=self._patched_collect)
        p2 = patch.object(self.game, "_wait_all_parallel", side_effect=self._patched_wait)
        self._patches = [p1, p2]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *args):
        for p in self._patches:
            p.stop()


def pn(game, choice_fn):
    return _PatchNight(game, choice_fn)


# ======================================================================
# 1. night_waiting sent to correct players
# ======================================================================

class TestNightWaitingMessages:

    async def test_non_acting_players_get_waiting(self, mock_manager: FakeManager):
        """Soldier (no night ability) should receive night_waiting."""
        game = make_game({
            "p1": "soldier",
            "p2": "poisoner",
            "p3": "imp",
            "p4": "empath",
            "p5": "monk",
        })
        game.phase = GamePhase.NIGHT
        game._night_deaths = []
        for p in game.players.values():
            p.poisoned = False
            p.protected = False

        def choose(pid, action_type, prompt, options):
            return options[0]["id"]

        with pn(game, choose):
            await game._process_night_actions(first_night=False)

        waiting = personal_msgs(mock_manager, "p1", "night_waiting")
        assert len(waiting) == 1, "soldier should get night_waiting"

    async def test_info_roles_get_waiting(self, mock_manager: FakeManager):
        """Empath (info-only, no choice needed) should get night_waiting, not night_action."""
        game = make_game({
            "p1": "empath",
            "p2": "poisoner",
            "p3": "imp",
            "p4": "monk",
            "p5": "washerwoman",
        })
        game.phase = GamePhase.NIGHT
        game._night_deaths = []
        for p in game.players.values():
            p.poisoned = False
            p.protected = False

        def choose(pid, action_type, prompt, options):
            return options[0]["id"]

        with pn(game, choose):
            await game._process_night_actions(first_night=False)

        waiting = personal_msgs(mock_manager, "p1", "night_waiting")
        assert len(waiting) == 1, "empath should get night_waiting"

        actions = personal_msgs(mock_manager, "p1", "night_action")
        assert len(actions) == 0, "empath should NOT get night_action"

    async def test_choice_roles_get_action_not_waiting(self, mock_manager: FakeManager):
        """Poisoner/Monk/Imp should get night_action, not night_waiting."""
        game = make_game({
            "p1": "soldier",
            "p2": "poisoner",
            "p3": "imp",
            "p4": "monk",
            "p5": "empath",
        })
        game.phase = GamePhase.NIGHT
        game._night_deaths = []
        for p in game.players.values():
            p.poisoned = False
            p.protected = False

        def choose(pid, action_type, prompt, options):
            return options[0]["id"]

        with pn(game, choose):
            await game._process_night_actions(first_night=False)

        for pid in ["p2", "p3", "p4"]:
            waiting = personal_msgs(mock_manager, pid, "night_waiting")
            assert len(waiting) == 0, f"{pid} should NOT get night_waiting"

    async def test_dead_players_get_nothing(self, mock_manager: FakeManager):
        """Dead players should not receive night_waiting or night_action."""
        game = make_game({
            "p1": "soldier",
            "p2": "poisoner",
            "p3": "imp",
            "p4": "monk",
            "p5": "empath",
        })
        kill(game, "p1")
        game.phase = GamePhase.NIGHT
        game._night_deaths = []
        for p in game.players.values():
            p.poisoned = False
            p.protected = False

        def choose(pid, action_type, prompt, options):
            return options[0]["id"]

        with pn(game, choose):
            await game._process_night_actions(first_night=False)

        waiting = personal_msgs(mock_manager, "p1", "night_waiting")
        actions = personal_msgs(mock_manager, "p1", "night_action")
        assert len(waiting) == 0
        assert len(actions) == 0


# ======================================================================
# 2. Parallel collect → resolve pipeline
# ======================================================================

class TestParallelPipeline:

    async def test_all_choices_collected_before_resolve(self, mock_manager: FakeManager):
        """Poisoner, Monk, Imp all get prompts simultaneously,
        then resolve in order: poisoner → monk → imp."""
        game = make_game({
            "p1": "empath",
            "p2": "poisoner",
            "p3": "monk",
            "p4": "fortune_teller",
            "p5": "imp",
        })
        game.phase = GamePhase.NIGHT
        game._night_deaths = []
        for p in game.players.values():
            p.poisoned = False
            p.protected = False

        def choose(pid, action_type, prompt, options):
            if pid == "p2":
                return "p1"  # poison empath
            if pid == "p3":
                return "p1"  # protect empath
            if pid == "p5":
                return "p1"  # kill empath
            if action_type == "fortune_teller_pick":
                return ["p2", "p3"]
            return options[0]["id"]

        with pn(game, choose):
            await game._process_night_actions(first_night=False)

        assert game.players["p1"].poisoned is True, "poisoner resolved"
        assert game.players["p1"].protected is True, "monk resolved"
        assert "p1" not in game._night_deaths, "monk protection should save p1"

    async def test_poisoner_affects_empath_info(self, mock_manager: FakeManager):
        """Poisoner poisons empath → empath gets random (possibly wrong) info."""
        game = make_game({
            "p1": "empath",
            "p2": "poisoner",
            "p3": "imp",
            "p4": "washerwoman",
            "p5": "chef",
        })
        game.phase = GamePhase.NIGHT
        game._night_deaths = []
        for p in game.players.values():
            p.poisoned = False
            p.protected = False

        def choose(pid, action_type, prompt, options):
            if pid == "p2":
                return "p1"  # poison empath
            if pid == "p3":
                return "p4"  # kill washerwoman
            return options[0]["id"]

        with pn(game, choose):
            await game._process_night_actions(first_night=False)

        assert game.players["p1"].poisoned is True
        empath_info = personal_msgs(mock_manager, "p1", "night_info")
        assert len(empath_info) == 1
        assert empath_info[0]["info_type"] == "empath_result"


# ======================================================================
# 3. First night parallel
# ======================================================================

class TestFirstNightParallel:

    async def test_first_night_poisoner_and_info_roles(self, mock_manager: FakeManager):
        """First night: poisoner + fortune_teller + butler get prompts;
        washerwoman/chef/empath get night_waiting then info."""
        game = make_game({
            "p1": "washerwoman",
            "p2": "chef",
            "p3": "empath",
            "p4": "fortune_teller",
            "p5": "butler",
            "p6": "poisoner",
            "p7": "imp",
        })
        game.phase = GamePhase.FIRST_NIGHT
        for p in game.players.values():
            p.poisoned = False
            p.protected = False

        def choose(pid, action_type, prompt, options):
            if pid == "p6":
                return "p3"  # poison empath
            if action_type == "fortune_teller_pick":
                return [options[0]["id"], options[1]["id"]]
            return options[0]["id"]

        with pn(game, choose):
            await game._process_night_actions(first_night=True)

        assert game.players["p3"].poisoned is True

        for pid in ["p1", "p2", "p3"]:
            waiting = personal_msgs(mock_manager, pid, "night_waiting")
            assert len(waiting) == 1, f"{pid} should get night_waiting on first night"

        washer_info = personal_msgs(mock_manager, "p1", "night_info")
        assert len(washer_info) >= 1
        chef_info = personal_msgs(mock_manager, "p2", "night_info")
        assert len(chef_info) >= 1


# ======================================================================
# 4. Imp self-kill (starpass) in parallel
# ======================================================================

class TestImpSelfKillParallel:

    async def test_starpass_via_parallel(self, mock_manager: FakeManager):
        """Imp self-kills → minion becomes imp; all via parallel pipeline."""
        game = make_game({
            "p1": "empath",
            "p2": "monk",
            "p3": "fortune_teller",
            "p4": "poisoner",
            "p5": "imp",
        })
        game.phase = GamePhase.NIGHT
        game._night_deaths = []
        for p in game.players.values():
            p.poisoned = False
            p.protected = False

        def choose(pid, action_type, prompt, options):
            if pid == "p4":
                return "p1"
            if pid == "p2":
                return "p1"
            if pid == "p5":
                return "p5"  # self-kill
            if action_type == "fortune_teller_pick":
                return ["p1", "p2"]
            return options[0]["id"]

        with pn(game, choose):
            await game._process_night_actions(first_night=False)

        assert not game.players["p5"].alive
        assert game.players["p4"].role_id == "imp"
        assert "p5" in game._night_deaths


# ======================================================================
# 5. Ravenkeeper triggers serially during resolve
# ======================================================================

class TestRavenkeeperSerial:

    async def test_ravenkeeper_gets_serial_prompt(self, mock_manager: FakeManager):
        """Ravenkeeper killed by imp → gets a serial _ask_player_choose prompt."""
        game = make_game({
            "p1": "ravenkeeper",
            "p2": "poisoner",
            "p3": "monk",
            "p4": "empath",
            "p5": "imp",
        })
        game.phase = GamePhase.NIGHT
        game._night_deaths = []
        for p in game.players.values():
            p.poisoned = False
            p.protected = False

        def choose(pid, action_type, prompt, options):
            if pid == "p2":
                return "p4"  # poison empath
            if pid == "p3":
                return "p4"  # protect empath
            if pid == "p5":
                return "p1"  # kill ravenkeeper
            return options[0]["id"]

        with pn(game, choose):
            with patch.object(game, "_ask_player_choose", return_value="p5"):
                await game._process_night_actions(first_night=False)

        assert "p1" in game._night_deaths
        rk_info = personal_msgs(mock_manager, "p1", "night_info")
        assert any(d.get("info_type") == "ravenkeeper_result" for d in rk_info)


# ======================================================================
# 6. Timeout auto-pick
# ======================================================================

class TestTimeoutAutoPick:

    async def test_timeout_produces_valid_choice(self, mock_manager: FakeManager):
        """When _wait_all_parallel times out, it should auto-pick from options."""
        game = make_game({
            "p1": "poisoner",
            "p2": "washerwoman",
            "p3": "imp",
            "p4": "empath",
            "p5": "chef",
        })
        game.phase = GamePhase.NIGHT
        game._night_deaths = []
        for p in game.players.values():
            p.poisoned = False
            p.protected = False

        targets = [pp for pp in game.alive_players() if pp.player_id != "p1"]
        options = [{"id": t.player_id, "name": t.name} for t in targets]

        evt = asyncio.Event()  # never set → will timeout
        game._pending_actions["p1"] = evt
        game._pending_options["p1"] = (options, 1)

        with patch("server.game_engine.NIGHT_ACTION_TIMEOUT", 0.01):
            await game._wait_all_parallel(min_wait=0)

        result = game._action_responses.get("p1", {})
        assert "chosen_id" in result, "timeout should auto-pick"
        assert result["chosen_id"] in [o["id"] for o in options]

    async def test_timeout_multi_choice_produces_valid(self, mock_manager: FakeManager):
        """Multi-choice timeout (fortune_teller) should auto-pick 2."""
        game = make_game({
            "p1": "fortune_teller",
            "p2": "washerwoman",
            "p3": "imp",
            "p4": "empath",
            "p5": "chef",
        })
        targets = [pp for pp in game.players.values() if pp.player_id != "p1"]
        options = [{"id": t.player_id, "name": t.name} for t in targets]

        evt = asyncio.Event()
        game._pending_actions["p1"] = evt
        game._pending_options["p1"] = (options, 2)

        with patch("server.game_engine.NIGHT_ACTION_TIMEOUT", 0.01):
            await game._wait_all_parallel(min_wait=0)

        result = game._action_responses.get("p1", {})
        assert "chosen_ids" in result
        assert len(result["chosen_ids"]) == 2
        valid_ids = {o["id"] for o in options}
        assert all(cid in valid_ids for cid in result["chosen_ids"])


# ======================================================================
# 7. submit_action routing
# ======================================================================

class TestSubmitActionRouting:

    async def test_parallel_path(self, mock_manager: FakeManager):
        """submit_action sets response and fires event for parallel actions."""
        game = make_game({"p1": "poisoner", "p2": "imp", "p3": "chef", "p4": "empath", "p5": "washerwoman"})
        evt = asyncio.Event()
        game._pending_actions["p1"] = evt

        game.submit_action("p1", {"chosen_id": "p2"})

        assert evt.is_set()
        assert game._action_responses["p1"]["chosen_id"] == "p2"

    async def test_legacy_path(self, mock_manager: FakeManager):
        """submit_action falls through to legacy path for ravenkeeper."""
        game = make_game({"p1": "ravenkeeper", "p2": "imp", "p3": "chef", "p4": "empath", "p5": "washerwoman"})
        game._pending_action = asyncio.Event()
        game._pending_action_player = "p1"

        game.submit_action("p1", {"chosen_id": "p2"})

        assert game._pending_action.is_set()
        assert game._action_response["chosen_id"] == "p2"

    async def test_wrong_player_ignored_legacy(self, mock_manager: FakeManager):
        """Legacy path ignores actions from wrong player."""
        game = make_game({"p1": "ravenkeeper", "p2": "imp", "p3": "chef", "p4": "empath", "p5": "washerwoman"})
        game._pending_action = asyncio.Event()
        game._pending_action_player = "p1"

        game.submit_action("p2", {"chosen_id": "p3"})

        assert not game._pending_action.is_set()

    async def test_parallel_takes_priority(self, mock_manager: FakeManager):
        """If player is in both parallel and legacy, parallel path wins."""
        game = make_game({"p1": "poisoner", "p2": "imp", "p3": "chef", "p4": "empath", "p5": "washerwoman"})
        evt = asyncio.Event()
        game._pending_actions["p1"] = evt
        game._pending_action = asyncio.Event()
        game._pending_action_player = "p1"

        game.submit_action("p1", {"chosen_id": "p2"})

        assert evt.is_set()
        assert not game._pending_action.is_set()


# ======================================================================
# 8. Resolve order: poisoner before info roles
# ======================================================================

class TestResolveOrder:

    async def test_poisoned_fortune_teller_gets_random_result(self, mock_manager: FakeManager):
        """Poisoner targets fortune_teller → FT gets random (unreliable) result."""
        game = make_game({
            "p1": "fortune_teller",
            "p2": "poisoner",
            "p3": "imp",
            "p4": "empath",
            "p5": "chef",
        })
        game.phase = GamePhase.NIGHT
        game._night_deaths = []
        for p in game.players.values():
            p.poisoned = False
            p.protected = False

        def choose(pid, action_type, prompt, options):
            if pid == "p2":
                return "p1"  # poison fortune_teller
            if pid == "p3":
                return "p4"  # kill empath
            if action_type == "fortune_teller_pick":
                return ["p4", "p5"]  # neither is demon
            return options[0]["id"]

        with pn(game, choose):
            await game._process_night_actions(first_night=False)

        assert game.players["p1"].poisoned is True
        ft_info = personal_msgs(mock_manager, "p1", "night_info")
        assert len(ft_info) == 1
        assert ft_info[0]["info_type"] == "fortune_teller_result"

    async def test_poisoned_monk_protection_fails(self, mock_manager: FakeManager):
        """Poisoner targets monk → monk's protection has no effect."""
        game = make_game({
            "p1": "monk",
            "p2": "poisoner",
            "p3": "imp",
            "p4": "empath",
            "p5": "chef",
        })
        game.phase = GamePhase.NIGHT
        game._night_deaths = []
        for p in game.players.values():
            p.poisoned = False
            p.protected = False

        def choose(pid, action_type, prompt, options):
            if pid == "p2":
                return "p1"  # poison monk
            if pid == "p1":
                return "p4"  # monk tries to protect empath
            if pid == "p3":
                return "p4"  # imp kills empath
            return options[0]["id"]

        with pn(game, choose):
            await game._process_night_actions(first_night=False)

        assert game.players["p1"].poisoned is True
        assert game.players["p4"].protected is False, "poisoned monk should not protect"
        assert "p4" in game._night_deaths, "empath should die (no protection)"


# ======================================================================
# 9. _pending_options / _pending_actions cleanup
# ======================================================================

class TestCleanup:

    async def test_restart_clears_parallel_state(self, mock_manager: FakeManager):
        game = make_game({"p1": "poisoner", "p2": "imp", "p3": "chef", "p4": "empath", "p5": "washerwoman"})
        game._pending_actions["p1"] = asyncio.Event()
        game._action_responses["p1"] = {"chosen_id": "p2"}
        game._pending_options["p1"] = ([{"id": "p2"}], 1)

        await game.restart_game()

        assert game._pending_actions == {}
        assert game._action_responses == {}
        assert game._pending_options == {}

    async def test_wait_clears_pending(self, mock_manager: FakeManager):
        """After _wait_all_parallel, _pending_actions and _pending_options are empty."""
        game = make_game({"p1": "poisoner", "p2": "imp", "p3": "chef", "p4": "empath", "p5": "washerwoman"})
        evt = asyncio.Event()
        evt.set()
        game._pending_actions["p1"] = evt
        game._action_responses["p1"] = {"chosen_id": "p2"}
        game._pending_options["p1"] = ([{"id": "p2"}], 1)

        await game._wait_all_parallel(min_wait=0)

        assert game._pending_actions == {}
        assert game._pending_options == {}
        assert game._action_responses.get("p1") == {"chosen_id": "p2"}


# ======================================================================
# 10. Full night round-trip: collect → wait → resolve → announce
# ======================================================================

class TestFullNightRoundTrip:

    async def test_normal_night_kill_announced(self, mock_manager: FakeManager):
        """Full parallel night: imp kills target → death announced."""
        game = make_game({
            "p1": "empath",
            "p2": "poisoner",
            "p3": "monk",
            "p4": "fortune_teller",
            "p5": "imp",
        })
        game.phase = GamePhase.NIGHT
        game._night_deaths = []
        for p in game.players.values():
            p.poisoned = False
            p.protected = False

        def choose(pid, action_type, prompt, options):
            if pid == "p2":
                return "p1"  # poison empath
            if pid == "p3":
                return "p4"  # protect fortune_teller
            if pid == "p5":
                return "p1"  # kill empath
            if action_type == "fortune_teller_pick":
                return ["p2", "p3"]
            return options[0]["id"]

        with pn(game, choose):
            await game._process_night_actions(first_night=False)

        assert "p1" in game._night_deaths
        assert not game.players["p1"].alive

        death_broadcasts = [
            d for _, t, d in mock_manager.broadcasts if t == "night_result"
        ]
        assert len(death_broadcasts) == 1
        assert "p1" in death_broadcasts[0]["deaths"]

    async def test_no_kill_peaceful_night(self, mock_manager: FakeManager):
        """Monk protects imp's target → no one dies."""
        game = make_game({
            "p1": "empath",
            "p2": "poisoner",
            "p3": "monk",
            "p4": "fortune_teller",
            "p5": "imp",
        })
        game.phase = GamePhase.NIGHT
        game._night_deaths = []
        for p in game.players.values():
            p.poisoned = False
            p.protected = False

        def choose(pid, action_type, prompt, options):
            if pid == "p2":
                return "p4"  # poison fortune_teller (not the target)
            if pid == "p3":
                return "p1"  # protect empath
            if pid == "p5":
                return "p1"  # kill empath (but protected)
            if action_type == "fortune_teller_pick":
                return ["p2", "p3"]
            return options[0]["id"]

        with pn(game, choose):
            await game._process_night_actions(first_night=False)

        assert game._night_deaths == []
        assert game.players["p1"].alive

        death_broadcasts = [
            d for _, t, d in mock_manager.broadcasts if t == "night_result"
        ]
        assert len(death_broadcasts) == 1
        assert death_broadcasts[0]["deaths"] == []


# ======================================================================
# 11. Poisoned/drunk monk still gets a prompt (no info leak)
# ======================================================================

class TestNoInfoLeakOnPoisonDrunk:

    async def test_poisoned_monk_still_gets_prompt(self, mock_manager: FakeManager):
        """A monk poisoned THIS night should still receive a choice prompt
        (to avoid revealing they are poisoned). The protection just fails."""
        game = make_game({
            "p1": "monk",
            "p2": "poisoner",
            "p3": "imp",
            "p4": "empath",
            "p5": "chef",
        })
        game.phase = GamePhase.NIGHT
        game._night_deaths = []
        for p in game.players.values():
            p.poisoned = False
            p.protected = False

        prompted = []

        def choose(pid, action_type, prompt, options):
            prompted.append((pid, action_type))
            if pid == "p2":
                return "p1"  # poison monk
            if pid == "p1":
                return "p4"  # monk tries to protect empath
            if pid == "p3":
                return "p4"  # imp kills empath
            return options[0]["id"]

        with pn(game, choose):
            await game._process_night_actions(first_night=False)

        monk_prompted = [p for p in prompted if p[0] == "p1"]
        assert len(monk_prompted) == 1, "poisoned monk should still get a prompt"
        assert game.players["p4"].protected is False, "poisoned monk protection should fail"

    async def test_drunk_monk_still_gets_prompt(self, mock_manager: FakeManager):
        """A drunk monk should still receive a choice prompt."""
        game = make_game({
            "p1": "monk",
            "p2": "poisoner",
            "p3": "imp",
            "p4": "empath",
            "p5": "chef",
        })
        game.players["p1"].drunk = True
        game.phase = GamePhase.NIGHT
        game._night_deaths = []
        for p in game.players.values():
            p.poisoned = False
            p.protected = False

        prompted = []

        def choose(pid, action_type, prompt, options):
            prompted.append((pid, action_type))
            if pid == "p1":
                return "p4"
            if pid == "p2":
                return "p3"
            if pid == "p3":
                return "p4"
            return options[0]["id"]

        with pn(game, choose):
            await game._process_night_actions(first_night=False)

        monk_prompted = [p for p in prompted if p[0] == "p1"]
        assert len(monk_prompted) == 1, "drunk monk should still get a prompt"
        assert game.players["p4"].protected is False, "drunk monk protection should fail"

    async def test_previously_poisoned_monk_gets_prompt(self, mock_manager: FakeManager):
        """A monk poisoned from the previous night (poison carries over) should
        still get a prompt. The per-night reset clears poison, so this tests
        the case where poison is NOT cleared (e.g. poisoner targets monk again)."""
        game = make_game({
            "p1": "monk",
            "p2": "poisoner",
            "p3": "imp",
            "p4": "empath",
            "p5": "chef",
        })
        game.phase = GamePhase.NIGHT
        game._night_deaths = []
        for p in game.players.values():
            p.poisoned = False
            p.protected = False

        prompted = []

        def choose(pid, action_type, prompt, options):
            prompted.append((pid, action_type))
            if pid == "p2":
                return "p1"  # poison monk again
            if pid == "p1":
                return "p4"
            if pid == "p3":
                return "p4"
            return options[0]["id"]

        with pn(game, choose):
            await game._process_night_actions(first_night=False)

        monk_prompted = [p for p in prompted if p[0] == "p1"]
        assert len(monk_prompted) == 1


# ======================================================================
# 12. Imp kills dead target → no death announced
# ======================================================================

class TestImpKillsDeadTarget:

    async def test_imp_targets_dead_player_no_death(self, mock_manager: FakeManager):
        """If imp targets an already dead player, no one should die from it."""
        game = make_game({
            "p1": "empath",
            "p2": "poisoner",
            "p3": "monk",
            "p4": "fortune_teller",
            "p5": "imp",
        })
        kill(game, "p1")
        game.phase = GamePhase.NIGHT
        game._night_deaths = []
        for p in game.players.values():
            p.poisoned = False
            p.protected = False

        def choose(pid, action_type, prompt, options):
            if pid == "p2":
                return "p3"
            if pid == "p3":
                return "p4"
            if pid == "p5":
                return "p1"  # target dead player
            if action_type == "fortune_teller_pick":
                return ["p2", "p3"]
            return options[0]["id"]

        with pn(game, choose):
            await game._process_night_actions(first_night=False)

        assert game._night_deaths == []
        death_broadcasts = [
            d for _, t, d in mock_manager.broadcasts if t == "night_result"
        ]
        assert len(death_broadcasts) == 1
        assert death_broadcasts[0]["deaths"] == []


# ======================================================================
# 13. Butler first night parallel
# ======================================================================

class TestButlerFirstNight:

    async def test_butler_gets_prompt_on_first_night(self, mock_manager: FakeManager):
        """Butler should receive a master selection prompt on first night."""
        game = make_game({
            "p1": "washerwoman",
            "p2": "chef",
            "p3": "butler",
            "p4": "poisoner",
            "p5": "imp",
        })
        game.phase = GamePhase.FIRST_NIGHT
        for p in game.players.values():
            p.poisoned = False
            p.protected = False

        prompted = []

        def choose(pid, action_type, prompt, options):
            prompted.append((pid, action_type))
            if pid == "p3":
                return "p1"  # butler picks master
            if pid == "p4":
                return "p2"
            return options[0]["id"]

        with pn(game, choose):
            await game._process_night_actions(first_night=True)

        butler_prompted = [p for p in prompted if p[0] == "p3"]
        assert len(butler_prompted) == 1
        assert butler_prompted[0][1] == "butler_master"
        assert game.players["p3"].butler_master_id == "p1"


# ======================================================================
# 14. Multiple roles with same night order
# ======================================================================

class TestMultipleSameOrder:

    async def test_all_prompted_even_if_same_order(self, mock_manager: FakeManager):
        """If two roles share the same night order value, both should still
        get prompted and resolved."""
        game = make_game({
            "p1": "empath",
            "p2": "poisoner",
            "p3": "fortune_teller",
            "p4": "butler",
            "p5": "imp",
        })
        game.phase = GamePhase.NIGHT
        game._night_deaths = []
        for p in game.players.values():
            p.poisoned = False
            p.protected = False

        prompted = []

        def choose(pid, action_type, prompt, options):
            prompted.append((pid, action_type))
            if pid == "p2":
                return "p1"
            if pid == "p5":
                return "p1"
            if pid == "p4":
                return "p2"
            if action_type == "fortune_teller_pick":
                return ["p2", "p4"]
            return options[0]["id"]

        with pn(game, choose):
            await game._process_night_actions(first_night=False)

        prompted_pids = {p[0] for p in prompted}
        assert "p2" in prompted_pids, "poisoner should be prompted"
        assert "p3" in prompted_pids, "fortune_teller should be prompted"
        assert "p4" in prompted_pids, "butler should be prompted"
        assert "p5" in prompted_pids, "imp should be prompted"
