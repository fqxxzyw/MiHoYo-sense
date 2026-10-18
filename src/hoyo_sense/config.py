"""集中管理项目路径和采集上限，不创建目录，也不在导入时发起网络请求。"""

from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path, PureWindowsPath

from .games import MIYOUSHE_GAME_IDS as GAMES

# __file__ 是本配置模块的文件路径，parents[2] 从 src/hoyo_sense 回到项目根目录。
# 不使用 Path.cwd()，因此在 PyCharm、终端、其他工作目录启动时，结果都一样。
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATHS = {
    "raw_dir": "data/raw",
    "processed_dir": "data/processed",
    "annotated_dir": "data/annotated",
    "splits_dir": "data/splits",
    "outputs_dir": "outputs",
}
DEFAULT_LIMITS = {"max_posts": 3, "max_pages": 5, "max_sub_pages": 2, "max_floors": 3}
DEFAULT_REQUEST = {"interval_seconds": 2.0, "timeout_seconds": 20.0}
DEFAULT_DISCOVERY = {"max_pages": 10}


def project_path(value, *, root=PROJECT_ROOT):
    """相对路径基于项目根目录；绝对路径和 ~ 用户目录保持各自含义。"""
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ValueError("路径必须是非空字符串或 Path。")
    # Windows 绝对路径只能在 Windows 上使用，避免 Linux 把 D:/... 当作相对目录。
    if os.name != "nt" and PureWindowsPath(str(value)).is_absolute():
        raise ValueError("当前系统不能使用 Windows 绝对路径；请配置项目相对路径。")
    path = Path(value).expanduser()
    return (path if path.is_absolute() else Path(root) / path).resolve()


@dataclass(frozen=True)
class ProjectConfig:
    """paths 字典保存解析后的 Path；limits 字典保存整数上限或 None。"""

    source: Path
    paths: dict[str, Path]
    limits: dict[str, int | None]
    request: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_REQUEST))
    discovery: dict[str, int | None] = field(default_factory=lambda: dict(DEFAULT_DISCOVERY))

    @property
    def miyoushe_root(self):
        # raw_dir 下仍区分平台，不能把三个游戏直接放在 raw_dir 根部。
        return self.paths["raw_dir"] / "miyoushe"


def load_config(path=None, *, root=PROJECT_ROOT):
    """JSON 中 null 对应 Python 的 None。未知键直接报错，防止拼错配置却被忽略。"""
    source = project_path(path if path is not None else "configs/miyoushe.json", root=root)
    with source.open(encoding="utf-8-sig") as file:
        data = json.load(file)
    if not isinstance(data, dict) or set(data) - {"paths", "collection", "request", "discovery"}:
        raise ValueError("配置顶层只支持 paths、collection、request 和 discovery 字典。")
    supplied_paths = data.get("paths", {})
    supplied_limits = data.get("collection", {})
    if not isinstance(supplied_paths, dict) or set(supplied_paths) - set(DEFAULT_PATHS):
        raise ValueError("paths 配置包含未知路径名，或不是字典。")
    if not isinstance(supplied_limits, dict) or set(supplied_limits) - set(DEFAULT_LIMITS):
        raise ValueError("collection 配置包含未知上限名，或不是字典。")
    paths = {key: project_path(value, root=root)
             for key, value in {**DEFAULT_PATHS, **supplied_paths}.items()}
    # raw 与处理结果不得相同或互相包含，避免清洗代码误覆盖采集源文件。
    values = list(paths.items())
    for index, (name, value) in enumerate(values):
        for other_name, other in values[index + 1:]:
            if value == other or value in other.parents or other in value.parents:
                raise ValueError(f"数据目录不能相同或互相包含：{name} 与 {other_name}")
    limits = {**DEFAULT_LIMITS, **supplied_limits}
    for name, value in limits.items():
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            raise ValueError(f"{name} 必须为非负整数或 null。")
    supplied_request = data.get("request", {})
    if not isinstance(supplied_request, dict) or set(supplied_request) - set(DEFAULT_REQUEST):
        raise ValueError("request 配置包含未知键，或不是字典。")
    request = {**DEFAULT_REQUEST, **supplied_request}
    for name, value in request.items():
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value < 0
                or (name == "timeout_seconds" and value == 0)):
            raise ValueError(f"{name} 必须为有限数字：间隔不小于 0，超时大于 0；不能为 null。")
    supplied_discovery = data.get("discovery", {})
    if not isinstance(supplied_discovery, dict) or set(supplied_discovery) - set(DEFAULT_DISCOVERY):
        raise ValueError("discovery 配置只支持 max_pages。")
    discovery = {**DEFAULT_DISCOVERY, **supplied_discovery}
    value = discovery["max_pages"]
    if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
        raise ValueError("发现 max_pages 必须为正整数或 null。")
    return ProjectConfig(source=source, paths=paths, limits=limits, request=request, discovery=discovery)
