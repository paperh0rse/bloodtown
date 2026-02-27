"""Core game engine – state machine, role assignment, night/day logic."""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any

from server.models import (
    Alignment,
    DaySubPhase,
    GamePhase,
    PlayerState,
    PLAYER_DISTRIBUTION,
    Team,
)
from server.role_data import ROLE_BY_ID, get_roles_by_team
from server.ws_manager import manager

NIGHT_ACTION_TIMEOUT = 60  # seconds


class Game:
    """Represents one game session inside a room."""

    def __init__(self, room_code: str) -> None:
        self.room_code = room_code
        self.phase: GamePhase = GamePhase.LOBBY
        self.day_sub: DaySubPhase = DaySubPhase.ANNOUNCE
        self.day_number: int = 0
        self.players: dict[str, PlayerState] = {}
        self.seat_order: list[str] = []  # player_ids in seat order
        self.host_id: str = ""

        # Night state
        self._pending_action: asyncio.Event | None = None
        self._action_response: dict[str, Any] = {}
        self._signal_events: dict[str, asyncio.Event] = {}
        self._night_deaths: list[str] = []  # player_ids killed tonight
        self._executed_today: str = ""  # player_id executed today

        # Demon bluffs (3 good roles not in play)
        self.demon_bluffs: list[str] = []

        # Game log (public events)
        self.log: list[dict[str, Any]] = []

        # Nomination tracking
        self._nominated_today: set[str] = set()
        self._nominators_today: set[str] = set()
        self._current_nominee: str = ""
        self._votes: dict[str, bool] = {}
        self._vote_eligible_count: int = 0
        self._vote_tally: dict[str, int] = {}  # nominee -> vote count
        self._highest_vote: tuple[str, int] = ("", 0)
        self._nominations_remaining: int = 0
        self._nomination_records: list[dict[str, Any]] = []
        self._end_nom_proposer: str = ""
        self._end_nom_votes: dict[str, bool] = {}  # player_id -> agree
        self._speech_player: str = ""
        self._day_speech_order: dict[str, Any] = {}
        self._last_night_msg: str = ""

        self._game_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Player management
    # ------------------------------------------------------------------

    def add_player(self, player_id: str, name: str) -> PlayerState:
        if player_id in self.players:
            self.players[player_id].name = name
            return self.players[player_id]
        p = PlayerState(player_id=player_id, name=name, seat=len(self.seat_order))
        self.players[player_id] = p
        self.seat_order.append(player_id)
        if not self.host_id:
            self.host_id = player_id
        return p

    def add_bot(self, name: str) -> PlayerState:
        bot_id = f"bot_{len([p for p in self.players.values() if p.is_bot]) + 1}"
        p = PlayerState(player_id=bot_id, name=name, seat=len(self.seat_order), is_bot=True)
        self.players[bot_id] = p
        self.seat_order.append(bot_id)
        return p

    def remove_player(self, player_id: str) -> None:
        self.players.pop(player_id, None)
        if player_id in self.seat_order:
            self.seat_order.remove(player_id)
        if self.host_id == player_id and self.seat_order:
            self.host_id = self.seat_order[0]

    def player_count(self) -> int:
        return len(self.seat_order)

    def alive_players(self) -> list[PlayerState]:
        return [self.players[pid] for pid in self.seat_order if self.players[pid].alive]

    def alive_count(self) -> int:
        return sum(1 for p in self.players.values() if p.alive)

    # ------------------------------------------------------------------
    # Seat helpers
    # ------------------------------------------------------------------

    def _alive_neighbours(self, player_id: str) -> tuple[PlayerState | None, PlayerState | None]:
        """Return the closest alive neighbour on each side (clockwise wrap)."""
        idx = self.seat_order.index(player_id)
        n = len(self.seat_order)

        left: PlayerState | None = None
        for i in range(1, n):
            cand = self.players[self.seat_order[(idx - i) % n]]
            if cand.alive and cand.player_id != player_id:
                left = cand
                break

        right: PlayerState | None = None
        for i in range(1, n):
            cand = self.players[self.seat_order[(idx + i) % n]]
            if cand.alive and cand.player_id != player_id:
                right = cand
                break

        return left, right

    # ------------------------------------------------------------------
    # Role assignment
    # ------------------------------------------------------------------

    def assign_roles(self) -> None:
        n = self.player_count()
        if n not in PLAYER_DISTRIBUTION:
            raise ValueError(f"Unsupported player count: {n}")

        tf_count, out_count, min_count, dem_count = PLAYER_DISTRIBUTION[n]

        townsfolk_pool = get_roles_by_team(Team.TOWNSFOLK)
        outsider_pool = get_roles_by_team(Team.OUTSIDER)
        minion_pool = get_roles_by_team(Team.MINION)
        demon_pool = get_roles_by_team(Team.DEMON)

        chosen_demons = random.sample(demon_pool, dem_count)
        chosen_minions = random.sample(minion_pool, min_count)

        # Baron modifies distribution
        has_baron = any(r.id == "baron" for r in chosen_minions)
        if has_baron:
            out_count = min(out_count + 2, len(outsider_pool))
            tf_count = n - out_count - min_count - dem_count

        chosen_outsiders = random.sample(outsider_pool, out_count)
        chosen_townsfolk = random.sample(townsfolk_pool, tf_count)

        all_roles = chosen_townsfolk + chosen_outsiders + chosen_minions + chosen_demons
        random.shuffle(all_roles)

        # Determine roles NOT in play (for demon bluffs)
        in_play_ids = {r.id for r in all_roles}
        good_not_in_play = [
            r.id for r in townsfolk_pool + outsider_pool if r.id not in in_play_ids
        ]
        random.shuffle(good_not_in_play)
        self.demon_bluffs = good_not_in_play[:3]

        # Assign to players
        for i, pid in enumerate(self.seat_order):
            role = all_roles[i]
            p = self.players[pid]
            p.role_id = role.id
            p.apparent_role_id = ""
            p.alignment = Alignment.EVIL if role.team in (Team.MINION, Team.DEMON) else Alignment.GOOD
            p.alive = True
            p.has_vote_token = True
            p.poisoned = False
            p.drunk = False
            p.protected = False
            p.used_ability = False
            p.butler_master_id = ""
            p.fortune_teller_red_herring = ""

        # Handle Drunk: thinks they are a random townsfolk not in play
        for pid in self.seat_order:
            p = self.players[pid]
            if p.role_id == "drunk":
                p.drunk = True
                available = [r for r in good_not_in_play if r not in [b for b in self.demon_bluffs]]
                if not available:
                    available = [r.id for r in townsfolk_pool if r.id not in in_play_ids]
                if available:
                    p.apparent_role_id = random.choice(available)
                else:
                    p.apparent_role_id = random.choice([r.id for r in townsfolk_pool])

        # Fortune Teller red herring
        for pid in self.seat_order:
            p = self.players[pid]
            if p.role_id == "fortune_teller" or (p.drunk and p.apparent_role_id == "fortune_teller"):
                good_players = [
                    pp.player_id for pp in self.players.values()
                    if pp.alignment == Alignment.GOOD and pp.player_id != pid
                ]
                if good_players:
                    p.fortune_teller_red_herring = random.choice(good_players)

    # ------------------------------------------------------------------
    # Public state snapshots
    # ------------------------------------------------------------------

    def public_state(self) -> dict[str, Any]:
        players = []
        for pid in self.seat_order:
            p = self.players[pid]
            players.append({
                "id": p.player_id,
                "name": p.name,
                "seat": p.seat,
                "alive": p.alive,
                "has_vote_token": p.has_vote_token,
                "is_bot": p.is_bot,
            })
        end_nom_vote = None
        if self._end_nom_proposer:
            proposer = self.players.get(self._end_nom_proposer)
            end_nom_vote = {
                "proposer_id": self._end_nom_proposer,
                "proposer_name": proposer.name if proposer else "???",
                "proposer_seat": (proposer.seat + 1) if proposer else 0,
                "votes": {pid: v for pid, v in self._end_nom_votes.items()},
                "needed": len([p for p in self.players.values() if p.alive and not p.is_bot]),
            }

        speech_info = None
        if self._speech_player:
            sp = self.players.get(self._speech_player)
            speech_info = {
                "player_id": self._speech_player,
                "player_name": sp.name if sp else "???",
                "player_seat": (sp.seat + 1) if sp else 0,
            }

        return {
            "phase": self.phase.value,
            "day_sub": self.day_sub.value if self.phase in (GamePhase.DAY, GamePhase.FIRST_NIGHT, GamePhase.NIGHT) else "",
            "day_number": self.day_number,
            "players": players,
            "host_id": self.host_id,
            "alive_count": self.alive_count(),
            "nominations_remaining": self._nominations_remaining,
            "nomination_records": self._nomination_records,
            "end_nomination_vote": end_nom_vote,
            "speech": speech_info,
            "speech_order": self._day_speech_order if self.phase == GamePhase.DAY else None,
            "nominators_today": list(self._nominators_today),
            "nominated_today": list(self._nominated_today),
            "log": self.log[-20:],
        }

    def private_state(self, player_id: str) -> dict[str, Any]:
        p = self.players[player_id]
        role_id = p.effective_role_id()
        role_def = ROLE_BY_ID.get(role_id)
        return {
            "role_id": role_id,
            "role_name": role_def.name_zh if role_def else "???",
            "role_ability": role_def.ability_zh if role_def else "",
            "team": role_def.team.value if role_def else "",
            "alignment": p.alignment.value,
            "alive": p.alive,
            "has_vote_token": p.has_vote_token,
            "used_ability": p.used_ability,
            "butler_master_id": p.butler_master_id,
        }

    # ------------------------------------------------------------------
    # Start game
    # ------------------------------------------------------------------

    async def start_game(self) -> None:
        random.shuffle(self.seat_order)
        for i, pid in enumerate(self.seat_order):
            self.players[pid].seat = i

        self.assign_roles()
        self.phase = GamePhase.SETUP

        for pid in self.seat_order:
            await manager.send_personal(self.room_code, pid, "role_assigned", self.private_state(pid))

        await manager.broadcast(self.room_code, "game_state", self.public_state())
        self._add_log("game_start", "游戏开始！")

        # Start the game loop
        self._game_task = asyncio.create_task(self._game_loop())

    async def _game_loop(self) -> None:
        try:
            await self._run_first_night()
            while self.phase != GamePhase.GAME_OVER:
                await self._run_day()
                if self.phase == GamePhase.GAME_OVER:
                    break
                await self._run_night()
                if self.phase == GamePhase.GAME_OVER:
                    break
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self._add_log("error", f"游戏引擎错误: {e}")
            await manager.broadcast(self.room_code, "error", {"message": str(e)})

    # ------------------------------------------------------------------
    # First Night
    # ------------------------------------------------------------------

    async def _run_first_night(self) -> None:
        self.phase = GamePhase.FIRST_NIGHT
        self.day_number = 0
        self._night_deaths = []
        self._add_log("phase", "第一个夜晚降临了...")
        await manager.broadcast(self.room_code, "game_state", self.public_state())
        await asyncio.sleep(1)

        await self._send_minion_info()
        await self._send_demon_info()

        # Clear poison from previous (none on first night, but reset)
        for p in self.players.values():
            p.poisoned = False
            p.protected = False

        # Process first-night actions in order
        await self._process_night_actions(first_night=True)

    async def _send_minion_info(self) -> None:
        demon_pid = self._find_demon_player()
        minion_pids = self._find_minion_players()
        if not demon_pid:
            return

        demon_p = self.players[demon_pid]
        for mpid in minion_pids:
            fellow = [self._ptag(self.players[mid]) for mid in minion_pids if mid != mpid]
            fellow_str = f"，同伴爪牙：{', '.join(fellow)}" if fellow else ""
            await manager.send_personal(self.room_code, mpid, "night_info", {
                "info_type": "minion_info",
                "message": f"你的恶魔是 {self._ptag(demon_p)}{fellow_str}",
            })

        await asyncio.sleep(2)

    async def _send_demon_info(self) -> None:
        demon_pid = self._find_demon_player()
        minion_pids = self._find_minion_players()
        if not demon_pid:
            return

        minion_names = ", ".join(self._ptag(self.players[mid]) for mid in minion_pids) or "无"
        await manager.send_personal(self.room_code, demon_pid, "night_info", {
            "info_type": "demon_info",
            "message": f"你的爪牙：{minion_names}",
        })

        await asyncio.sleep(2)

    # ------------------------------------------------------------------
    # Regular Night
    # ------------------------------------------------------------------

    async def _run_night(self) -> None:
        self.phase = GamePhase.NIGHT
        self._night_deaths = []
        self._add_log("phase", f"第 {self.day_number} 个夜晚降临了...")
        await manager.broadcast(self.room_code, "game_state", self.public_state())
        await asyncio.sleep(1)

        # Reset per-night states
        for p in self.players.values():
            p.poisoned = False
            p.protected = False

        await self._process_night_actions(first_night=False)

    # ------------------------------------------------------------------
    # Night action processing
    # ------------------------------------------------------------------

    async def _process_night_actions(self, first_night: bool) -> None:
        order_attr = "first_night_order" if first_night else "other_night_order"

        acting_players: list[tuple[int, str]] = []
        for pid in self.seat_order:
            p = self.players[pid]
            if not p.alive:
                continue
            role_def = ROLE_BY_ID.get(p.role_id)
            if not role_def:
                continue
            order_val = getattr(role_def, order_attr, 0)
            if order_val > 0:
                acting_players.append((order_val, pid))

        acting_players.sort(key=lambda x: x[0])

        for _, pid in acting_players:
            p = self.players[pid]
            role_id = p.role_id

            if role_id == "poisoner":
                await self._action_poisoner(pid, first_night)
            elif role_id == "monk" and not first_night:
                await self._action_monk(pid)
            elif role_id == "imp" and not first_night:
                await self._action_imp(pid)
            elif role_id == "ravenkeeper" and not first_night:
                await self._action_ravenkeeper(pid)
            elif role_id == "washerwoman" and first_night:
                await self._action_washerwoman(pid)
            elif role_id == "librarian" and first_night:
                await self._action_librarian(pid)
            elif role_id == "investigator" and first_night:
                await self._action_investigator(pid)
            elif role_id == "chef" and first_night:
                await self._action_chef(pid)
            elif role_id == "empath":
                await self._action_empath(pid)
            elif role_id == "fortune_teller":
                await self._action_fortune_teller(pid)
            elif role_id == "undertaker" and not first_night:
                await self._action_undertaker(pid)
            elif role_id == "butler":
                await self._action_butler(pid)
            elif role_id == "spy":
                await self._action_spy(pid)
            elif role_id == "scarlet_woman" and not first_night:
                pass  # passive, handled in imp death

        # Apply night deaths
        if not first_night:
            await self._resolve_night_deaths()

    # ------------------------------------------------------------------
    # Individual role actions
    # ------------------------------------------------------------------

    async def _action_poisoner(self, pid: str, first_night: bool) -> None:
        p = self.players[pid]
        if not p.alive:
            return
        targets = [pp for pp in self.alive_players() if pp.player_id != pid]
        target_id = await self._ask_player_choose(
            pid, "poison_target",
            "选择一名玩家进行投毒（该玩家今晚和明天中毒）：",
            [{"id": t.player_id, "name": f"{t.seat+1}号 {t.name}"} for t in targets],
        )
        if target_id and target_id in self.players:
            self.players[target_id].poisoned = True

    async def _action_monk(self, pid: str) -> None:
        p = self.players[pid]
        if not p.alive or p.poisoned or p.drunk:
            return
        targets = [pp for pp in self.alive_players() if pp.player_id != pid]
        target_id = await self._ask_player_choose(
            pid, "monk_protect",
            "选择一名其他玩家进行保护（今晚免受恶魔攻击）：",
            [{"id": t.player_id, "name": f"{t.seat+1}号 {t.name}"} for t in targets],
        )
        if target_id and target_id in self.players:
            if not (p.poisoned or p.drunk):
                self.players[target_id].protected = True

    async def _action_imp(self, pid: str) -> None:
        p = self.players[pid]
        if not p.alive:
            return
        all_players = [pp for pp in self.players.values()]
        target_id = await self._ask_player_choose(
            pid, "imp_kill",
            "选择一名玩家进行攻击：",
            [{"id": t.player_id, "name": f"{t.seat+1}号 {t.name}"} for t in all_players],
        )
        if not target_id or target_id not in self.players:
            return

        target = self.players[target_id]

        # Imp kills self -> minion becomes Imp
        if target_id == pid:
            self._night_deaths.append(pid)
            alive_minions = [
                pp for pp in self.players.values()
                if pp.alive and pp.player_id != pid
                and ROLE_BY_ID.get(pp.role_id, None) is not None
                and ROLE_BY_ID[pp.role_id].team == Team.MINION
            ]
            if alive_minions:
                new_imp = random.choice(alive_minions)
                new_imp.role_id = "imp"
                new_imp.alignment = Alignment.EVIL
                await manager.send_personal(self.room_code, new_imp.player_id, "night_info", {
                    "info_type": "became_demon",
                    "message": "小恶魔自杀了，你现在是新的小恶魔！",
                })
            return

        # Check Soldier immunity
        if target.role_id == "soldier" and not target.poisoned and not target.drunk:
            return

        # Check Monk protection
        if target.protected:
            return

        # Check Mayor redirect
        if target.role_id == "mayor" and not target.poisoned and not target.drunk:
            others = [pp for pp in self.alive_players()
                      if pp.player_id != target_id and pp.player_id != pid]
            if others:
                substitute = random.choice(others)
                self._night_deaths.append(substitute.player_id)
                return

        if target.alive:
            self._night_deaths.append(target_id)

    async def _action_ravenkeeper(self, pid: str) -> None:
        p = self.players[pid]
        if pid not in self._night_deaths:
            return
        targets = [pp for pp in self.players.values() if pp.player_id != pid]
        target_id = await self._ask_player_choose(
            pid, "ravenkeeper_learn",
            "你在夜间死亡了！选择一名玩家查看其角色：",
            [{"id": t.player_id, "name": f"{t.seat+1}号 {t.name}"} for t in targets],
        )
        if target_id and target_id in self.players:
            target = self.players[target_id]
            shown_role = target.role_id
            if p.poisoned or p.drunk:
                all_role_ids = list(ROLE_BY_ID.keys())
                shown_role = random.choice(all_role_ids)
            # Spy may register as good, Recluse may register as evil
            shown_role = self._apply_spy_recluse_registration(target, shown_role)
            role_def = ROLE_BY_ID.get(shown_role)
            await manager.send_personal(self.room_code, pid, "night_info", {
                "info_type": "ravenkeeper_result",
                "message": f"{self._ptag(target)} 的角色是 {role_def.name_zh if role_def else shown_role}。",
                "target_name": target.name,
                "role_id": shown_role,
                "role_name": role_def.name_zh if role_def else shown_role,
            })

    async def _action_washerwoman(self, pid: str) -> None:
        p = self.players[pid]
        is_drunk_or_poisoned = p.poisoned or p.drunk

        townsfolk_players = [
            pp for pp in self.players.values()
            if pp.player_id != pid and ROLE_BY_ID.get(pp.role_id) is not None
            and ROLE_BY_ID[pp.role_id].team == Team.TOWNSFOLK
        ]

        if is_drunk_or_poisoned or not townsfolk_players:
            # Give false info
            all_others = [pp for pp in self.players.values() if pp.player_id != pid]
            if len(all_others) >= 2:
                pair = random.sample(all_others, 2)
                fake_role = random.choice(get_roles_by_team(Team.TOWNSFOLK))
                await self._send_two_player_info(pid, "washerwoman", pair[0], pair[1], fake_role.id)
            return

        real_tf = random.choice(townsfolk_players)
        others = [pp for pp in self.players.values()
                  if pp.player_id not in (pid, real_tf.player_id)]
        wrong = random.choice(others) if others else real_tf
        shown_role = real_tf.role_id

        # Spy can register as townsfolk
        if wrong.role_id == "spy" and random.random() < 0.5:
            shown_role = real_tf.role_id  # spy registers as the shown townsfolk

        await self._send_two_player_info(pid, "washerwoman", real_tf, wrong, shown_role)

    async def _action_librarian(self, pid: str) -> None:
        p = self.players[pid]
        is_drunk_or_poisoned = p.poisoned or p.drunk

        outsider_players = [
            pp for pp in self.players.values()
            if pp.player_id != pid and ROLE_BY_ID.get(pp.role_id) is not None
            and ROLE_BY_ID[pp.role_id].team == Team.OUTSIDER
            and pp.role_id != "drunk"  # Drunk appears as townsfolk to themselves
        ]

        if is_drunk_or_poisoned:
            all_others = [pp for pp in self.players.values() if pp.player_id != pid]
            if len(all_others) >= 2:
                pair = random.sample(all_others, 2)
                fake_role = random.choice(get_roles_by_team(Team.OUTSIDER))
                await self._send_two_player_info(pid, "librarian", pair[0], pair[1], fake_role.id)
            return

        if not outsider_players:
            await manager.send_personal(self.room_code, pid, "night_info", {
                "info_type": "librarian_result",
                "message": "没有外来者在场。",
                "player1": None, "player2": None, "role_id": None,
            })
            return

        real_out = random.choice(outsider_players)
        others = [pp for pp in self.players.values()
                  if pp.player_id not in (pid, real_out.player_id)]
        wrong = random.choice(others) if others else real_out
        await self._send_two_player_info(pid, "librarian", real_out, wrong, real_out.role_id)

    async def _action_investigator(self, pid: str) -> None:
        p = self.players[pid]
        is_drunk_or_poisoned = p.poisoned or p.drunk

        minion_players = [
            pp for pp in self.players.values()
            if pp.player_id != pid and ROLE_BY_ID.get(pp.role_id) is not None
            and ROLE_BY_ID[pp.role_id].team == Team.MINION
        ]

        if is_drunk_or_poisoned or not minion_players:
            all_others = [pp for pp in self.players.values() if pp.player_id != pid]
            if len(all_others) >= 2:
                pair = random.sample(all_others, 2)
                fake_role = random.choice(get_roles_by_team(Team.MINION))
                await self._send_two_player_info(pid, "investigator", pair[0], pair[1], fake_role.id)
            return

        real_min = random.choice(minion_players)
        others = [pp for pp in self.players.values()
                  if pp.player_id not in (pid, real_min.player_id)]
        wrong = random.choice(others) if others else real_min
        shown_role = real_min.role_id

        # Recluse might register as minion
        if wrong.role_id == "recluse" and random.random() < 0.3:
            shown_role = real_min.role_id

        await self._send_two_player_info(pid, "investigator", real_min, wrong, shown_role)

    async def _action_chef(self, pid: str) -> None:
        p = self.players[pid]
        is_drunk_or_poisoned = p.poisoned or p.drunk

        if is_drunk_or_poisoned:
            count = random.randint(0, 2)
        else:
            count = 0
            for i, spid in enumerate(self.seat_order):
                next_pid = self.seat_order[(i + 1) % len(self.seat_order)]
                sp = self.players[spid]
                np = self.players[next_pid]
                if self._registers_as_evil(sp) and self._registers_as_evil(np):
                    count += 1

        await manager.send_personal(self.room_code, pid, "night_info", {
            "info_type": "chef_result",
            "message": f"有 {count} 对邪恶玩家相邻而坐。",
            "count": count,
        })

    async def _action_empath(self, pid: str) -> None:
        p = self.players[pid]
        is_drunk_or_poisoned = p.poisoned or p.drunk

        left, right = self._alive_neighbours(pid)
        if is_drunk_or_poisoned:
            count = random.randint(0, 2)
        else:
            count = 0
            for nb in (left, right):
                if nb and self._registers_as_evil(nb):
                    count += 1

        await manager.send_personal(self.room_code, pid, "night_info", {
            "info_type": "empath_result",
            "message": f"你的存活邻居中有 {count} 个邪恶玩家。",
            "count": count,
        })

    async def _action_fortune_teller(self, pid: str) -> None:
        p = self.players[pid]
        targets = [pp for pp in self.players.values() if pp.player_id != pid]
        chosen = await self._ask_player_choose_two(
            pid, "fortune_teller_pick",
            "选择2名玩家进行占卜（得知其中是否有恶魔）：",
            [{"id": t.player_id, "name": f"{t.seat+1}号 {t.name}"} for t in targets],
        )

        if not chosen or len(chosen) < 2:
            # Auto-pick
            if len(targets) >= 2:
                auto = random.sample(targets, 2)
                chosen = [auto[0].player_id, auto[1].player_id]
            else:
                return

        is_drunk_or_poisoned = p.poisoned or p.drunk

        if is_drunk_or_poisoned:
            has_demon = random.choice([True, False])
        else:
            has_demon = False
            for cid in chosen:
                cp = self.players.get(cid)
                if not cp:
                    continue
                if cp.role_id == "imp" or (
                    cp.role_id == "recluse" and random.random() < 0.3
                ):
                    has_demon = True
                if cp.player_id == p.fortune_teller_red_herring:
                    has_demon = True

        names = [self._ptag(self.players[c]) for c in chosen if c in self.players]
        await manager.send_personal(self.room_code, pid, "night_info", {
            "info_type": "fortune_teller_result",
            "message": f"{'、'.join(names)} 中{'有' if has_demon else '没有'}恶魔。",
            "has_demon": has_demon,
            "targets": chosen,
        })

    async def _action_undertaker(self, pid: str) -> None:
        p = self.players[pid]
        if not self._executed_today:
            return
        executed = self.players.get(self._executed_today)
        if not executed:
            return

        is_drunk_or_poisoned = p.poisoned or p.drunk
        if is_drunk_or_poisoned:
            shown_role = random.choice(list(ROLE_BY_ID.keys()))
        else:
            shown_role = executed.role_id
            shown_role = self._apply_spy_recluse_registration(executed, shown_role)

        role_def = ROLE_BY_ID.get(shown_role)
        await manager.send_personal(self.room_code, pid, "night_info", {
            "info_type": "undertaker_result",
            "message": f"今天被处决的 {self._ptag(executed)} 的角色是 {role_def.name_zh if role_def else shown_role}。",
            "executed_name": executed.name,
            "role_id": shown_role,
            "role_name": role_def.name_zh if role_def else shown_role,
        })

    async def _action_butler(self, pid: str) -> None:
        p = self.players[pid]
        targets = [pp for pp in self.alive_players() if pp.player_id != pid]
        target_id = await self._ask_player_choose(
            pid, "butler_master",
            "选择一名玩家作为你的主人（明天投票时你只能跟随主人投票）：",
            [{"id": t.player_id, "name": f"{t.seat+1}号 {t.name}"} for t in targets],
        )
        if target_id and target_id in self.players:
            p.butler_master_id = target_id

    async def _action_spy(self, pid: str) -> None:
        p = self.players[pid]
        if not p.alive:
            return
        # Spy sees the grimoire
        grimoire = []
        for spid in self.seat_order:
            sp = self.players[spid]
            role_def = ROLE_BY_ID.get(sp.role_id)
            grimoire.append({
                "seat": sp.seat + 1,
                "name": sp.name,
                "role_id": sp.role_id,
                "role_name": role_def.name_zh if role_def else "???",
                "alignment": sp.alignment.value,
                "alive": sp.alive,
            })
        await manager.send_personal(self.room_code, pid, "night_info", {
            "info_type": "spy_grimoire",
            "message": "你查看了魔典，以下是所有玩家的角色：",
            "grimoire": grimoire,
        })

    # ------------------------------------------------------------------
    # Night death resolution
    # ------------------------------------------------------------------

    async def _resolve_night_deaths(self) -> None:
        # Deduplicate
        unique_deaths = list(dict.fromkeys(self._night_deaths))
        dead_names = []
        for dpid in unique_deaths:
            dp = self.players.get(dpid)
            if dp and dp.alive:
                dp.alive = False
                dead_names.append(dp.name)

                # Ravenkeeper triggers on night death
                if dp.role_id == "ravenkeeper" and not dp.poisoned and not dp.drunk:
                    await self._action_ravenkeeper(dpid)

        dead_tags = [self._ptag(self.players[d]) for d in unique_deaths if self.players.get(d)]
        if dead_tags:
            msg = f"昨夜，{'、'.join(dead_tags)} 死亡了。"
        else:
            msg = "昨夜平安无事，没有人死亡。"
        self._last_night_msg = msg
        self._add_log("death", msg)
        await manager.broadcast(self.room_code, "night_result", {"message": msg, "deaths": unique_deaths})

        # Check win conditions
        demon_alive = any(
            p.alive and ROLE_BY_ID.get(p.role_id) is not None
            and ROLE_BY_ID[p.role_id].team == Team.DEMON
            for p in self.players.values()
        )
        if not demon_alive:
            # Check Scarlet Woman
            scarlet = self._find_scarlet_woman()
            if scarlet and self.alive_count() >= 5:
                scarlet.role_id = "imp"
                await manager.send_personal(self.room_code, scarlet.player_id, "night_info", {
                    "info_type": "became_demon",
                    "message": "恶魔已死，你现在是新的小恶魔！",
                })
            else:
                await self._end_game("good", "恶魔已被消灭，好人阵营获胜！")
                return

        if self.alive_count() <= 2:
            await self._end_game("evil", "仅剩2名玩家存活，邪恶阵营获胜！")

    # ------------------------------------------------------------------
    # Day phase
    # ------------------------------------------------------------------

    async def _run_day(self) -> None:
        self.day_number += 1
        self.phase = GamePhase.DAY
        self.day_sub = DaySubPhase.NOMINATION
        self._executed_today = ""
        self._nominated_today = set()
        self._nominators_today = set()
        self._current_nominee = ""
        self._votes = {}
        self._vote_tally = {}
        self._highest_vote = ("", 0)
        self._nominations_remaining = self.alive_count()
        self._nomination_records = []
        self._end_nom_proposer = ""
        self._end_nom_votes = {}
        self._speech_player = ""
        self._pending_nomination: tuple[str, str] | None = None

        alive_seats = [pid for pid in self.seat_order if self.players[pid].alive]
        start_pid = random.choice(alive_seats)
        direction = random.choice(["顺时针", "逆时针"])
        start_p = self.players[start_pid]
        speech_msg = f"从 {self._ptag(start_p)} 开始{direction}发言"
        self._day_speech_order = {
            "start_seat": start_p.seat + 1,
            "start_name": start_p.name,
            "direction": direction,
            "message": speech_msg,
        }
        self._add_log("phase", f"第 {self.day_number} 天 — 天亮了！{speech_msg}。")
        await manager.broadcast(self.room_code, "game_state", self.public_state())

        day_broadcast = self._last_night_msg + "  " + speech_msg if self._last_night_msg else speech_msg
        self._last_night_msg = ""
        await manager.broadcast(self.room_code, "day_announce", {"message": day_broadcast})

        # Day loop: wait for nominations or end signal
        while self.phase == GamePhase.DAY:
            self.day_sub = DaySubPhase.NOMINATION
            self._end_nom_proposer = ""
            self._end_nom_votes = {}
            self._speech_player = ""
            self._pending_nomination = None
            await manager.broadcast(self.room_code, "game_state", self.public_state())

            # Schedule bot auto-nominate after signal is registered
            bot_task = asyncio.create_task(self._bot_auto_nominate())

            # Wait for a nomination or end_nominations signal
            await self._wait_for_signal("nomination_submitted", timeout=86400)
            bot_task.cancel()

            if self.phase == GamePhase.GAME_OVER:
                return

            # Check if end_nominations was triggered instead of a nomination
            if self._pending_nomination is None:
                break

            nominator_id, nominee_id = self._pending_nomination
            self._pending_nomination = None

            # Skip marker (e.g. Virgin trigger already handled the nomination)
            if nominator_id == "__skip__":
                if self._nominations_remaining <= 0:
                    self._add_log("phase", "所有提名次数已用完。")
                    await manager.broadcast(self.room_code, "game_state", self.public_state())
                    await asyncio.sleep(1)
                    break
                continue

            await self._process_nomination(nominator_id, nominee_id)

            if self.phase == GamePhase.GAME_OVER:
                return

            if self._nominations_remaining <= 0:
                self._add_log("phase", "所有提名次数已用完。")
                await manager.broadcast(self.room_code, "game_state", self.public_state())
                await asyncio.sleep(1)
                break

        if self.phase == GamePhase.GAME_OVER:
            return

        # Already executed someone (e.g. Virgin trigger) → skip normal execution
        if self._executed_today:
            await asyncio.sleep(1)
            return

        # Execute if applicable
        if self._highest_vote[0] and self._highest_vote[1] >= self.alive_count() / 2:
            await self._execute_player(self._highest_vote[0])
        else:
            self._add_log("execution", "今天没有人被处决。")
            await manager.broadcast(self.room_code, "no_execution", {"message": "今天没有人被处决。"})
            mayor_alive = any(
                p.alive and p.role_id == "mayor" and not p.poisoned and not p.drunk
                for p in self.players.values()
            )
            if self.alive_count() == 3 and mayor_alive:
                await self._end_game("good", "市长能力触发：3人存活且无处决，好人阵营获胜！")
                return

        await asyncio.sleep(1)

    async def _process_nomination(self, nominator_id: str, nominee_id: str) -> None:
        """Run a single nomination → speech → speech → vote cycle."""
        nominator = self.players[nominator_id]
        nominee = self.players[nominee_id]

        self._add_log("nomination", f"{self._ptag(nominator)} 提名了 {self._ptag(nominee)}。")
        await manager.broadcast(self.room_code, "nomination", {
            "nominator": nominator_id,
            "nominator_name": nominator.name,
            "nominator_seat": nominator.seat + 1,
            "nominee": nominee_id,
            "nominee_name": nominee.name,
            "nominee_seat": nominee.seat + 1,
        })

        # --- Phase 1: Nominator speech ---
        self.day_sub = DaySubPhase.NOMINATOR_SPEECH
        self._current_nominee = nominee_id
        self._speech_player = nominator_id
        await manager.broadcast(self.room_code, "game_state", self.public_state())
        await manager.broadcast(self.room_code, "speech_start", {
            "speaker_id": nominator_id,
            "speaker_name": nominator.name,
            "speaker_seat": nominator.seat + 1,
            "role": "nominator",
            "message": f"提名者 {self._ptag(nominator)} 发言中...",
        })
        if nominator.is_bot:
            await asyncio.sleep(0.3)
        else:
            await self._wait_for_signal("speech_done", timeout=300)

        if self.phase == GamePhase.GAME_OVER:
            return

        # --- Phase 2: Nominee speech ---
        self.day_sub = DaySubPhase.NOMINEE_SPEECH
        self._speech_player = nominee_id
        await manager.broadcast(self.room_code, "game_state", self.public_state())
        await manager.broadcast(self.room_code, "speech_start", {
            "speaker_id": nominee_id,
            "speaker_name": nominee.name,
            "speaker_seat": nominee.seat + 1,
            "role": "nominee",
            "message": f"被提名者 {self._ptag(nominee)} 发言中...",
        })
        if nominee.is_bot:
            await asyncio.sleep(0.3)
        else:
            await self._wait_for_signal("speech_done", timeout=300)

        if self.phase == GamePhase.GAME_OVER:
            return

        # --- Phase 3: Voting ---
        self.day_sub = DaySubPhase.VOTING
        self._speech_player = ""
        self._vote_eligible_count = sum(
            1 for pp in self.players.values() if pp.alive or pp.has_vote_token
        )
        self._votes = {nominator_id: True}
        await manager.broadcast(self.room_code, "game_state", self.public_state())
        await manager.broadcast(self.room_code, "voting_start", {
            "nominee": nominee_id,
            "nominee_name": nominee.name,
            "nominee_seat": nominee.seat + 1,
            "nominator": nominator_id,
            "message": f"对 {self._ptag(nominee)} 的投票开始！",
        })

        bot_vote_task = asyncio.create_task(self._bot_auto_vote())
        await self._wait_for_signal("voting_complete", timeout=60)
        bot_vote_task.cancel()

        # Butler rule: nominator's auto-yes is voided if butler's master didn't vote yes
        if nominator.role_id == "butler" and nominator.butler_master_id:
            master_voted = self._votes.get(nominator.butler_master_id)
            if not master_voted:
                self._votes[nominator_id] = False

        # Tally
        yes_votes = sum(1 for v in self._votes.values() if v)
        needed = (self.alive_count() + 1) // 2
        self._vote_tally[nominee_id] = yes_votes

        yes_voter_seats = sorted(
            self.players[vid].seat + 1
            for vid, v in self._votes.items() if v and vid in self.players
        )
        self._nomination_records.append({
            "nominator_id": nominator_id,
            "nominator_name": nominator.name,
            "nominator_seat": nominator.seat + 1,
            "nominee_id": nominee_id,
            "nominee_name": nominee.name,
            "nominee_seat": nominee.seat + 1,
            "yes_votes": yes_votes,
            "yes_voter_seats": yes_voter_seats,
            "total_voters": len(self._votes),
            "needed": needed,
            "passed": yes_votes >= needed,
        })

        self._add_log("vote", f"对 {self._ptag(nominee)} 的投票结果：{yes_votes}/{needed} 票赞成。")
        await manager.broadcast(self.room_code, "vote_result", {
            "nominee": nominee_id,
            "nominee_name": nominee.name,
            "nominee_seat": nominee.seat + 1,
            "yes_votes": yes_votes,
            "total_voters": len(self._votes),
            "needed": needed,
        })

        if yes_votes > self._highest_vote[1]:
            self._highest_vote = (nominee_id, yes_votes)
        elif yes_votes == self._highest_vote[1] and self._highest_vote[0]:
            self._highest_vote = ("", yes_votes)

        self._nominations_remaining -= 1
        self.day_sub = DaySubPhase.NOMINATION

    async def _execute_player(self, player_id: str) -> None:
        p = self.players.get(player_id)
        if not p:
            return

        self._executed_today = player_id

        tag = self._ptag(p)
        if p.role_id == "saint" and not p.poisoned and not p.drunk:
            p.alive = False
            self._add_log("execution", f"{tag} 被处决了！")
            await manager.broadcast(self.room_code, "execution", {
                "message": f"{tag} 被处决了！",
                "player_id": player_id,
            })
            await self._end_game("evil", f"圣徒 {tag} 被处决，邪恶阵营获胜！")
            return

        p.alive = False
        self._add_log("execution", f"{tag} 被处决了！")
        await manager.broadcast(self.room_code, "execution", {
            "message": f"{tag} 被处决了！",
            "player_id": player_id,
        })

        # Check demon death first (good wins take priority per rules)
        role_def = ROLE_BY_ID.get(p.role_id)
        if role_def and role_def.team == Team.DEMON:
            scarlet = self._find_scarlet_woman()
            if scarlet and self.alive_count() >= 5:
                scarlet.role_id = "imp"
                await manager.send_personal(self.room_code, scarlet.player_id, "night_info", {
                    "info_type": "became_demon",
                    "message": "恶魔被处决了，你现在是新的小恶魔！",
                })
                self._add_log("event", "恶魔被处决...但邪恶的力量似乎并未消散。")
            else:
                await self._end_game("good", "恶魔被处决，好人阵营获胜！")
                return

        if self.alive_count() <= 2:
            await self._end_game("evil", "仅剩2名玩家存活，邪恶阵营获胜！")

    # ------------------------------------------------------------------
    # Nomination & Voting (called from WS handler)
    # ------------------------------------------------------------------

    async def handle_nominate(self, nominator_id: str, nominee_id: str) -> None:
        """Validate and submit a nomination. Does NOT block for voting."""
        if self.phase != GamePhase.DAY or self.day_sub != DaySubPhase.NOMINATION:
            return
        if self._end_nom_proposer:
            await manager.send_personal(self.room_code, nominator_id, "error", {"message": "正在投票是否结束提名，暂时无法提名。"})
            return
        nominator = self.players.get(nominator_id)
        nominee = self.players.get(nominee_id)
        if not nominator or not nominee:
            return
        if not nominator.alive:
            await manager.send_personal(self.room_code, nominator_id, "error", {"message": "死亡玩家不能提名。"})
            return
        if nominator_id in self._nominators_today:
            await manager.send_personal(self.room_code, nominator_id, "error", {"message": "你今天已经提名过了。"})
            return
        if nominee_id in self._nominated_today:
            await manager.send_personal(self.room_code, nominator_id, "error", {"message": "该玩家今天已被提名过了。"})
            return

        self._nominators_today.add(nominator_id)
        self._nominated_today.add(nominee_id)

        # Virgin ability: townsfolk nominates virgin → nominator is executed, day ends
        if (nominee.role_id == "virgin" and not nominee.used_ability
                and not nominee.poisoned and not nominee.drunk
                and nominator.alive):
            role_def = ROLE_BY_ID.get(nominator.role_id)
            if role_def and role_def.team == Team.TOWNSFOLK:
                nominee.used_ability = True
                nominator.alive = False
                self._add_log("ability", f"贞女能力触发！提名者 {self._ptag(nominator)} 被处决！")
                await manager.broadcast(self.room_code, "virgin_trigger", {
                    "message": f"贞女能力触发！{self._ptag(nominator)} 被处决！",
                    "dead_player": nominator_id,
                })
                self._executed_today = nominator_id
                await manager.broadcast(self.room_code, "game_state", self.public_state())
                # Signal day loop to end (None = break out of day loop)
                self._pending_nomination = None
                self.handle_signal("nomination_submitted")
                return

        # Set pending nomination and wake up the day loop
        self._pending_nomination = (nominator_id, nominee_id)
        self.handle_signal("nomination_submitted")

    async def handle_vote(self, voter_id: str, vote: bool) -> None:
        if self.phase != GamePhase.DAY or self.day_sub != DaySubPhase.VOTING:
            return
        p = self.players.get(voter_id)
        if not p:
            return
        if not p.alive and not p.has_vote_token:
            return
        if voter_id in self._votes:
            return

        # Butler restriction
        if p.role_id == "butler" and p.butler_master_id:
            master_voted = self._votes.get(p.butler_master_id)
            if vote and (master_voted is None or not master_voted):
                await manager.send_personal(self.room_code, voter_id, "error", {
                    "message": "你的主人还未投赞成票，你不能投赞成票。"
                })
                return

        self._votes[voter_id] = vote
        if not p.alive and vote:
            p.has_vote_token = False

        if len(self._votes) >= self._vote_eligible_count:
            self.handle_signal("voting_complete")

    async def handle_propose_end_nominations(self, proposer_id: str) -> None:
        """Any alive player can propose to end nominations early."""
        if self.phase != GamePhase.DAY or self.day_sub != DaySubPhase.NOMINATION:
            return
        p = self.players.get(proposer_id)
        if not p or not p.alive:
            return
        if self._end_nom_proposer:
            await manager.send_personal(self.room_code, proposer_id, "error", {
                "message": "已有一个结束提名的提议正在投票中。",
            })
            return

        self._end_nom_proposer = proposer_id
        self._end_nom_votes = {proposer_id: True}

        # Bots auto-agree
        for pid in self.seat_order:
            bp = self.players[pid]
            if bp.is_bot and bp.alive:
                self._end_nom_votes[pid] = True

        if self._check_end_nom_passed():
            self._add_log("phase", "所有玩家同意结束提名，即将进入夜晚。")
            self._end_nom_proposer = ""
            self._end_nom_votes = {}
            await manager.broadcast(self.room_code, "game_state", self.public_state())
            await asyncio.sleep(0.5)
            self._pending_nomination = None
            self.handle_signal("nomination_submitted")
        else:
            self._add_log("event", f"{self._ptag(p)} 提议结束提名，等待所有玩家同意。")
            await manager.broadcast(self.room_code, "game_state", self.public_state())
            await manager.broadcast(self.room_code, "end_nom_proposal", {
                "proposer_id": proposer_id,
                "proposer_name": p.name,
                "proposer_seat": p.seat + 1,
                "message": f"{self._ptag(p)} 提议结束提名进入夜晚，是否同意？",
            })

    async def handle_vote_end_nominations(self, voter_id: str, agree: bool) -> None:
        if not self._end_nom_proposer:
            return
        p = self.players.get(voter_id)
        if not p or not p.alive:
            return
        if voter_id in self._end_nom_votes:
            return

        self._end_nom_votes[voter_id] = agree

        if not agree:
            self._add_log("event", f"{self._ptag(p)} 拒绝了结束提名的提议。")
            self._end_nom_proposer = ""
            self._end_nom_votes = {}
            await manager.broadcast(self.room_code, "end_nom_rejected", {
                "message": f"{self._ptag(p)} 拒绝了结束提名的提议，继续提名阶段。",
            })
            await manager.broadcast(self.room_code, "game_state", self.public_state())
            return

        if self._check_end_nom_passed():
            self._add_log("phase", "所有玩家同意结束提名，即将进入夜晚。")
            self._end_nom_proposer = ""
            self._end_nom_votes = {}
            await manager.broadcast(self.room_code, "game_state", self.public_state())
            await asyncio.sleep(0.5)
            self._pending_nomination = None
            self.handle_signal("nomination_submitted")
        else:
            await manager.broadcast(self.room_code, "game_state", self.public_state())

    def _check_end_nom_passed(self) -> bool:
        alive_humans = [pp for pp in self.players.values() if pp.alive and not pp.is_bot]
        return all(self._end_nom_votes.get(pp.player_id) for pp in alive_humans)

    async def _bot_auto_nominate(self) -> None:
        """If only bots are alive (no humans), have a bot nominate to avoid deadlock."""
        # Small delay to ensure _wait_for_signal registers its event first
        await asyncio.sleep(0.1)
        alive_humans = [p for p in self.players.values() if p.alive and not p.is_bot]
        if alive_humans:
            return
        await asyncio.sleep(random.uniform(0.5, 1.0))
        alive_bots = [p for p in self.players.values() if p.alive and p.is_bot]
        eligible_nominators = [b for b in alive_bots if b.player_id not in self._nominators_today]
        eligible_nominees = [b for b in alive_bots if b.player_id not in self._nominated_today]
        if eligible_nominators and eligible_nominees:
            nominator = random.choice(eligible_nominators)
            nominee = random.choice([n for n in eligible_nominees if n.player_id != nominator.player_id] or eligible_nominees)
            self._nominators_today.add(nominator.player_id)
            self._nominated_today.add(nominee.player_id)
            self._pending_nomination = (nominator.player_id, nominee.player_id)
            self.handle_signal("nomination_submitted")
        else:
            self._pending_nomination = None
            self.handle_signal("nomination_submitted")

    async def _bot_auto_vote(self) -> None:
        """Bots cast random votes immediately."""
        await asyncio.sleep(0.1)
        for pid in self.seat_order:
            p = self.players[pid]
            if not p.is_bot:
                continue
            if not p.alive and not p.has_vote_token:
                continue
            vote = random.random() < 0.5
            if not p.alive and not vote:
                vote = False  # dead bot abstains — record as False, don't consume token
            self._votes[pid] = vote
            if not p.alive and vote:
                p.has_vote_token = False

        if len(self._votes) >= self._vote_eligible_count:
            self.handle_signal("voting_complete")

    # ------------------------------------------------------------------
    # Slayer ability (day action)
    # ------------------------------------------------------------------

    async def handle_slayer(self, slayer_id: str, target_id: str) -> None:
        """Any alive player can claim Slayer. Only the real Slayer's shot kills."""
        p = self.players.get(slayer_id)
        if not p or not p.alive:
            return
        if self.phase != GamePhase.DAY:
            return

        target = self.players.get(target_id)
        if not target or not target.alive:
            return

        is_real_slayer = p.effective_role_id() == "slayer" and not p.used_ability
        if is_real_slayer:
            p.used_ability = True

        role_def = ROLE_BY_ID.get(target.role_id)
        is_demon = role_def and role_def.team == Team.DEMON
        kills = is_real_slayer and is_demon and not p.poisoned and not p.drunk

        pt = self._ptag(p)
        tt = self._ptag(target)
        if kills:
            target.alive = False
            self._add_log("ability", f"{pt} 声称自己是杀手，对 {tt} 开枪——{tt} 死亡了！")
            await manager.broadcast(self.room_code, "slayer_success", {
                "message": f"{pt} 声称自己是杀手，对 {tt} 开枪——{tt} 死亡了！",
                "slayer": slayer_id, "target": target_id,
            })
            await manager.broadcast(self.room_code, "game_state", self.public_state())
            await self._end_game("good", f"{pt} 杀死了恶魔，好人阵营获胜！")
        else:
            self._add_log("ability", f"{pt} 声称自己是杀手，对 {tt} 开枪——但什么也没发生。")
            await manager.broadcast(self.room_code, "slayer_fail", {
                "message": f"{pt} 声称自己是杀手，对 {tt} 开枪——但什么也没发生。",
                "slayer": slayer_id, "target": target_id,
            })
            await manager.broadcast(self.room_code, "game_state", self.public_state())

    # ------------------------------------------------------------------
    # Speech done
    # ------------------------------------------------------------------

    async def handle_speech_done(self, player_id: str) -> None:
        """Player confirms they finished speaking."""
        if self.day_sub not in (DaySubPhase.NOMINATOR_SPEECH, DaySubPhase.NOMINEE_SPEECH):
            return
        if player_id != self._speech_player:
            return
        self.handle_signal("speech_done")

    # ------------------------------------------------------------------
    # Signals (for async coordination)
    # ------------------------------------------------------------------

    async def _wait_for_signal(self, signal_name: str, timeout: int = 60) -> None:
        """Wait for a named signal from the WS handler. Independent of night actions."""
        evt = asyncio.Event()
        self._signal_events[signal_name] = evt
        try:
            await asyncio.wait_for(evt.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        self._signal_events.pop(signal_name, None)

    def handle_signal(self, signal_name: str) -> None:
        evt = self._signal_events.get(signal_name)
        if evt:
            evt.set()

    # ------------------------------------------------------------------
    # Player choice helpers
    # ------------------------------------------------------------------

    async def _ask_player_choose(
        self, player_id: str, action_type: str, prompt: str, options: list[dict],
    ) -> str | None:
        if not options:
            return None

        p = self.players.get(player_id)
        if p and p.is_bot:
            await asyncio.sleep(random.uniform(0.3, 1.0))
            return random.choice(options)["id"]

        self._pending_action = asyncio.Event()
        self._action_response = {}

        await manager.send_personal(self.room_code, player_id, "night_action", {
            "action_type": action_type,
            "prompt": prompt,
            "options": options,
            "timeout": NIGHT_ACTION_TIMEOUT,
        })

        try:
            await asyncio.wait_for(self._pending_action.wait(), timeout=NIGHT_ACTION_TIMEOUT)
        except asyncio.TimeoutError:
            return random.choice(options)["id"]
        finally:
            self._pending_action = None

        return self._action_response.get("chosen_id")

    async def _ask_player_choose_two(
        self, player_id: str, action_type: str, prompt: str, options: list[dict],
    ) -> list[str] | None:
        if len(options) < 2:
            return None

        p = self.players.get(player_id)
        if p and p.is_bot:
            await asyncio.sleep(random.uniform(0.3, 1.0))
            picked = random.sample(options, 2)
            return [o["id"] for o in picked]

        self._pending_action = asyncio.Event()
        self._action_response = {}

        await manager.send_personal(self.room_code, player_id, "night_action", {
            "action_type": action_type,
            "prompt": prompt,
            "options": options,
            "choose_count": 2,
            "timeout": NIGHT_ACTION_TIMEOUT,
        })

        try:
            await asyncio.wait_for(self._pending_action.wait(), timeout=NIGHT_ACTION_TIMEOUT)
        except asyncio.TimeoutError:
            picked = random.sample(options, 2)
            return [o["id"] for o in picked]
        finally:
            self._pending_action = None

        return self._action_response.get("chosen_ids")

    def submit_action(self, player_id: str, data: dict[str, Any]) -> None:
        self._action_response = data
        if self._pending_action:
            self._pending_action.set()

    # ------------------------------------------------------------------
    # Helper: two-player info (Washerwoman/Librarian/Investigator pattern)
    # ------------------------------------------------------------------

    async def _send_two_player_info(
        self, receiver_pid: str, info_type: str,
        player_a: PlayerState, player_b: PlayerState, role_id: str,
    ) -> None:
        role_def = ROLE_BY_ID.get(role_id)
        pair = [player_a, player_b]
        random.shuffle(pair)
        await manager.send_personal(self.room_code, receiver_pid, "night_info", {
            "info_type": f"{info_type}_result",
            "message": f"{self._ptag(pair[0])} 和 {self._ptag(pair[1])} 中有一个是{role_def.name_zh if role_def else role_id}。",
            "player1": {"id": pair[0].player_id, "name": pair[0].name},
            "player2": {"id": pair[1].player_id, "name": pair[1].name},
            "role_id": role_id,
            "role_name": role_def.name_zh if role_def else role_id,
        })

    # ------------------------------------------------------------------
    # Registration helpers (Spy / Recluse)
    # ------------------------------------------------------------------

    def _registers_as_evil(self, p: PlayerState) -> bool:
        if p.alignment == Alignment.EVIL:
            if p.role_id == "spy" and random.random() < 0.3:
                return False  # Spy might register as good
            return True
        if p.role_id == "recluse" and random.random() < 0.3:
            return True  # Recluse might register as evil
        return False

    def _apply_spy_recluse_registration(self, target: PlayerState, default_role: str) -> str:
        if target.role_id == "spy" and random.random() < 0.5:
            good_roles = get_roles_by_team(Team.TOWNSFOLK) + get_roles_by_team(Team.OUTSIDER)
            return random.choice(good_roles).id
        if target.role_id == "recluse" and random.random() < 0.3:
            evil_roles = get_roles_by_team(Team.MINION) + get_roles_by_team(Team.DEMON)
            return random.choice(evil_roles).id
        return default_role

    # ------------------------------------------------------------------
    # Find helpers
    # ------------------------------------------------------------------

    def _find_demon_player(self) -> str | None:
        for pid in self.seat_order:
            p = self.players[pid]
            rd = ROLE_BY_ID.get(p.role_id)
            if rd and rd.team == Team.DEMON and p.alive:
                return pid
        return None

    def _find_minion_players(self) -> list[str]:
        result = []
        for pid in self.seat_order:
            p = self.players[pid]
            rd = ROLE_BY_ID.get(p.role_id)
            if rd and rd.team == Team.MINION:
                result.append(pid)
        return result

    def _find_scarlet_woman(self) -> PlayerState | None:
        for p in self.players.values():
            if p.role_id == "scarlet_woman" and p.alive:
                return p
        return None

    # ------------------------------------------------------------------
    # Game end
    # ------------------------------------------------------------------

    async def _end_game(self, winner: str, message: str) -> None:
        self.phase = GamePhase.GAME_OVER
        self._add_log("game_over", message)

        reveal = []
        for pid in self.seat_order:
            p = self.players[pid]
            rd = ROLE_BY_ID.get(p.role_id)
            reveal.append({
                "id": pid,
                "name": p.name,
                "seat": p.seat + 1,
                "role_id": p.role_id,
                "role_name": rd.name_zh if rd else "???",
                "alignment": p.alignment.value,
                "alive": p.alive,
            })

        key_types = {"phase", "death", "execution", "ability", "game_over"}
        log_summary = [
            e["message"] for e in self.log
            if e.get("type") in key_types
        ]

        await manager.broadcast(self.room_code, "game_over", {
            "winner": winner,
            "message": message,
            "players": reveal,
            "log_summary": log_summary,
        })

    # ------------------------------------------------------------------
    # Restart
    # ------------------------------------------------------------------

    async def restart_game(self) -> None:
        """Reset the game back to lobby, keeping all players."""
        if self._game_task and not self._game_task.done():
            self._game_task.cancel()
            self._game_task = None

        self.phase = GamePhase.LOBBY
        self.day_sub = DaySubPhase.ANNOUNCE
        self.day_number = 0
        self.log.clear()
        self.demon_bluffs.clear()
        self._night_deaths.clear()
        self._executed_today = ""
        self._signal_events.clear()
        self._pending_action = None
        self._action_response.clear()
        self._nominated_today.clear()
        self._nominators_today.clear()
        self._current_nominee = ""
        self._votes.clear()
        self._vote_eligible_count = 0
        self._vote_tally.clear()
        self._highest_vote = ("", 0)
        self._nominations_remaining = 0
        self._nomination_records.clear()
        self._end_nom_proposer = ""
        self._end_nom_votes.clear()
        self._speech_player = ""
        self._day_speech_order = {}
        self._last_night_msg = ""

        for p in self.players.values():
            p.role_id = ""
            p.apparent_role_id = ""
            p.alive = True
            p.has_vote_token = True
            p.alignment = Alignment.GOOD
            p.poisoned = False
            p.drunk = False
            p.protected = False
            p.used_ability = False
            p.butler_master_id = ""
            p.fortune_teller_red_herring = ""

        await manager.broadcast(self.room_code, "game_restarted", {})
        await manager.broadcast(self.room_code, "game_state", self.public_state())

    # ------------------------------------------------------------------
    # Log
    # ------------------------------------------------------------------

    def _ptag(self, p) -> str:
        """Format player as '[seat]name' for logs."""
        return f"[{p.seat + 1}]{p.name}"

    def _add_log(self, event_type: str, message: str) -> None:
        self.log.append({
            "type": event_type,
            "message": message,
            "time": time.time(),
        })
