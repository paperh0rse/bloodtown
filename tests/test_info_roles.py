"""Tests for first-night information roles: Washerwoman, Librarian, Investigator.

Key invariants:
- Washerwoman: one of the two shown players IS a real townsfolk; shown_role is that townsfolk's role.
  Spy may appear as the *wrong* player (decoy), never as the real townsfolk.
- Librarian: one of the two shown players IS a real outsider; shown_role is that outsider's role.
  Spy may appear as the decoy. If no outsiders exist, reports "没有外来者在场".
- Investigator: one of the two shown players IS a real minion; shown_role is that minion's role.
  Recluse may appear as the decoy, never as the real minion.
- When drunk or poisoned, all three give completely random (unreliable) info.
"""

from __future__ import annotations

import random
from unittest.mock import patch

import pytest

from server.models import Team
from server.role_data import ROLE_BY_ID, get_roles_by_team
from tests.conftest import FakeManager, make_game, poison


def night_infos(mgr: FakeManager, pid: str) -> list[dict]:
    return [data for _, p, t, data in mgr.messages if p == pid and t == "night_info"]


# ======================================================================
# Washerwoman
# ======================================================================

class TestWasherwoman:

    async def test_shows_real_townsfolk_with_correct_role(self, mock_manager):
        game = make_game({
            "a": "washerwoman", "b": "chef", "c": "empath",
            "d": "poisoner", "e": "imp",
        })
        await game._action_washerwoman("a")

        msgs = night_infos(mock_manager, "a")
        assert len(msgs) == 1
        data = msgs[0]
        assert data["info_type"] == "washerwoman_result"

        shown_ids = {data["player1"]["id"], data["player2"]["id"]}
        assert "a" not in shown_ids

        townsfolk_ids = {"b", "c"}
        real_tf_in_pair = shown_ids & townsfolk_ids
        assert len(real_tf_in_pair) >= 1
        assert data["role_id"] in ("chef", "empath")

    async def test_poisoned_gives_arbitrary_info(self, mock_manager):
        game = make_game({
            "a": "washerwoman", "b": "chef", "c": "empath",
            "d": "poisoner", "e": "imp",
        })
        poison(game, "a")

        await game._action_washerwoman("a")

        msgs = night_infos(mock_manager, "a")
        assert len(msgs) == 1

    async def test_drunk_gives_arbitrary_info(self, mock_manager):
        game = make_game({
            "a": "washerwoman", "b": "chef", "c": "empath",
            "d": "poisoner", "e": "imp",
        })
        game.players["a"].drunk = True

        await game._action_washerwoman("a")

        msgs = night_infos(mock_manager, "a")
        assert len(msgs) == 1

    async def test_spy_never_chosen_as_real_townsfolk(self, mock_manager):
        """Spy misregistration only places Spy in the decoy slot, not as the real townsfolk.
        Run many iterations to exercise the random path."""
        for seed in range(100):
            random.seed(seed)
            mock_manager.messages.clear()

            game = make_game({
                "a": "washerwoman", "b": "chef",
                "c": "spy", "d": "imp",
            })
            with patch.object(type(game), "_MISREGISTER_CHANCE", 1.0):
                await game._action_washerwoman("a")

            msgs = night_infos(mock_manager, "a")
            assert len(msgs) == 1, f"seed={seed}: expected 1 msg"
            data = msgs[0]

            assert data["role_id"] == "chef", (
                f"seed={seed}: shown_role should be chef (the only real townsfolk), "
                f"got {data['role_id']}"
            )

            shown_ids = {data["player1"]["id"], data["player2"]["id"]}
            assert "b" in shown_ids, (
                f"seed={seed}: real townsfolk 'b' must be in the pair"
            )

    async def test_spy_can_appear_as_decoy(self, mock_manager):
        """With MISREGISTER_CHANCE=1.0, Spy should sometimes be the decoy."""
        spy_appeared = False
        for seed in range(100):
            random.seed(seed)
            mock_manager.messages.clear()

            game = make_game({
                "a": "washerwoman", "b": "chef",
                "c": "spy", "d": "imp",
            })
            with patch.object(type(game), "_MISREGISTER_CHANCE", 1.0):
                await game._action_washerwoman("a")

            data = night_infos(mock_manager, "a")[0]
            shown_ids = {data["player1"]["id"], data["player2"]["id"]}
            if "c" in shown_ids:
                spy_appeared = True
                break

        assert spy_appeared, "Spy should appear as decoy at least once in 100 seeds"


# ======================================================================
# Librarian
# ======================================================================

