"""Pydantic models and enums for the game."""

from __future__ import annotations

import enum
from typing import Any

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Team(str, enum.Enum):
    TOWNSFOLK = "townsfolk"
    OUTSIDER = "outsider"
    MINION = "minion"
    DEMON = "demon"


class Alignment(str, enum.Enum):
    GOOD = "good"
    EVIL = "evil"


class GamePhase(str, enum.Enum):
    LOBBY = "lobby"
    SETUP = "setup"
    FIRST_NIGHT = "first_night"
    DAY = "day"
    NOMINATION = "nomination"
    VOTING = "voting"
    NIGHT = "night"
    GAME_OVER = "game_over"


class DaySubPhase(str, enum.Enum):
    ANNOUNCE = "announce"
    DISCUSSION = "discussion"
    NOMINATION = "nomination"
    NOMINATOR_SPEECH = "nominator_speech"
    NOMINEE_SPEECH = "nominee_speech"
    VOTING = "voting"
    EXECUTION = "execution"


# ---------------------------------------------------------------------------
# Role definition (static data)
# ---------------------------------------------------------------------------

class RoleDef(BaseModel):
    id: str
    name_en: str
    name_zh: str
    team: Team
    ability_zh: str
    first_night_order: int = 0   # 0 = does not act
    other_night_order: int = 0
    setup_modifies: bool = False  # e.g. Baron, Drunk


# ---------------------------------------------------------------------------
# Runtime player state
# ---------------------------------------------------------------------------

class PlayerState(BaseModel):
    player_id: str
    name: str
    seat: int = 0
    is_bot: bool = False
    role_id: str = ""
    # For the Drunk: what the player *thinks* they are
    apparent_role_id: str = ""
    alive: bool = True
    has_vote_token: bool = True  # dead players get one last vote
    alignment: Alignment = Alignment.GOOD
    poisoned: bool = False
    drunk: bool = False
    protected: bool = False  # Monk protection this night
    used_ability: bool = False  # one-shot abilities (Slayer, Virgin)
    # Butler's master
    butler_master_id: str = ""
    # Fortune Teller red herring player id
    fortune_teller_red_herring: str = ""

    def effective_role_id(self) -> str:
        return self.apparent_role_id or self.role_id

    @property
    def is_evil(self) -> bool:
        return self.alignment == Alignment.EVIL

    @property
    def has_ability(self) -> bool:
        return self.alive and not self.drunk and not self.poisoned


# ---------------------------------------------------------------------------
# WebSocket message envelope
# ---------------------------------------------------------------------------

class WSMessage(BaseModel):
    type: str
    data: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Player count -> role distribution
# ---------------------------------------------------------------------------

PLAYER_DISTRIBUTION: dict[int, tuple[int, int, int, int]] = {
    # (townsfolk, outsiders, minions, demons)
    5:  (3, 0, 1, 1),
    6:  (3, 1, 1, 1),
    7:  (5, 0, 1, 1),
    8:  (5, 1, 1, 1),
    9:  (5, 2, 1, 1),
    10: (7, 0, 2, 1),
    11: (7, 1, 2, 1),
    12: (7, 2, 2, 1),
    13: (9, 0, 3, 1),
    14: (9, 1, 3, 1),
    15: (9, 2, 3, 1),
}
