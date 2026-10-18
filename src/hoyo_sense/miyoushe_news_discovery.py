"""官方资讯 type=3 列表发现：按响应游标续页，不调度正文或评论采集。"""

from datetime import datetime, timezone
from pathlib import Path

from .endpoints import MIYOUSHE_ENDPOINTS
from .games import MIYOUSHE_GAME_IDS
from .storage import utc_now, read_json, save_json, digest, check_digest, run_lock


SOURCE = "getNewsList/list"
NEWS_TYPE = 3
PAGE_SIZE = 20


def news_cursor(value):
    """游标只传递、不加减；浮点数可能损失 ID 精度，所以拒绝它。"""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("资讯分页标记必须是字符串或整数。")
    return str(value)


def request_params(game, last_id):
    # 第一页也保留 last_id=""，与浏览器已观察到的请求完全一致。
    return {
        "client_type": 4, "gids": MIYOUSHE_GAME_IDS[game], "last_id": last_id,
        "page_size": PAGE_SIZE, "type": NEWS_TYPE,
    }


def parse_news_page(result, game, params, fetched_at, filename):
    """列表只提供发现信息。空正文、uid=0、stat=null 不解释为真实详情。"""
    if not isinstance(result, dict) or result.get("retcode") != 0:
        raise ValueError("资讯接口业务失败或 JSON 最外层结构错误。")
    data = result["data"]
    if not isinstance(data["list"], list) or not isinstance(data.get("is_last"), bool):
        raise ValueError("资讯响应 list 或 is_last 类型错误。")
    by_id = {}
    for index, item in enumerate(data["list"], start=1):
        post = item["post"]
        post_id = post["post_id"]
        if isinstance(post_id, bool) or not isinstance(post_id, (str, int)):
            raise ValueError("资讯帖子 ID 必须是数字字符串或整数。")
        post_id = str(post_id)
        if not post_id.isascii() or not post_id.isdecimal():
            raise ValueError("资讯帖子 ID 必须是 ASCII 数字。")
        if not isinstance(post["subject"], str):
            raise ValueError("资讯标题不是字符串。")
        game_id = post["game_id"]
        if isinstance(game_id, bool) or not isinstance(game_id, (str, int)):
            raise ValueError("资讯记录 game_id 类型错误。")
        if post_id not in by_id:
            by_id[post_id] = {
                "post_id": post_id, "subject": post["subject"], "game_id": game_id,
                # game 是本次请求的上下文；game_id 保留接口实际返回值（样本为 0）。
                "game": game, "game_assignment": "request_context",
                "requires_detail_validation": True, "discovery_status": "discovered",
                "source": SOURCE, "endpoint": MIYOUSHE_ENDPOINTS["news_list"],
                "discovered_at": fetched_at, "request_params": dict(params),
                "raw_file": filename, "source_indices": [],
            }
        by_id[post_id]["source_indices"].append(index)
    return list(by_id.values()), len(data["list"]), news_cursor(data.get("last_id")), data["is_last"]


