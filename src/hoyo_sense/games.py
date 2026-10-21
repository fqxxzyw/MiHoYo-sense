"""统一游戏名称与平台自己的游戏标识；不能跨平台套用米游社 gids。"""

GAME_NAMES = {
    "genshin": "原神",
    "starrail": "崩坏：星穹铁道",
    "zzz": "绝区零",
}

MIYOUSHE_GAME_IDS = {
    "genshin": 2,
    "starrail": 6,
    "zzz": 8,
}

# B站 mid 和抖音 sec_user_id 保存在各自 configs/<platform>.json 的 accounts 中。