class TestLibrarian:

    async def test_shows_real_outsider_with_correct_role(self, mock_manager):
        game = make_game({
            "a": "librarian", "b": "butler", "c": "chef",
            "d": "poisoner", "e": "imp",
        })
        await game._action_librarian("a")

        msgs = night_infos(mock_manager, "a")
        assert len(msgs) == 1
        data = msgs[0]
        assert data["info_type"] == "librarian_result"
        assert data["role_id"] == "butler"

        shown_ids = {data["player1"]["id"], data["player2"]["id"]}
        assert "b" in shown_ids

    async def test_no_outsiders_reports_none(self, mock_manager):
        game = make_game({
            "a": "librarian", "b": "chef", "c": "empath",
            "d": "poisoner", "e": "imp",
        })
        await game._action_librarian("a")

        msgs = night_infos(mock_manager, "a")
        assert len(msgs) == 1
        assert "没有外来者" in msgs[0]["message"]

    async def test_poisoned_gives_arbitrary_info(self, mock_manager):
        game = make_game({
            "a": "librarian", "b": "butler", "c": "chef",
            "d": "poisoner", "e": "imp",
        })
        poison(game, "a")

        await game._action_librarian("a")

        msgs = night_infos(mock_manager, "a")
        assert len(msgs) == 1

    async def test_spy_never_chosen_as_real_outsider(self, mock_manager):
        """Spy misregistration only places Spy in the decoy slot."""
        for seed in range(100):
            random.seed(seed)
            mock_manager.messages.clear()

            game = make_game({
                "a": "librarian", "b": "butler",
                "c": "spy", "d": "imp",
            })
            with patch.object(type(game), "_MISREGISTER_CHANCE", 1.0):
                await game._action_librarian("a")

            msgs = night_infos(mock_manager, "a")
            assert len(msgs) == 1, f"seed={seed}: expected 1 msg"
            data = msgs[0]

            assert data["role_id"] == "butler", (
                f"seed={seed}: shown_role should be butler (the only real outsider), "
                f"got {data['role_id']}"
            )

            shown_ids = {data["player1"]["id"], data["player2"]["id"]}
            assert "b" in shown_ids, (
                f"seed={seed}: real outsider 'b' must be in the pair"
            )

    async def test_spy_can_appear_as_decoy(self, mock_manager):
        spy_appeared = False
        for seed in range(100):
            random.seed(seed)
            mock_manager.messages.clear()

            game = make_game({
                "a": "librarian", "b": "butler",
                "c": "spy", "d": "imp",
            })
            with patch.object(type(game), "_MISREGISTER_CHANCE", 1.0):
                await game._action_librarian("a")

            data = night_infos(mock_manager, "a")[0]
            shown_ids = {data["player1"]["id"], data["player2"]["id"]}
            if "c" in shown_ids:
                spy_appeared = True
                break

        assert spy_appeared, "Spy should appear as decoy at least once in 100 seeds"


# ======================================================================
# Investigator
# ======================================================================

class TestInvestigator:

    async def test_shows_real_minion_with_correct_role(self, mock_manager):
        game = make_game({
            "a": "investigator", "b": "chef", "c": "empath",
            "d": "poisoner", "e": "imp",
        })
        await game._action_investigator("a")

        msgs = night_infos(mock_manager, "a")
        assert len(msgs) == 1
        data = msgs[0]
        assert data["info_type"] == "investigator_result"
        assert data["role_id"] == "poisoner"

        shown_ids = {data["player1"]["id"], data["player2"]["id"]}
        assert "d" in shown_ids

    async def test_poisoned_gives_arbitrary_info(self, mock_manager):
        game = make_game({
            "a": "investigator", "b": "chef", "c": "empath",
            "d": "poisoner", "e": "imp",
        })
        poison(game, "a")

        await game._action_investigator("a")

        msgs = night_infos(mock_manager, "a")
        assert len(msgs) == 1

    async def test_drunk_gives_arbitrary_info(self, mock_manager):
        game = make_game({
            "a": "investigator", "b": "chef", "c": "empath",
            "d": "poisoner", "e": "imp",
        })
        game.players["a"].drunk = True

        await game._action_investigator("a")

        msgs = night_infos(mock_manager, "a")
        assert len(msgs) == 1

    async def test_recluse_never_chosen_as_real_minion(self, mock_manager):
        """Recluse misregistration only places Recluse in the decoy slot, not as the real minion."""
        for seed in range(100):
            random.seed(seed)
            mock_manager.messages.clear()

            game = make_game({
                "a": "investigator", "b": "recluse",
                "c": "poisoner", "d": "imp",
            })
            with patch.object(type(game), "_MISREGISTER_CHANCE", 1.0):
                await game._action_investigator("a")

            msgs = night_infos(mock_manager, "a")
            assert len(msgs) == 1, f"seed={seed}: expected 1 msg"
            data = msgs[0]

            assert data["role_id"] == "poisoner", (
                f"seed={seed}: shown_role should be poisoner (the only real minion), "
                f"got {data['role_id']}"
            )

            shown_ids = {data["player1"]["id"], data["player2"]["id"]}
            assert "c" in shown_ids, (
                f"seed={seed}: real minion 'c' must be in the pair"
            )

    async def test_recluse_can_appear_as_decoy(self, mock_manager):
        """With MISREGISTER_CHANCE=1.0, Recluse should sometimes be the decoy."""
        recluse_appeared = False
        for seed in range(100):
            random.seed(seed)
            mock_manager.messages.clear()

            game = make_game({
                "a": "investigator", "b": "recluse",
                "c": "poisoner", "d": "imp",
            })
            with patch.object(type(game), "_MISREGISTER_CHANCE", 1.0):
                await game._action_investigator("a")

            data = night_infos(mock_manager, "a")[0]
            shown_ids = {data["player1"]["id"], data["player2"]["id"]}
            if "b" in shown_ids:
                recluse_appeared = True
                break

        assert recluse_appeared, "Recluse should appear as decoy at least once in 100 seeds"

    async def test_multiple_minions_picks_one(self, mock_manager):
        """With multiple minions, one real minion is always in the pair."""
        game = make_game({
            "a": "investigator", "b": "chef",
            "c": "poisoner", "d": "spy", "e": "imp",
        })
        with patch.object(type(game), "_MISREGISTER_CHANCE", 0.0):
            await game._action_investigator("a")

        msgs = night_infos(mock_manager, "a")
        data = msgs[0]
        shown_ids = {data["player1"]["id"], data["player2"]["id"]}
        real_minions = {"c", "d"}
        assert len(shown_ids & real_minions) >= 1
        assert data["role_id"] in ("poisoner", "spy")
