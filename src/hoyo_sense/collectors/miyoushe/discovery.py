"""首页推荐列表分页发现：保存所有原始 JSON，候选去重仅用于任务调度。"""

from pathlib import Path

from ...endpoints import MIYOUSHE_ENDPOINTS
from ...games import MIYOUSHE_GAME_IDS
from ...storage import utc_now, read_json, save_json, digest, check_digest, run_lock


SOURCE = "webHome/recommended_posts"


def discover_home_pages(game, game_root, max_pages, request_json, build_tasks, seed_dir=None):
    """按累计页数恢复发现。request_json 由入口传入，继续共用原请求间隔和超时。

    game_root 是 data/raw/miyoushe/<game>，pages 列表中的路径相对它保存。
    seed_dir 可指向已有的第一页发现目录；直接引用旧 raw，不复制或重写。
    返回状态和所有已落盘页面重建的候选列表，不采集正文或评论。
    """
    if game not in MIYOUSHE_GAME_IDS:
        raise ValueError("发现任务的游戏不支持。")
    if max_pages is not None and (isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages < 0):
        raise ValueError("发现页数上限必须是非负整数或 None。")
    game_root = Path(game_root).resolve()
    run_dir = game_root / "_discovery" / "home_pages"
    manifest_path = run_dir / "discovery_manifest.json"
    endpoint = MIYOUSHE_ENDPOINTS["web_home"]

    def params_for(page):
        return {"gids": MIYOUSHE_GAME_IDS[game], "page": page, "page_size": 20}

    def raw_path(filename):
        path = (game_root / filename).resolve()
        if not path.is_relative_to(game_root):
            raise ValueError("发现页面路径超出当前游戏目录。")
        return path

    def page_candidates(result, page_number, fetched_at, filename):
        # 复用已有字段解析，随后把来源指向本页 raw，保留本页所有原始记录。
        tasks, record_count = build_tasks(result, fetched_at, params_for(page_number))
        for task in tasks:
            task["raw_file"] = filename
        return tasks, record_count

    def register(state, pending, result):
        filename = pending["file"]
        tasks, count = page_candidates(result, pending["page"], pending["started_at"], filename)
        ids = [task["post_id"] for task in tasks]
        previous_ids = {post_id for p in state["pages"] for post_id in p["post_ids"]}
        new_count = len(set(ids) - previous_ids)
        state["pages"].append({
            "page": pending["page"], "file": filename, "params": pending["params"],
            "fetched_at": pending["started_at"], "sha256": digest(raw_path(filename)),
            "record_count": count, "post_ids": ids, "new_post_count": new_count,
        })
        state["pending_request"] = None
        state["next_page"] = pending["page"] + 1
        if not count:
            state.update(status="exhausted", exhausted=True, stop_reason="当前推荐列表返回空页")
        elif not new_count:
            state.update(status="stalled", exhausted=False,
                         stop_reason="当前页没有新帖子 ID，停止防止重复翻页；raw 仍完整保留")
        else:
            state.update(status="pending", exhausted=False, stop_reason="等待下一页")

    with run_lock(run_dir):
        if manifest_path.exists():
            state = read_json(manifest_path)
            if (state.get("schema_version") != 1 or state.get("game") != game
                    or state.get("source") != SOURCE or state.get("endpoint") != endpoint):
                raise ValueError("分页发现断点的来源或游戏不一致。")
        else:
            if any(p.name != ".collector.lock" for p in run_dir.iterdir()):
                raise ValueError("分页发现目录已有未登记文件，停止以免覆盖。")
            state = {
                "schema_version": 1, "kind": "paged_post_discovery", "source": SOURCE,
                "endpoint": endpoint, "game": game, "started_at": utc_now(),
                "pages": [], "next_page": 1, "pending_request": None,
                "status": "pending", "exhausted": False, "stop_reason": "尚未开始",
                "attempts": [],
                "scope": "仅当前首页推荐列表，不保证全站或所有板块、历史帖子覆盖",
            }
            if seed_dir is not None:
                seed_dir = Path(seed_dir).resolve()
                legacy = read_json(seed_dir / "discovery_manifest.json")
                if (legacy.get("status") != "discovered" or legacy.get("source") != SOURCE
                        or legacy.get("request_params") != params_for(1)):
                    raise ValueError("旧首页快照不满足第一页导入条件。")
                filename = (seed_dir / "webHome_page1.json").relative_to(game_root).as_posix()
                pending = {"file": filename, "page": 1, "params": params_for(1),
                           "started_at": legacy["discovered_at"]}
                register(state, pending, read_json(raw_path(filename)))
                state["seed_dir"] = seed_dir.relative_to(game_root).as_posix()
            save_json(state, manifest_path)

        # 重算所有来源。任务清单是可重建的索引，raw 才是原始数据。
        candidates_by_id = {}
        seen_ids = set()
        for index, page in enumerate(state["pages"], start=1):
            if page["page"] != index or page["params"] != params_for(index):
                raise ValueError("发现页码或请求参数不衔接。")
            check_digest(raw_path(page["file"]), page["sha256"])
            tasks, count = page_candidates(read_json(raw_path(page["file"])), index,
                                           page["fetched_at"], page["file"])
            ids = [task["post_id"] for task in tasks]
            if (count != page["record_count"] or ids != page["post_ids"]
                    or len(set(ids) - seen_ids) != page["new_post_count"]):
                raise ValueError("发现原始页与断点统计不一致。")
            seen_ids.update(ids)
            for task in tasks:
                candidates_by_id.setdefault(task["post_id"], task)
            if index < len(state["pages"]) and (not count or not page["new_post_count"]):
                raise ValueError("发现停止页之后仍有登记页，需要检查。")
        if state["next_page"] != len(state["pages"]) + 1:
            raise ValueError("发现断点下一页编号错误。")

        attempt = {"started_at": utc_now(), "max_pages": max_pages}
        state["attempts"].append(attempt)
        state.pop("error", None)
        try:
            pending = state.get("pending_request")
            if pending:
                expected = f"_discovery/home_pages/page_{state['next_page']:06d}.json"
                if (pending["page"] != state["next_page"] or pending["file"] != expected
                        or pending["params"] != params_for(state["next_page"])):
                    raise ValueError("待请求的发现页与断点不一致。")
                if raw_path(pending["file"]).exists():
                    register(state, pending, read_json(raw_path(pending["file"])))
                    save_json(state, manifest_path)
                # 原始页尚未落盘时，循环会使用相同页码重新请求，不跳页。
            # 中断状态从最后一页恢复判断，不把异常中断误当作列表遍历结束。
            if state["pages"]:
                last = state["pages"][-1]
                if not last["record_count"]:
                    state.update(status="exhausted", exhausted=True)
                elif not last["new_post_count"]:
                    state.update(status="stalled", exhausted=False)
                else:
                    state.update(status="pending", exhausted=False, stop_reason="等待下一页")
            while state["status"] not in ("exhausted", "stalled"):
                if max_pages is not None and len(state["pages"]) >= max_pages:
                    state.update(status="limited", stop_reason="达到累计发现页数上限")
                    break
                number = state["next_page"]
                filename = f"_discovery/home_pages/page_{number:06d}.json"
                if raw_path(filename).exists():
                    raise ValueError("发现未登记原始页，不覆盖也不盲目重请求。")
                pending = {"page": number, "file": filename, "params": params_for(number),
                           "started_at": utc_now()}
                state.update(status="running", pending_request=pending)
                save_json(state, manifest_path)
                result = request_json(endpoint, pending["params"])
                save_json(result, raw_path(filename))
                register(state, pending, result)
                save_json(state, manifest_path)
                print(f"{game} 帖子发现第 {number} 页：{state['pages'][-1]['record_count']} 条，"
                      f"新增任务 ID {state['pages'][-1]['new_post_count']}；{state['status']}", flush=True)
        except (Exception, KeyboardInterrupt) as error:
            state.update(status="interrupted", exhausted=False, error=f"{type(error).__name__}: {error}")
            raise
        finally:
            state["updated_at"] = utc_now()
            attempt.update(finished_at=utc_now(), status=state["status"], error=state.get("error"))
            save_json(state, manifest_path)

        # 新请求页和崩溃后补登记页也从 raw 重建，保留首次发现顺序。
        for page in state["pages"]:
            tasks, _ = page_candidates(read_json(raw_path(page["file"])), page["page"],
                                       page["fetched_at"], page["file"])
            for task in tasks:
                candidates_by_id.setdefault(task["post_id"], task)
        tasks = list(candidates_by_id.values())
        save_json({"source": SOURCE, "game": game, "tasks": tasks,
                   "unique_post_count": len(tasks)}, run_dir / "discovered_posts.json")
        return state, tasks
