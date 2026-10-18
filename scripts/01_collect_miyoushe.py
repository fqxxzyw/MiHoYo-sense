"""米游社三游戏原始采集：发现第一页、有限批量、逐页保存与断点恢复。"""

import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
from typing import Any

import requests

# 直接运行 scripts 下的文件时，将本项目 src 加入模块查找路径。
# 这一步只用于加载共用配置，不改变当前工作目录，也不移动用户的数据。
_SOURCE_DIR = Path(__file__).resolve().parents[1] / "src"
if str(_SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(_SOURCE_DIR))
from hoyo_sense.config import GAMES, PROJECT_ROOT, load_config, project_path

DEFAULT_CONFIG = load_config()
POST_ID = "78918177"
GAME_ID = 2
PAGE_SIZE = 20
MAX_PAGES = None       # 顶层累计页数上限；None 表示不设上限。
MAX_SUB_PAGES = None   # 每楼累计页数上限；恢复时包含之前成功保存的页。
MAX_FLOORS = None      # 小规模验证时限制楼层数。
REQUEST_INTERVAL = 2
REQUEST_TIMEOUT = 20
POST_URL = "https://bbs-api.miyoushe.com/post/wapi/getPostFull"
COMMENTS_URL = "https://bbs-api.miyoushe.com/post/wapi/getPostReplies"
SUB_REPLIES_URL = "https://bbs-api.miyoushe.com/post/wapi/getSubReplies"
WEB_HOME_URL = "https://bbs-api-static.miyoushe.com/apihub/wapi/webHome"
DISCOVERY_SOURCE = "webHome/recommended_posts"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/155.0.0.0 Safari/537.36 Edg/155.0.0.0"
    ),
    "Referer": "https://www.miyoushe.com/",
    "Origin": "https://www.miyoushe.com",
    "Accept": "application/json, text/plain, */*",
}
RAW_DIR = DEFAULT_CONFIG.miyoushe_root / "genshin"
DEFAULT_RUN_DIR = RAW_DIR / POST_ID / "full_collection"


def game_raw_dir(game, raw_root=None):
    """三个游戏分开保存；函数返回 Path 路径对象，不在这里创建文件。"""
    if game not in GAMES:
        raise ValueError(f"不支持的游戏：{game}")
    if raw_root is not None:
        return project_path(raw_root) / game
    return RAW_DIR if game == "genshin" else RAW_DIR.parent / game


def validate_post_id(post_id):
    if isinstance(post_id, bool) or not isinstance(post_id, (str, int)):
        raise ValueError("帖子 ID 必须是数字字符串或整数。")
    post_id = str(post_id)
    if not post_id.isascii() or not post_id.isdecimal():
        raise ValueError("帖子 ID 必须是 ASCII 数字，不能包含路径字符。")
    return post_id


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path) -> Any:
    with Path(path).open(encoding="utf-8") as file:
        return json.load(file)


