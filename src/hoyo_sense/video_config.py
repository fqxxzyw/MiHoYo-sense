"""视频平台配置：复用项目根路径，公用设置与本地会话分开。"""

from dataclasses import dataclass
import json
import math
from pathlib import Path

from .config import project_path
from .games import GAME_NAMES


LIMITS = {"max_posts": 1, "max_discovery_pages": 1, "max_pages": 1,
          "max_sub_pages": 1, "max_floors": 1}


@dataclass
class VideoConfig:
    platform: str
    raw_root: Path
    accounts: dict
    limits: dict
    request: dict
    params: dict


def limit(value):
    if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
        raise ValueError("上限必须为非负整数或 null。")
    return value


def load_video_config(platform, path=None):
    if platform not in {"bilibili", "douyin"}:
        raise ValueError("平台不支持。")
    data = json.loads(project_path(path or f"configs/{platform}.json").read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or set(data) - {"paths", "request", "collection", "accounts", "params"}:
        raise ValueError("视频平台配置存在未知顶层键。")
    paths = data.get("paths", {})
    if not isinstance(paths, dict) or set(paths) - {"raw_dir"}:
        raise ValueError("视频平台 paths 仅支持 raw_dir。")
    limits = data.get("collection", {})
    if not isinstance(limits, dict) or set(limits) - set(LIMITS):
        raise ValueError("collection 存在未知上限。")
    limits = {key: limit(value) for key, value in {**LIMITS, **limits}.items()}
    request = data.get("request", {})
    if not isinstance(request, dict) or set(request) - {"interval_seconds", "timeout_seconds", "retries"}:
        raise ValueError("request 存在未知键。")
    request = {"interval_seconds": 2, "timeout_seconds": 60, "retries": 1, **request}
    for key in ("interval_seconds", "timeout_seconds"):
        value = request[key]
        if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                or value < 0 or (key == "timeout_seconds" and value == 0)):
            raise ValueError("请求间隔必须非负，超时必须大于零，且都必须是有限数字。")
    retries = request["retries"]
    if isinstance(retries, bool) or not isinstance(retries, int) or not 0 <= retries <= 5:
        raise ValueError("retries 必须为 0 到 5 的整数（额外重试次数）。")
    accounts = data.get("accounts")
    if not isinstance(accounts, dict) or set(accounts) != set(GAME_NAMES):
        raise ValueError("accounts 必须包含 genshin/starrail/zzz。")
    account_key = "mid" if platform == "bilibili" else "sec_user_id"
    for account in accounts.values():
        if not isinstance(account, dict) or not isinstance(account.get(account_key), str) or not account[account_key]:
            raise ValueError("账号标识必须是完整非空字符串。")
        if platform == "bilibili" and not account[account_key].isascii():
            raise ValueError("mid 必须为 ASCII 数字。")
        if platform == "bilibili" and not account[account_key].isdecimal():
            raise ValueError("mid 必须为数字。")
    params = data.get("params", {})
    if not isinstance(params, dict) or set(params) - {"common", "discovery", "comments", "replies"}:
        raise ValueError("params 存在未知接口用途。")
    # 公共配置不接受会话或签名字段；它们只允许通过被忽略的私密上下文传入。
    from .video_http import PUBLIC_PARAMS
    for values in params.values():
        if (not isinstance(values, dict) or set(values) - PUBLIC_PARAMS
                or any(not isinstance(v, (str, int, float, bool)) for v in values.values())):
            raise ValueError("公共 params 包含非业务参数或不支持的类型，请将会话字段移至本地私密配置。")
    if platform == "bilibili":
        if params.get("comments", {}).get("mode", 3) != 3:
            raise ValueError("当前 B站解析仅验证热门模式结构，mode 必须为 3。")
        if any(params.get(k, {}).get("type", 1) != 1 for k in ("comments", "replies")):
            raise ValueError("视频评论 type 必须为 1。")
    for values in params.values():
        for key in ("count", "ps"):
            if key in values and (type(values[key]) is not int or values[key] < 1):
                raise ValueError("分页大小必须为正整数。")
    return VideoConfig(platform, project_path(paths.get("raw_dir", "data/raw")) / platform,
                       accounts, limits, request, params)
