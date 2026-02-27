"""Trouble Brewing role definitions."""

from server.models import RoleDef, Team

# First-night order reference (non-zero = acts):
#   Poisoner 10, Washerwoman 20, Librarian 21, Investigator 22,
#   Chef 23, Empath 24, Fortune Teller 25, Butler 26
# Other-night order reference:
#   Poisoner 10, Monk 11, Scarlet Woman (passive 15), Imp 20,
#   Ravenkeeper 25, Empath 30, Fortune Teller 31, Undertaker 32, Butler 33

TROUBLE_BREWING: list[RoleDef] = [
    # ---- Townsfolk ----
    RoleDef(
        id="washerwoman", name_en="Washerwoman", name_zh="洗衣妇",
        team=Team.TOWNSFOLK,
        ability_zh="首夜，你得知2名玩家中有1名是某个特定的村民角色。",
        first_night_order=20,
    ),
    RoleDef(
        id="librarian", name_en="Librarian", name_zh="图书管理员",
        team=Team.TOWNSFOLK,
        ability_zh="首夜，你得知2名玩家中有1名是某个特定的外来者角色（或得知没有外来者在场）。",
        first_night_order=21,
    ),
    RoleDef(
        id="investigator", name_en="Investigator", name_zh="调查员",
        team=Team.TOWNSFOLK,
        ability_zh="首夜，你得知2名玩家中有1名是某个特定的爪牙角色。",
        first_night_order=22,
    ),
    RoleDef(
        id="chef", name_en="Chef", name_zh="厨师",
        team=Team.TOWNSFOLK,
        ability_zh="首夜，你得知有多少对邪恶玩家相邻而坐。",
        first_night_order=23,
    ),
    RoleDef(
        id="empath", name_en="Empath", name_zh="共情者",
        team=Team.TOWNSFOLK,
        ability_zh="每个夜晚，你得知与你相邻的存活玩家中有几个是邪恶的。",
        first_night_order=24,
        other_night_order=30,
    ),
    RoleDef(
        id="fortune_teller", name_en="Fortune Teller", name_zh="占卜师",
        team=Team.TOWNSFOLK,
        ability_zh="每个夜晚，选择2名玩家：你得知他们之中是否有恶魔。（有一名好人玩家会被误判为恶魔。）",
        first_night_order=25,
        other_night_order=31,
    ),
    RoleDef(
        id="undertaker", name_en="Undertaker", name_zh="掘墓人",
        team=Team.TOWNSFOLK,
        ability_zh="每个夜晚*，你得知今天被处决的玩家的角色。",
        other_night_order=32,
    ),
    RoleDef(
        id="monk", name_en="Monk", name_zh="僧侣",
        team=Team.TOWNSFOLK,
        ability_zh="每个夜晚*，选择一名其他玩家：今晚该玩家免受恶魔的攻击。",
        other_night_order=11,
    ),
    RoleDef(
        id="ravenkeeper", name_en="Ravenkeeper", name_zh="守鸦人",
        team=Team.TOWNSFOLK,
        ability_zh="如果你在夜晚死亡，你会被唤醒并选择一名玩家：你得知他的角色。",
        other_night_order=25,
    ),
    RoleDef(
        id="virgin", name_en="Virgin", name_zh="贞女",
        team=Team.TOWNSFOLK,
        ability_zh="第一次被村民提名时，该提名者立即被处决。",
    ),
    RoleDef(
        id="slayer", name_en="Slayer", name_zh="杀手",
        team=Team.TOWNSFOLK,
        ability_zh="白天，你可以选择一名玩家：如果该玩家是恶魔，他立即死亡。（每局限用一次。）",
    ),
    RoleDef(
        id="soldier", name_en="Soldier", name_zh="士兵",
        team=Team.TOWNSFOLK,
        ability_zh="你不会被恶魔在夜间杀死。",
    ),
    RoleDef(
        id="mayor", name_en="Mayor", name_zh="市长",
        team=Team.TOWNSFOLK,
        ability_zh="如果恶魔在夜间攻击你，另一名玩家可能代替你死亡。如果只剩3名玩家且无人被处决，好人阵营获胜。",
    ),
    # ---- Outsiders ----
    RoleDef(
        id="butler", name_en="Butler", name_zh="管家",
        team=Team.OUTSIDER,
        ability_zh="每个夜晚，选择一名玩家（非你自己）：明天投票时，只有当你的「主人」也投票时，你的投票才有效。",
        first_night_order=26,
        other_night_order=33,
    ),
    RoleDef(
        id="drunk", name_en="Drunk", name_zh="酒鬼",
        team=Team.OUTSIDER,
        ability_zh="你以为自己是一个村民角色，但其实你不是。你没有能力。",
        setup_modifies=True,
    ),
    RoleDef(
        id="recluse", name_en="Recluse", name_zh="隐士",
        team=Team.OUTSIDER,
        ability_zh="你可能会被当作邪恶的爪牙或恶魔来检测，即使你是好人。",
    ),
    RoleDef(
        id="saint", name_en="Saint", name_zh="圣徒",
        team=Team.OUTSIDER,
        ability_zh="如果你被处决，你的阵营（邪恶阵营）获胜。",
    ),
    # ---- Minions ----
    RoleDef(
        id="poisoner", name_en="Poisoner", name_zh="投毒者",
        team=Team.MINION,
        ability_zh="每个夜晚，选择一名玩家：该玩家今晚和明天中毒（能力失效，可能获得错误信息）。",
        first_night_order=10,
        other_night_order=10,
    ),
    RoleDef(
        id="spy", name_en="Spy", name_zh="间谍",
        team=Team.MINION,
        ability_zh="每个夜晚，你可以查看魔典（所有角色分配）。你可能会被当作好人来检测。",
        first_night_order=27,
        other_night_order=34,
    ),
    RoleDef(
        id="scarlet_woman", name_en="Scarlet Woman", name_zh="猩红女郎",
        team=Team.MINION,
        ability_zh="如果存活玩家≥5且恶魔死亡，你变成恶魔。",
        other_night_order=15,
    ),
    RoleDef(
        id="baron", name_en="Baron", name_zh="男爵",
        team=Team.MINION,
        ability_zh="游戏中额外增加2个外来者角色（替换2个村民）。",
        setup_modifies=True,
    ),
    # ---- Demon ----
    RoleDef(
        id="imp", name_en="Imp", name_zh="小恶魔",
        team=Team.DEMON,
        ability_zh="每个夜晚*，选择一名玩家：该玩家死亡。如果你选择自己，一名爪牙变为小恶魔。",
        other_night_order=20,
    ),
]

ROLE_BY_ID: dict[str, RoleDef] = {r.id: r for r in TROUBLE_BREWING}


def get_roles_by_team(team: Team) -> list[RoleDef]:
    return [r for r in TROUBLE_BREWING if r.team == team]