def save_json(data, path):
    """先写同目录临时文件，再原子替换，避免中断留下半份 JSON。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_digest(path, expected):
    """兼容 Git 在 Windows/Linux 间仅转换 CRLF/LF；其他字节变化仍拒绝。

    返回实际 SHA；旧 SHA 被保留为元数据，不改写原始 JSON。
    JSON 字符串中的换行是转义字符，不受这里的文件换行转换影响。
    """
    content = Path(path).read_bytes()
    actual = hashlib.sha256(content).hexdigest()
    if expected and actual != expected:
        lf = content.replace(b"\r\n", b"\n")
        candidates = (lf, lf.replace(b"\n", b"\r\n"))
        if not any(hashlib.sha256(data).hexdigest() == expected for data in candidates):
            raise ValueError(f"已保存文件发生修改，校验失败：{path}")
    return actual


@contextmanager
def run_lock(run_dir):
    """系统文件锁：同一目录只能有一个采集进程；进程退出后自动释放。"""
    run_dir.mkdir(parents=True, exist_ok=True)
    with (run_dir / ".collector.lock").open("a+b") as file:
        if file.seek(0, os.SEEK_END) == 0:
            file.write(b"0")
            file.flush()
        file.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(file, fcntl.LOCK_UN)


def check_result(result):
    if not isinstance(result, dict):
        raise ValueError("JSON 最外层不是字典。")
    if result.get("retcode") != 0:
        raise ValueError(f"接口业务失败：retcode={result.get('retcode')!r}，"
                         f"message={str(result.get('message', ''))[:200]!r}")


def request_json(url, params):
    """统一请求间隔、超时与报错；不自动重试，不修改代理或添加凭据。"""
    time.sleep(REQUEST_INTERVAL)
    response = requests.get(url, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    print("请求：", response.url, "HTTP", response.status_code, flush=True)
    response.raise_for_status()
    result = response.json()
    check_result(result)
    return result


def build_discovery_tasks(result, discovered_at, params):
    """首页列表转成候选任务列表，不调用帖子详情或评论接口。"""
    check_result(result)
    recommended = result["data"]["recommended_posts"]
    if not isinstance(recommended, list):
        raise ValueError("data.recommended_posts 不是列表。")

    # 用帖子 ID 作为字典键去重，保留首次出现顺序；最后转为便于保存的列表。
    # 不从别的首页栏目提取帖子，也不把请求的 gids 当作返回记录的 game_id。
    by_post_id: dict[str, Any] = {}
    for index, item in enumerate(recommended, start=1):
        post = item["post"]
        post_id = post["post_id"]
        if isinstance(post_id, bool) or not isinstance(post_id, (str, int)):
            raise ValueError("post_id 必须是字符串或整数，不能是浮点数。")
        post_id = str(post_id)
        if not post_id.isdecimal():
            raise ValueError("post_id 必须是数字 ID。")
        subject, game_id = post["subject"], post["game_id"]
        if not isinstance(subject, str):
            raise ValueError("post.subject 不是字符串。")
        if isinstance(game_id, bool) or not isinstance(game_id, (str, int)):
            raise ValueError("post.game_id 类型不正确。")
        if post_id not in by_post_id:
            by_post_id[post_id] = {
                "post_id": post_id, "subject": subject, "game_id": game_id,
                "discovery_status": "discovered",  # 仅发现候选，不代表帖子已采集或未采集。
                "source": DISCOVERY_SOURCE, "endpoint": WEB_HOME_URL,
                "discovered_at": discovered_at, "request_params": dict(params),
                "raw_file": "webHome_page1.json", "source_indices": [],
            }
        # 同 ID 的所有出现位置仍可追溯到 raw；不同标题等原始字段也都保留在 raw 中。
        by_post_id[post_id]["source_indices"].append(index)
    return list(by_post_id.values()), len(recommended)


def discover_posts(discovery_root=None, *, game="genshin", raw_root=None):
    """仅请求一次首页第 1 页，每次独立保存，不打开或更新单帖采集断点。"""
    started = datetime.now(timezone.utc)
    root = (project_path(discovery_root) if discovery_root is not None
            else game_raw_dir(game, raw_root) / "_discovery")
    run_dir = root / started.strftime("%Y%m%d_%H%M%S_%f_UTC")
    run_dir.mkdir(parents=True, exist_ok=False)
    params = {"gids": GAMES[game], "page": 1, "page_size": 20}
    manifest = {
        "kind": "post_discovery", "game": game, "source": DISCOVERY_SOURCE, "endpoint": WEB_HOME_URL,
        "request_params": params, "started_at": started.isoformat(), "status": "running",
        "raw_file": "webHome_page1.json", "task_file": "discovered_posts.json",
        "pagination_verified": False, "collection_started": False,
    }
    save_json(manifest, run_dir / "discovery_manifest.json")
    try:
        # 有意没有翻页循环：列表翻页规则尚未验证，只请求明确授权的第 1 页。
        result = request_json(WEB_HOME_URL, params)
        discovered_at = utc_now()
        save_json(result, run_dir / manifest["raw_file"])
        tasks, record_count = build_discovery_tasks(result, discovered_at, params)
        save_json({
            "source": DISCOVERY_SOURCE, "endpoint": WEB_HOME_URL,
            "discovered_at": discovered_at, "request_params": params,
            "raw_file": manifest["raw_file"], "record_count": record_count,
            "unique_post_count": len(tasks), "duplicate_records": record_count - len(tasks),
            "tasks": tasks,
        }, run_dir / manifest["task_file"])
        manifest.update(
            status="discovered", discovered_at=discovered_at, record_count=record_count,
            unique_post_count=len(tasks), duplicate_records=record_count - len(tasks),
            stop_reason="仅请求第 1 页；列表翻页规则尚未验证",
        )
    except (Exception, KeyboardInterrupt) as error:
        manifest.update(status="interrupted", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        manifest["finished_at"] = utc_now()
        save_json(manifest, run_dir / "discovery_manifest.json")

    print(f"\n发现 {record_count} 条推荐记录，去重后 {len(tasks)} 个帖子：")
    for index, task in enumerate(tasks, start=1):
        print(f"{index:>2}. post_id={task['post_id']}  game_id={task['game_id']}  "
              f"{json.dumps(task['subject'], ensure_ascii=False)}")
    print("帖子发现步骤结束；本步骤不请求评论。")
    print("任务清单：", (run_dir / manifest["task_file"]).resolve())
    return run_dir, tasks


def cursor(value):
    """分页标记不参与算术；拒绝浮点数，避免长 ID 精度丢失。"""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("分页标记必须是字符串或整数。")
    return str(value)


def new_task(kind, floor_id=None, parent_reply_id=None, post_id=POST_ID) -> dict[str, Any]:
    # 字典保存任务属性；pages 列表按请求顺序记录每页的来源和结果。
    return {
        "kind": kind, "post_id": post_id, "floor_id": floor_id, "parent_reply_id": parent_reply_id,
        "status": "pending", "completed": False, "stop_reason": "尚未开始",
        "next_last_id": None, "pending_request": None, "pages": [],
        "pages_saved": 0, "total_records": 0, "unique_comments": 0, "duplicate_records": 0,
    }


def page_file(task, number):
    if task["kind"] == "top":
        return f"comments_{task.get('post_id', POST_ID)}_hot_page{number}.json"
    return f"sub_replies/floor_{task['floor_id']}/page{number}.json"


def checkpoint(state, run_dir):
    state["updated_at"] = utc_now()
    save_json(state, run_dir / "manifest.json")


def page_data(result, task):
    """检查列表、结束标记、ID 和归属；原始记录中的文字与图片信息不删改。"""
    check_result(result)
    data = result["data"]
    if not isinstance(data["list"], list) or not isinstance(data.get("is_last"), bool):
        raise ValueError("list 或 is_last 类型不正确。")
    for item in data["list"]:
        reply = item["reply"]
        if not reply.get("reply_id") or str(reply["post_id"]) != task.get("post_id", POST_ID):
            raise ValueError("评论 ID 缺失或帖子归属不正确。")
        if task["kind"] == "sub" and str(reply["floor_id"]) != str(task["floor_id"]):
            raise ValueError("楼中楼的楼层归属不正确。")
    return data


def append_page(task, result, path, request_cursor, seen_ids, used_cursors, fetched_at=None):
    """集合仅统计重复；raw 内重复记录、空文本和图片元信息全部保留。"""
    data = page_data(result, task)
    new_count = 0
    for item in data["list"]:
        reply_id = str(item["reply"]["reply_id"])
        if reply_id not in seen_ids:
            seen_ids.add(reply_id)
            new_count += 1
    if request_cursor is not None:
        used_cursors.add(request_cursor)
    next_cursor = cursor(data.get("last_id"))
    number = len(task["pages"]) + 1
    task["pages"].append({
        "page": number, "file": page_file(task, number),
        "request_last_id": request_cursor, "response_last_id": next_cursor,
        "record_count": len(data["list"]), "new_count": new_count,
        "duplicate_count": len(data["list"]) - new_count, "is_last": data["is_last"],
        "fetched_at": fetched_at or utc_now(), "sha256": digest(path),
    })
    task.update(
        next_last_id=next_cursor, pending_request=None, pages_saved=number,
        total_records=task["total_records"] + len(data["list"]),
        unique_comments=len(seen_ids),
    )
    task["duplicate_records"] = task["total_records"] - len(seen_ids)
    task["completed"] = data["is_last"] is True
    task["status"] = "completed" if task["completed"] else "pending"
    task["stop_reason"] = "正常遍历结束：接口返回 is_last=True" if task["completed"] else "等待下一页"
    if not task["completed"]:
        if not data["list"]:
            raise ValueError("未返回 is_last=True 却返回空列表，异常停止。")
        if next_cursor in (None, ""):
            raise ValueError("分页标记缺失，异常停止。")
        if next_cursor in used_cursors:
            raise ValueError("分页标记重复，异常停止。")


def reconcile(task, run_dir):
    """重读原始页核验断点；补登记已落盘但尚未写入断点的页面，不重新请求。"""
    rebuilt = new_task(task["kind"], task["floor_id"], task["parent_reply_id"], task.get("post_id", POST_ID))
    seen_ids, used_cursors = set(), set()
    for record in task["pages"]:
        filename = page_file(rebuilt, len(rebuilt["pages"]) + 1)
        path = run_dir / filename
        if record["file"] != filename or cursor(record["request_last_id"]) != rebuilt["next_last_id"]:
            raise ValueError("断点中的文件次序或请求分页标记不衔接。")
        actual_sha = check_digest(path, record.get("sha256"))
        if rebuilt["completed"]:
            raise ValueError("is_last=True 后仍有页面，需要检查。")
        result = read_json(path)
        data = page_data(result, task)
        if cursor(record["response_last_id"]) != cursor(data.get("last_id")):
            raise ValueError("断点分页标记与原始响应不一致。")
        append_page(rebuilt, result, path, rebuilt["next_last_id"],
                    seen_ids, used_cursors, record.get("fetched_at"))
        if record.get("sha256") and actual_sha != record["sha256"]:
            rebuilt["pages"][-1].update(
                original_sha256=record.get("original_sha256", record["sha256"]),
                hash_note="仅 CRLF/LF 文件换行转换；原始文件未改写",
            )
        elif record.get("original_sha256"):
            rebuilt["pages"][-1].update(original_sha256=record["original_sha256"],
                                        hash_note=record["hash_note"])
        # 旧数据导入时，也用原始页重新核对记录数、新增数与重复数。
        for field in ("record_count", "new_count", "duplicate_count", "is_last"):
            if record[field] != rebuilt["pages"][-1][field]:
                raise ValueError(f"页面统计与断点不一致：{field}")
    pending = task.get("pending_request")
    if pending is not None:
        expected = page_file(rebuilt, len(rebuilt["pages"]) + 1)
        if pending["file"] != expected or pending["last_id"] != rebuilt["next_last_id"]:
            raise ValueError("待处理请求与现有分页链不一致。")
        path = run_dir / expected
        if path.exists():
            append_page(rebuilt, read_json(path), path, rebuilt["next_last_id"],
                        seen_ids, used_cursors, pending["started_at"])
    # 核验失败时不破坏原任务的已登记页；只有全部通过才替换重建的字段。
    task.update(rebuilt)
    return seen_ids, used_cursors


def collect_pages(state, run_dir, key, max_pages):
    task = state["tasks"][key]
    state["active_task"] = key
    seen_ids, used_cursors = reconcile(task, run_dir)
    checkpoint(state, run_dir)
    while not task["completed"]:
        if max_pages is not None and len(task["pages"]) >= max_pages:
            task.update(status="limited", stop_reason="达到上限：累计页数上限")
            checkpoint(state, run_dir)
            return
        params = {"gids": state["game_id"], "post_id": state["post_id"], "size": PAGE_SIZE}
        if task["kind"] == "top":
            url = COMMENTS_URL
            params["is_hot"] = "true"
        else:
            url = SUB_REPLIES_URL
            params = {"floor_id": task["floor_id"], "gids": state["game_id"],
                      "post_id": state["post_id"], "size": PAGE_SIZE}
        request_cursor = task["next_last_id"]
        if request_cursor is not None:
            params["last_id"] = request_cursor
        filename = page_file(task, len(task["pages"]) + 1)
        path = run_dir / filename
        if path.exists():
            raise ValueError(f"发现未登记文件，不覆盖也不重请求：{path}")
        task.update(status="running", pending_request={
            "file": filename, "last_id": request_cursor, "started_at": utc_now(),
        })
        checkpoint(state, run_dir)  # 在网络请求前持久化待请求页，处理落盘与断点间的中断。
        result = request_json(url, params)
        save_json(result, path)     # 原始响应先落盘；核验失败也保留现场，不跳过异常页。
        append_page(task, result, path, request_cursor, seen_ids, used_cursors)
        checkpoint(state, run_dir)
        print(f"{key}：{task['pages_saved']} 页，不同评论 {len(seen_ids)}，"
              f"状态 {task['status']}；{path.resolve()}", flush=True)


def collect_post(state, run_dir):
    task = state["tasks"]["post"]
    state["active_task"] = "post"
    path = run_dir / task["file"]
    if path.exists():
        actual_sha = check_digest(path, task.get("sha256"))
        if task.get("sha256") and actual_sha != task["sha256"]:
            task.setdefault("original_sha256", task["sha256"])
            task["hash_note"] = "仅 CRLF/LF 文件换行转换；原始文件未改写"
        result = read_json(path)
    else:
        if task["completed"]:
            raise FileNotFoundError("断点记录的帖子文件缺失。")
        result = request_json(POST_URL, {"gids": state["game_id"], "post_id": state["post_id"], "read": 1})
        save_json(result, path)
    check_result(result)
    post = result["data"]["post"]["post"]
    if str(post["post_id"]) != state["post_id"]:
        raise ValueError("帖子归属不正确。")
    if "game_id" in post and str(post["game_id"]) != str(state["game_id"]):
        raise ValueError("帖子详情所属游戏与采集任务不一致。")
    state["title"] = post["subject"]
    task.update(status="completed", completed=True, sha256=digest(path))
    checkpoint(state, run_dir)


def discover_floors(state, run_dir):
    """字典键按 floor_id 去重，parent_reply_id 保留楼中楼与顶层评论的关联。"""
    for page in state["tasks"]["top"]["pages"]:
        for item in read_json(run_dir / page["file"])["data"]["list"]:
            if not item.get("sub_reply_count", 0) and not item.get("sub_replies"):
                continue
            floor_id = str(item["reply"]["floor_id"])
            if not floor_id.isdecimal():
                raise ValueError("楼层 ID 非数字。")
            key = f"floor_{floor_id}"
            parent_id = str(item["reply"]["reply_id"])
            task = state["tasks"].setdefault(key, new_task("sub", floor_id, parent_id, state["post_id"]))
            if task["parent_reply_id"] not in (None, parent_id):
                raise ValueError("同一楼层出现不同的顶层 reply_id。")
            task.update(parent_reply_id=parent_id, observed_sub_reply_count=item.get("sub_reply_count"))


def initialize(run_dir, seed_run=None, *, game="genshin", post_id=POST_ID) -> dict[str, Any]:
    post_id = validate_post_id(post_id)
    game_id = GAMES[game]
    path = run_dir / "manifest.json"
    if path.exists():
        state = read_json(path)
        if state.get("schema_version") != 2 or state.get("post_id") != post_id:
            raise ValueError("旧格式或其他帖子不能直接恢复；请用新目录和 --seed-run 导入旧采样。")
        if (state["game_id"] != game_id or state["game"] != game
                or state["page_size"] != PAGE_SIZE or state["sort"] != "hot"):
            raise ValueError("采样配置与断点不一致。")
        # 兼容已有 schema_version=2 断点：为分页任务补上明确的帖子关联。
        for task in state["tasks"].values():
            if "pages" in task:
                if task.get("post_id", post_id) != post_id:
                    raise ValueError("分页任务与帖子断点的 ID 不一致。")
                task["post_id"] = post_id
        return state
    if any(entry.name != ".collector.lock" for entry in run_dir.iterdir()):
        raise ValueError("新运行目录必须为空，避免覆盖已有数据。")
    state = {
        "schema_version": 2, "platform": "miyoushe", "game": game,
        "post_id": post_id, "game_id": game_id, "sort": "hot", "page_size": PAGE_SIZE,
        "started_at": utc_now(), "status": "pending", "completed": False,
        "completion_scope": "仅当前接口、热门顶层排序及已发现楼层；不保证全部历史、删除或折叠评论",
        "tasks": {
            "post": {"file": f"post_{post_id}.json", "status": "pending", "completed": False},
            "top": new_task("top", post_id=post_id),
        }, "attempts": [],
    }
    if seed_run is not None:
        seed_run = project_path(seed_run)
        legacy = read_json(seed_run / "manifest.json")
        if (legacy.get("post_id") != post_id or legacy.get("game_id", 2) != game_id
                or legacy.get("sort") != "hot" or legacy.get("page_size") != PAGE_SIZE):
            raise ValueError("旧采样的帖子或采样条件不一致。")
        state["seed_run"] = str(seed_run)
        shutil.copy2(seed_run / f"post_{post_id}.json", run_dir / f"post_{post_id}.json")
        state["tasks"]["top"]["pages"] = legacy["comments"]["pages"]
        sub = legacy.get("sub_replies_test")
        if sub:
            floor_id = str(sub["floor_id"])
            summary = read_json(seed_run / f"sub_replies/floor_{floor_id}/summary.json")
            if summary != sub:
                raise ValueError("旧楼中楼 summary 与 manifest 不一致。")
            task = new_task("sub", floor_id, post_id=post_id)
            task["pages"] = [dict(page, file=f"sub_replies/floor_{floor_id}/{page['file']}") for page in sub["pages"]]
            state["tasks"][f"floor_{floor_id}"] = task
        for task in state["tasks"].values():
            if "pages" not in task:
                continue
            for page in task["pages"]:
                target = run_dir / page["file"]
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(seed_run / page["file"], target)
            reconcile(task, run_dir)
    checkpoint(state, run_dir)
    return state


def audit(state, run_dir):
    """只读核验：重算传入的内存字典，不写磁盘，不发请求。"""
    for key, task in state["tasks"].items():
        if "pages" in task:
            if task.get("post_id", state["post_id"]) != state["post_id"]:
                raise ValueError("分页任务的帖子 ID 不一致。")
            task["post_id"] = state["post_id"]
            reconcile(task, run_dir)
            print(f"核验 {key}：{task['pages_saved']} 页，{task['unique_comments']} 个不同 ID，"
                  f"重复 {task['duplicate_records']}，接口结束 {task['completed']}")
        else:
            result = read_json(run_dir / task["file"])
            check_result(result)
            if str(result["data"]["post"]["post"]["post_id"]) != state["post_id"]:
                raise ValueError("帖子 ID 错误。")
            post = result["data"]["post"]["post"]
            if "game_id" in post and str(post["game_id"]) != str(state["game_id"]):
                raise ValueError("帖子游戏归属不正确。")
            check_digest(run_dir / task["file"], task.get("sha256"))


def run_collection(run_dir, max_pages=MAX_PAGES, max_sub_pages=MAX_SUB_PAGES,
                   max_floors=MAX_FLOORS, seed_run=None, *, game="genshin", post_id=POST_ID):
    """上限按累计页数计算。恢复同目录时，已保存页只读核验，不重复请求。"""
    for value in (max_pages, max_sub_pages, max_floors):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            raise ValueError("上限必须是非负整数或 None。")
    run_dir = project_path(run_dir)
    with run_lock(run_dir):
        state = initialize(run_dir, seed_run, game=game, post_id=post_id)
        attempt = {"started_at": utc_now(), "max_pages": max_pages,
                   "max_sub_pages": max_sub_pages, "max_floors": max_floors}
        state["attempts"].append(attempt)
        state.pop("error", None)
        state.update(status="running", completed=False, active_task=None)
        checkpoint(state, run_dir)
        try:
            collect_post(state, run_dir)
            collect_pages(state, run_dir, "top", max_pages)
            discover_floors(state, run_dir)
            checkpoint(state, run_dir)
            floor_keys = [key for key in state["tasks"] if key.startswith("floor_")]
            # 首次发现顺序固定；同一上限重复运行，不会逐轮扩大采样范围。
            for index, key in enumerate(floor_keys):
                task = state["tasks"][key]
                if max_floors is not None and index >= max_floors:
                    if not task["completed"]:
                        task.update(status="limited", stop_reason="达到上限：楼层数上限")
                    continue
                collect_pages(state, run_dir, key, max_sub_pages)
            state["completed"] = all(task["completed"] for task in state["tasks"].values())
            state["status"] = "completed" if state["completed"] else "limited"
            state["stop_reason"] = "正常遍历结束" if state["completed"] else "达到上限，仍有任务未遍历结束"
        except (Exception, KeyboardInterrupt) as error:
            state.update(status="interrupted", completed=False, stop_reason="异常中断",
                         error=f"{type(error).__name__}: {error}")
            key = state.get("active_task")
            if key:
                state["tasks"][key].update(status="interrupted", completed=False, stop_reason=state["error"])
            raise
        finally:
            attempt.update(finished_at=utc_now(), status=state["status"], error=state.get("error"))
            state["active_task"] = None
            checkpoint(state, run_dir)
            print(f"本次状态：{state['status']}；断点：{run_dir / 'manifest.json'}", flush=True)
        return state


def load_discovery_snapshot(discovery_dir, game):
    """只读已有首页响应，重建候选并检查来源，不请求网络、不改发现清单。"""
    discovery_dir = project_path(discovery_dir)
    manifest = read_json(discovery_dir / "discovery_manifest.json")
    params = {"gids": GAMES[game], "page": 1, "page_size": 20}
    if (manifest.get("status") != "discovered" or manifest.get("source") != DISCOVERY_SOURCE
            or manifest.get("request_params") != params):
        raise ValueError("发现快照未成功完成，或游戏、来源、请求参数不匹配。")
    tasks, _ = build_discovery_tasks(
        read_json(discovery_dir / "webHome_page1.json"), manifest["discovered_at"], params,
    )
    if read_json(discovery_dir / "discovered_posts.json")["tasks"] != tasks:
        raise ValueError("发现任务清单与原始首页响应不一致。")
    return tasks


def prepare_batch_game(game, raw_root):
    """优先复用最近一次成功的发现快照；列表来源固定后，批次恢复不重新发现。"""
    discovery_root = raw_root / game / "_discovery"
    successful = []
    if discovery_root.exists():
        for candidate in discovery_root.iterdir():
            path = candidate / "discovery_manifest.json"
            if path.is_file() and read_json(path).get("status") == "discovered":
                successful.append(candidate)
    if successful:
        discovery_dir = max(successful, key=lambda p: p.name)
        tasks = load_discovery_snapshot(discovery_dir, game)
    else:
        discovery_dir, tasks = discover_posts(discovery_root, game=game)

    # 保留跨游戏候选及排除原因，但只采集 game_id 与当前游戏相符的帖子。
    eligible, excluded = [], []
    for candidate in tasks:
        if str(candidate["game_id"]) != str(GAMES[game]):
            excluded.append(dict(candidate, reason="返回 game_id 与当前游戏不符"))
            continue
        post_id = validate_post_id(candidate["post_id"])
        eligible.append({
            "post_id": post_id, "subject": candidate["subject"], "game_id": GAMES[game],
            "source": candidate, "status": "pending", "completed": False,
            "run_dir": (Path(game) / post_id / "full_collection").as_posix(),
        })
    return {
        "discovery_dir": discovery_dir.relative_to(raw_root).as_posix(),
        "raw_sha256": digest(discovery_dir / "webHome_page1.json"),
        "tasks": eligible, "excluded": excluded,
    }


def run_batch(batch_dir, games=("genshin", "starrail", "zzz"), *, max_posts=3,
              max_pages=5, max_sub_pages=2, max_floors=3, raw_root=None):
    """任务清单 → 逐帖采集 → 批次进度。上限均按累计数量计算，恢复不轮换帖子。

    batches 字典保存游戏，tasks 列表保持发现顺序；每个帖子仍使用原单帖断点。
    批次只保存汇总与来源，真实分页进度以各帖 manifest.json 为准。
    """
    games = list(games)
    if not games or len(set(games)) != len(games) or any(g not in GAMES for g in games):
        raise ValueError("游戏列表为空、重复或包含不支持的游戏。")
    for limit in (max_posts, max_pages, max_sub_pages, max_floors):
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 0):
            raise ValueError("上限必须是非负整数或 None。")
    raw_root = project_path(raw_root if raw_root is not None else RAW_DIR.parent)
    batch_dir = project_path(batch_dir)
    path = batch_dir / "batch_manifest.json"
    with run_lock(batch_dir):
        if path.exists():
            batch = read_json(path)
            if (batch.get("schema_version") != 1 or batch.get("games") != games
                    or batch.get("source") != DISCOVERY_SOURCE):
                raise ValueError("批次游戏或格式不一致，请使用另一个 --batch-dir。")
        else:
            if any(p.name != ".collector.lock" for p in batch_dir.iterdir()):
                raise ValueError("新批次目录必须为空，避免覆盖已有文件。")
            batch = {
                "schema_version": 1, "platform": "miyoushe", "games": games,
                "source": DISCOVERY_SOURCE, "started_at": utc_now(),
                "pagination_verified": False, "batches": {}, "attempts": [],
                "completion_scope": "仅首页第 1 页推荐样本及当前条件下的评论；不是全站全量",
            }
        attempt = {"started_at": utc_now(), "max_posts_per_game": max_posts,
                   "max_pages": max_pages, "max_sub_pages": max_sub_pages, "max_floors": max_floors}
        batch["attempts"].append(attempt)
        batch.update(status="running", completed=False)
        batch.pop("error", None)
        save_json(batch, path)
        current = None
        try:
            for game in games:
                batch["active_game"] = game
                current = None
                if game not in batch["batches"]:
                    batch["batches"][game] = prepare_batch_game(game, raw_root)
                    save_json(batch, path)
                group = batch["batches"][game]
                discovery_dir = raw_root / group["discovery_dir"]
                check_digest(discovery_dir / "webHome_page1.json", group["raw_sha256"])
                source_tasks = load_discovery_snapshot(discovery_dir, game)
                expected = [t for t in source_tasks if str(t["game_id"]) == str(GAMES[game])]
                if [t["source"] for t in group["tasks"]] != expected:
                    raise ValueError("批次任务与发现快照不一致。")
                for index, task in enumerate(group["tasks"]):
                    current = None
                    # 上限是固定顺序中的前 N 个；不会每次跳过已完成帖而再选 N 个。
                    if max_posts is not None and index >= max_posts:
                        if task["status"] in ("pending", "deferred"):
                            task.update(status="deferred", stop_reason="达到本游戏累计帖子数上限")
                        continue
                    current = task
                    post_id = validate_post_id(task["source"]["post_id"])
                    expected_dir = (Path(game) / post_id / "full_collection").as_posix()
                    if task["post_id"] != post_id or task["run_dir"] != expected_dir:
                        raise ValueError("批次的帖子 ID 或输出路径与来源不一致。")
                    task.update(status="running", completed=False)
                    task.pop("error", None)
                    save_json(batch, path)
                    state = run_collection(
                        raw_root / expected_dir, max_pages, max_sub_pages, max_floors,
                        game=game, post_id=post_id,
                    )
                    task.update(
                        status=state["status"], completed=state["completed"],
                        stop_reason=state["stop_reason"], updated_at=utc_now(),
                        top_unique_comments=state["tasks"]["top"]["unique_comments"],
                        sub_unique_comments=sum(t["unique_comments"] for k, t in state["tasks"].items()
                                                if k.startswith("floor_")),
                        discovered_floors=sum(k.startswith("floor_") for k in state["tasks"]),
                    )
                    save_json(batch, path)
                current = None
            all_tasks = [t for group in batch["batches"].values() for t in group["tasks"]]
            nonempty = all(group["tasks"] for group in batch["batches"].values())
            batch["completed"] = bool(nonempty) and all(t["completed"] for t in all_tasks)
            batch["status"] = ("completed" if batch["completed"] else
                               "limited" if all_tasks else "no_posts")
        except (Exception, KeyboardInterrupt) as error:
            batch.update(status="interrupted", error=f"{type(error).__name__}: {error}")
            if current is not None:
                current.update(status="interrupted", completed=False, error=batch["error"])
            raise  # 不无限重试，不在 403/429 后继续轮询其他帖子。
        finally:
            batch["active_game"] = None
            batch["updated_at"] = utc_now()
            attempt.update(finished_at=utc_now(), status=batch["status"], error=batch.get("error"))
            save_json(batch, path)
            print("批次状态：", batch["status"], "；进度：", path, flush=True)
        return batch


def optional_limit(value):
    if value.lower() == "none":
        return None
    try:
        number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("请输入非负整数或 None") from error
    if number < 0:
        raise argparse.ArgumentTypeError("上限不能为负数")
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="配置文件；相对路径基于项目根目录")
    parser.add_argument("--raw-root", type=Path, help="临时覆盖米游社原始数据根目录（其下是游戏目录）")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--discover-posts", action="store_true", help="只发现首页第 1 页推荐帖子，不启动评论采集")
    mode.add_argument("--batch", action="store_true", help="复用发现清单并有限批量采集")
    parser.add_argument("--game", choices=[*GAMES, "all"], default="genshin")
    parser.add_argument("--post-id", type=validate_post_id, help="单帖模式的帖子 ID，原神默认使用已验证测试帖")
    parser.add_argument("--run-dir", type=Path, help="单帖续采目录；默认按游戏和帖子 ID 分开")
    parser.add_argument("--batch-dir", type=Path, help="批次进度目录；同目录重复运行自动恢复")
    parser.add_argument("--seed-run", type=Path, help="新目录首次运行时导入旧格式采样")
    parser.add_argument("--max-posts", type=optional_limit, default=argparse.SUPPRESS,
                        help="每游戏累计帖子数上限，默认读取配置中的 3")
    parser.add_argument("--max-pages", type=optional_limit, default=argparse.SUPPRESS)
    parser.add_argument("--max-sub-pages", type=optional_limit, default=argparse.SUPPRESS)
    parser.add_argument("--max-floors", type=optional_limit, default=argparse.SUPPRESS)
    mode.add_argument("--verify-only", action="store_true", help="只读核验原始页与断点，不发请求")
    args = parser.parse_args()
    try:
        config = load_config(args.config) if args.config else DEFAULT_CONFIG
        # 优先级：显式命令行参数 > 指定配置 > 项目默认配置。
        raw_root = (project_path(args.raw_root) if args.raw_root is not None else
                    config.miyoushe_root if args.config else None)
        for name, value in config.limits.items():
            if not hasattr(args, name):
                setattr(args, name, value)
        for name in ("run_dir", "batch_dir", "seed_run"):
            if getattr(args, name) is not None:
                setattr(args, name, project_path(getattr(args, name)))
    except (OSError, ValueError) as error:
        parser.error(str(error))
    games = list(GAMES) if args.game == "all" else [args.game]
    if (args.batch or args.discover_posts) and (args.post_id or args.run_dir or args.seed_run):
        parser.error("发现/批次模式不接受 --post-id、--run-dir 或 --seed-run。")
    if args.batch_dir and not args.batch:
        parser.error("--batch-dir 仅用于 --batch。")
    print("配置文件：", config.source, flush=True)
    print("米游社原始数据目录：", raw_root or RAW_DIR.parent, flush=True)
    if args.discover_posts:
        for game in games:
            discover_posts(game=game, raw_root=raw_root)
    elif args.batch:
        batch_dir = args.batch_dir or (raw_root or RAW_DIR.parent) / "_batches" / "_".join(games)
        run_batch(batch_dir, games, max_posts=args.max_posts, max_pages=args.max_pages,
                  max_sub_pages=args.max_sub_pages, max_floors=args.max_floors, raw_root=raw_root)
    else:
        if args.game == "all":
            parser.error("--game all 仅用于 --discover-posts 或 --batch。")
        if args.game != "genshin" and not args.post_id:
            parser.error("星穹铁道/绝区零单帖模式需要 --post-id。")
        post_id = args.post_id or POST_ID
        run_dir = args.run_dir or game_raw_dir(args.game, raw_root) / post_id / "full_collection"
        if args.verify_only:
            state = deepcopy(read_json(run_dir / "manifest.json"))
            if state["game"] != args.game or state["post_id"] != post_id:
                parser.error("核验目录与 --game / --post-id 不一致。")
            audit(state, run_dir)
        else:
            run_collection(run_dir, args.max_pages, args.max_sub_pages, args.max_floors,
                           args.seed_run, game=args.game, post_id=post_id)


if __name__ == "__main__":
    main()
