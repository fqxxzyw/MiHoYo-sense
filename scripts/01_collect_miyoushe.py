"""单帖原始数据采集：逐页保存，断点恢复，仅 is_last=True 表示接口遍历结束。"""

import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
from typing import Any

import requests

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
PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw" / "miyoushe" / "genshin"
DEFAULT_RUN_DIR = RAW_DIR / POST_ID / "full_collection"


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
    if not isinstance(result, dict) or result.get("retcode") != 0:
        raise ValueError("接口业务失败或 JSON 最外层结构错误。")


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


def discover_posts(discovery_root=None):
    """仅请求一次首页第 1 页，每次独立保存，不打开或更新单帖采集断点。"""
    started = datetime.now(timezone.utc)
    root = Path(discovery_root) if discovery_root is not None else RAW_DIR / "_discovery"
    run_dir = root / started.strftime("%Y%m%d_%H%M%S_%f_UTC")
    run_dir.mkdir(parents=True, exist_ok=False)
    params = {"gids": GAME_ID, "page": 1, "page_size": 20}
    manifest = {
        "kind": "post_discovery", "source": DISCOVERY_SOURCE, "endpoint": WEB_HOME_URL,
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
    print("仅展示发现结果；评论采集未启动。")
    print("任务清单：", (run_dir / manifest["task_file"]).resolve())
    return run_dir, tasks


def cursor(value):
    """分页标记不参与算术；拒绝浮点数，避免长 ID 精度丢失。"""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("分页标记必须是字符串或整数。")
    return str(value)


def new_task(kind, floor_id=None, parent_reply_id=None) -> dict[str, Any]:
    # 字典保存任务属性；pages 列表按请求顺序记录每页的来源和结果。
    return {
        "kind": kind, "floor_id": floor_id, "parent_reply_id": parent_reply_id,
        "status": "pending", "completed": False, "stop_reason": "尚未开始",
        "next_last_id": None, "pending_request": None, "pages": [],
        "pages_saved": 0, "total_records": 0, "unique_comments": 0, "duplicate_records": 0,
    }


def page_file(task, number):
    if task["kind"] == "top":
        return f"comments_{POST_ID}_hot_page{number}.json"
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
        if not reply.get("reply_id") or str(reply["post_id"]) != POST_ID:
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
    rebuilt = new_task(task["kind"], task["floor_id"], task["parent_reply_id"])
    seen_ids, used_cursors = set(), set()
    for record in task["pages"]:
        filename = page_file(rebuilt, len(rebuilt["pages"]) + 1)
        path = run_dir / filename
        if record["file"] != filename or cursor(record["request_last_id"]) != rebuilt["next_last_id"]:
            raise ValueError("断点中的文件次序或请求分页标记不衔接。")
        if record.get("sha256") and digest(path) != record["sha256"]:
            raise ValueError(f"已保存页面发生修改：{path}")
        if rebuilt["completed"]:
            raise ValueError("is_last=True 后仍有页面，需要检查。")
        result = read_json(path)
        data = page_data(result, task)
        if cursor(record["response_last_id"]) != cursor(data.get("last_id")):
            raise ValueError("断点分页标记与原始响应不一致。")
        append_page(rebuilt, result, path, rebuilt["next_last_id"],
                    seen_ids, used_cursors, record.get("fetched_at"))
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
        params = {"gids": GAME_ID, "post_id": POST_ID, "size": PAGE_SIZE}
        if task["kind"] == "top":
            url = COMMENTS_URL
            params["is_hot"] = "true"
        else:
            url = SUB_REPLIES_URL
            params["floor_id"] = task["floor_id"]
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
        if task.get("sha256") and digest(path) != task["sha256"]:
            raise ValueError("已保存帖子文件发生修改。")
        result = read_json(path)
    else:
        if task["completed"]:
            raise FileNotFoundError("断点记录的帖子文件缺失。")
        result = request_json(POST_URL, {"gids": GAME_ID, "post_id": POST_ID, "read": 1})
        save_json(result, path)
    check_result(result)
    post = result["data"]["post"]["post"]
    if str(post["post_id"]) != POST_ID:
        raise ValueError("帖子归属不正确。")
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
            task = state["tasks"].setdefault(key, new_task("sub", floor_id, parent_id))
            if task["parent_reply_id"] not in (None, parent_id):
                raise ValueError("同一楼层出现不同的顶层 reply_id。")
            task.update(parent_reply_id=parent_id, observed_sub_reply_count=item.get("sub_reply_count"))


def initialize(run_dir, seed_run=None) -> dict[str, Any]:
    path = run_dir / "manifest.json"
    if path.exists():
        state = read_json(path)
        if state.get("schema_version") != 2 or state.get("post_id") != POST_ID:
            raise ValueError("旧格式或其他帖子不能直接恢复；请用新目录和 --seed-run 导入旧采样。")
        if state["game_id"] != GAME_ID or state["page_size"] != PAGE_SIZE or state["sort"] != "hot":
            raise ValueError("采样配置与断点不一致。")
        return state
    if any(entry.name != ".collector.lock" for entry in run_dir.iterdir()):
        raise ValueError("新运行目录必须为空，避免覆盖已有数据。")
    state = {
        "schema_version": 2, "platform": "miyoushe", "game": "genshin",
        "post_id": POST_ID, "game_id": GAME_ID, "sort": "hot", "page_size": PAGE_SIZE,
        "started_at": utc_now(), "status": "pending", "completed": False,
        "completion_scope": "仅当前接口、热门顶层排序及已发现楼层；不保证全部历史、删除或折叠评论",
        "tasks": {
            "post": {"file": f"post_{POST_ID}.json", "status": "pending", "completed": False},
            "top": new_task("top"),
        }, "attempts": [],
    }
    if seed_run is not None:
        seed_run = Path(seed_run).resolve()
        legacy = read_json(seed_run / "manifest.json")
        if legacy.get("post_id") != POST_ID or legacy.get("sort") != "hot" or legacy.get("page_size") != PAGE_SIZE:
            raise ValueError("旧采样的帖子或采样条件不一致。")
        state["seed_run"] = str(seed_run)
        shutil.copy2(seed_run / f"post_{POST_ID}.json", run_dir / f"post_{POST_ID}.json")
        state["tasks"]["top"]["pages"] = legacy["comments"]["pages"]
        sub = legacy.get("sub_replies_test")
        if sub:
            floor_id = str(sub["floor_id"])
            summary = read_json(seed_run / f"sub_replies/floor_{floor_id}/summary.json")
            if summary != sub:
                raise ValueError("旧楼中楼 summary 与 manifest 不一致。")
            task = new_task("sub", floor_id)
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
            reconcile(task, run_dir)
            print(f"核验 {key}：{task['pages_saved']} 页，{task['unique_comments']} 个不同 ID，"
                  f"重复 {task['duplicate_records']}，接口结束 {task['completed']}")
        else:
            result = read_json(run_dir / task["file"])
            check_result(result)
            if str(result["data"]["post"]["post"]["post_id"]) != POST_ID:
                raise ValueError("帖子 ID 错误。")
            if task.get("sha256") and digest(run_dir / task["file"]) != task["sha256"]:
                raise ValueError("帖子文件校验失败。")


def run_collection(run_dir, max_pages=MAX_PAGES, max_sub_pages=MAX_SUB_PAGES,
                   max_floors=MAX_FLOORS, seed_run=None):
    """上限按累计页数计算。恢复同目录时，已保存页只读核验，不重复请求。"""
    for value in (max_pages, max_sub_pages, max_floors):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            raise ValueError("上限必须是非负整数或 None。")
    run_dir = Path(run_dir).resolve()
    with run_lock(run_dir):
        state = initialize(run_dir, seed_run)
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
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--discover-posts", action="store_true", help="只发现首页第 1 页推荐帖子，不启动评论采集")
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR, help="固定续采目录；再次运行自动恢复")
    parser.add_argument("--seed-run", type=Path, help="新目录首次运行时导入旧格式采样")
    parser.add_argument("--max-pages", type=optional_limit, default=MAX_PAGES)
    parser.add_argument("--max-sub-pages", type=optional_limit, default=MAX_SUB_PAGES)
    parser.add_argument("--max-floors", type=optional_limit, default=MAX_FLOORS)
    mode.add_argument("--verify-only", action="store_true", help="只读核验原始页与断点，不发请求")
    args = parser.parse_args()
    if args.discover_posts:
        discover_posts()
    elif args.verify_only:
        audit(deepcopy(read_json(args.run_dir / "manifest.json")), args.run_dir)
    else:
        run_collection(args.run_dir, args.max_pages, args.max_sub_pages, args.max_floors, args.seed_run)


if __name__ == "__main__":
    main()