def discover_news_pages(game, game_root, max_pages, request_json, *, preview_saved_pages=True):
    """输入游戏目录和累计页数上限，返回发现断点、按 ID 去重的候选列表。

    仅验证了原神 + type=3。状态和 raw 单独放在 _discovery/news_type_3，
    不读取、覆盖 home_pages、批次清单或任何单帖 manifest。
    """
    if game != "genshin":
        raise ValueError("本阶段官方资讯接口仅验证原神，请使用 --game genshin。")
    if max_pages is not None and (
        isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages < 1
    ):
        raise ValueError("资讯发现页数上限必须为正整数或 None。")
    game_root = Path(game_root).resolve()
    run_dir = game_root / "_discovery" / f"news_type_{NEWS_TYPE}"
    manifest_path = run_dir / "discovery_manifest.json"
    endpoint = MIYOUSHE_ENDPOINTS["news_list"]

    def filename_for(number):
        return f"_discovery/news_type_{NEWS_TYPE}/page_{number:06d}.json"

    def read_page(record):
        # 路径由当前页序号生成，断点中不接受任意文件路径。
        expected = filename_for(record["page"])
        if record["file"] != expected:
            raise ValueError("资讯页面文件路径与页序号不一致。")
        path = game_root / expected
        check_digest(path, record["sha256"])
        return read_json(path)

    def check_next(count, next_id, is_last, used_cursors, new_count):
        if is_last:
            return
        if not count:
            raise ValueError("资讯列表为空但 is_last=False，异常停止，不能标记遍历结束。")
        if next_id in (None, ""):
            raise ValueError("资讯下一分页标记缺失。")
        if next_id in used_cursors:
            raise ValueError("资讯分页标记重复。")
        if not new_count:
            raise ValueError("资讯整页没有新增帖子 ID，停止检查分页；原始页保留。")

    def rebuild(state):
        # 字典按首次发现顺序去重；原始响应中的重复记录全部保留。
        by_id, used_cursors = {}, set()
        expected_cursor = ""
        finished = False
        for number, record in enumerate(state["pages"], start=1):
            if finished or record["page"] != number:
                raise ValueError("资讯页次序错误，或结束页后仍有记录。")
            params = request_params(game, expected_cursor)
            if record["params"] != params or record["request_last_id"] != expected_cursor:
                raise ValueError("资讯请求参数或分页链不衔接。")
            tasks, count, next_id, finished = parse_news_page(
                read_page(record), game, params, record["fetched_at"], record["file"],
            )
            ids = [task["post_id"] for task in tasks]
            new_count = len(set(ids) - set(by_id))
            if (record["post_ids"] != ids or record["record_count"] != count
                    or record["new_post_count"] != new_count
                    or record["response_last_id"] != next_id or record["is_last"] != finished):
                raise ValueError("资讯断点统计与原始响应不一致。")
            used_cursors.add(expected_cursor)
            check_next(count, next_id, finished, used_cursors, new_count)
            for task in tasks:
                by_id.setdefault(task["post_id"], task)
            expected_cursor = next_id
        if state["next_last_id"] != expected_cursor:
            raise ValueError("资讯断点的下一分页标记不一致。")
        return by_id, used_cursors, finished

    def register(state, pending, by_id, used_cursors):
        path = game_root / pending["file"]
        # 从 raw 的落盘时间恢复采集时间，不能用“恢复程序的当前时间”冒充旧页发现时间。
        fetched_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
        tasks, count, next_id, is_last = parse_news_page(
            read_json(path), game, pending["params"], fetched_at, pending["file"],
        )
        ids = [task["post_id"] for task in tasks]
        new_count = len(set(ids) - set(by_id))
        record = {
            "page": pending["page"], "file": pending["file"], "params": pending["params"],
            "request_last_id": pending["params"]["last_id"], "response_last_id": next_id,
            "record_count": count, "post_ids": ids, "new_post_count": new_count,
            "duplicate_records": count - new_count, "is_last": is_last,
            "fetched_at": fetched_at, "sha256": digest(path),
        }
        state["pages"].append(record)
        state.update(next_last_id=next_id, pending_request=None, completed=is_last)
        used_cursors.add(record["request_last_id"])
        check_next(count, next_id, is_last, used_cursors, new_count)
        for task in tasks:
            by_id.setdefault(task["post_id"], task)
        state.update(
            status="completed" if is_last else "pending",
            stop_reason="当前资讯列表接口返回 is_last=True" if is_last else "等待下一页",
        )
        print(f"\n{game} 官方资讯第 {record['page']} 页：{count} 条，新增 {new_count} 帖；"
              f"is_last={is_last}，下一游标={next_id}", flush=True)

    with run_lock(run_dir):
        if manifest_path.exists():
            state = read_json(manifest_path)
            if (state.get("schema_version") != 1 or state.get("source") != SOURCE
                    or state.get("game") != game or state.get("endpoint") != endpoint
                    or state.get("base_params") != request_params(game, "")):
                raise ValueError("资讯断点来源、游戏或请求配置不一致。")
        else:
            if any(path.name != ".collector.lock" for path in run_dir.iterdir()):
                raise ValueError("资讯发现目录已有未登记文件，不覆盖。")
            state = {
                "schema_version": 1, "kind": "paged_news_discovery", "source": SOURCE,
                "game": game, "endpoint": endpoint, "base_params": request_params(game, ""),
                "started_at": utc_now(), "pages": [], "next_last_id": "",
                "pending_request": None, "status": "pending", "completed": False,
                "collection_started": False, "attempts": [],
                "scope": "仅原神官方资讯 type=3 列表，非原神全站或玩家讨论帖",
            }
            save_json(state, manifest_path)
        attempt = {"started_at": utc_now(), "max_pages": max_pages}
        state["attempts"].append(attempt)
        state.pop("error", None)
        try:
            by_id, used_cursors, finished = rebuild(state)
            state.update(status="completed" if finished else "pending", completed=finished)
            pending = state.get("pending_request")
            if pending is not None:
                number = len(state["pages"]) + 1
                if (finished or pending["page"] != number or pending["file"] != filename_for(number)
                        or pending["params"] != request_params(game, state["next_last_id"])):
                    raise ValueError("资讯待请求页与断点不一致。")
                if (game_root / pending["file"]).exists():
                    # raw 已保存但断点没更新时，直接登记，不再次请求该页。
                    register(state, pending, by_id, used_cursors)
                    save_json(state, manifest_path)
            while not state["completed"]:
                if max_pages is not None and len(state["pages"]) >= max_pages:
                    state.update(status="limited", stop_reason="达到累计资讯发现页数上限")
                    break
                number = len(state["pages"]) + 1
                filename = filename_for(number)
                path = game_root / filename
                if path.exists():
                    raise ValueError("资讯发现有未登记文件，不覆盖也不重复请求。")
                pending = {
                    "page": number, "file": filename,
                    "params": request_params(game, state["next_last_id"]), "started_at": utc_now(),
                }
                state.update(status="running", pending_request=pending)
                save_json(state, manifest_path)
                result = request_json(endpoint, pending["params"])
                save_json(result, path)
                register(state, pending, by_id, used_cursors)
                save_json(state, manifest_path)
            tasks = list(by_id.values())
            state.update(
                unique_post_count=len(tasks), total_records=sum(p["record_count"] for p in state["pages"]),
            )
            state["duplicate_records"] = state["total_records"] - len(tasks)
            save_json({
                "source": SOURCE, "game": game, "endpoint": endpoint,
                "unique_post_count": len(tasks), "tasks": tasks,
                "collection_started": False, "requires_detail_validation": True,
            }, run_dir / "discovered_posts.json")
        except (Exception, KeyboardInterrupt) as error:
            state.update(status="interrupted", completed=False,
                         stop_reason="资讯发现异常中断", error=f"{type(error).__name__}: {error}")
            raise
        finally:
            state["updated_at"] = utc_now()
            attempt.update(finished_at=utc_now(), status=state["status"], error=state.get("error"))
            save_json(state, manifest_path)
        print(f"\n官方资讯候选共 {len(tasks)} 帖，状态 {state['status']}；"
              f"已复用 {len(state['pages'])} 页原始数据。", flush=True)
        # 恢复已有断点时同样展示每页清单，让用户看见本次发现范围，不必重新发请求。
        # 批次只打印汇总，避免数百条候选预览遮住“开始采评论”的提示。
        # 独立发现模式才逐页预览；这些都是本地读取，不是额外网络请求。
        for record in state["pages"] if preview_saved_pages else []:
            page_tasks, _, _, _ = parse_news_page(
                read_page(record), game, record["params"], record["fetched_at"], record["file"],
            )
            print(f"第 {record['page']} 页候选预览（本页按 ID 去重）：", flush=True)
            for task in page_tasks:
                print(f"  {task['post_id']}  game_id={task['game_id']}  {task['subject']}", flush=True)
        print("任务清单：", run_dir / "discovered_posts.json", flush=True)
        print("仅发现候选，未启动正文或评论采集。", flush=True)
        return state, tasks
