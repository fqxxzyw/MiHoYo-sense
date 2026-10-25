"""资讯列表 → 去重候选队列 → 已有单帖采集器；复用原批次目录及单帖断点。"""

from copy import deepcopy
from pathlib import Path

from ...games import MIYOUSHE_GAME_IDS
from .news_discovery import SOURCE, discover_news_pages
from ...storage import utc_now, read_json, save_json, run_lock


def post_id_string(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("批次帖子 ID 必须是数字字符串或整数。")
    value = str(value)
    if not value.isascii() or not value.isdecimal():
        raise ValueError("批次帖子 ID 必须是 ASCII 数字。")
    return value


def append_candidates(batch, candidates):
    """按帖子 ID 去重调度；sources 列表保留首页和资讯的多份发现来源。"""
    known = {task["post_id"]: task for task in batch["tasks"]}
    for candidate in candidates:
        post_id = post_id_string(candidate["post_id"])
        if candidate.get("source") == SOURCE:
            if candidate.get("game") != "genshin" or candidate["request_params"]["gids"] != 2:
                raise ValueError("资讯候选请求上下文与原神批次不符。")
        elif (candidate.get("source") != "webHome/recommended_posts"
              or str(candidate.get("game_id")) != "2"
              or candidate["request_params"]["gids"] != 2):
            raise ValueError("旧首页候选的来源或游戏不符。")
        if post_id not in known:
            task = {
                "post_id": post_id, "subject": candidate["subject"], "sources": [],
                "status": "pending", "completed": False, "detail_validated": False,
                "run_dir": f"{post_id}/full_collection",
            }
            batch["tasks"].append(task)
            known[post_id] = task
        if candidate not in known[post_id]["sources"]:
            known[post_id]["sources"].append(deepcopy(candidate))


def run_news_batch(batch_dir, game_root, request_json, collect, *,
                   max_discovery_pages=None, max_posts=None, max_pages=None,
                   max_sub_pages=None, max_floors=None, discovery_batch_pages=20,
                   retry_unavailable=False):
    """每发现一批资讯页就先采评论，清空本批队列后再继续发现。

    collect 是入口传入的原单帖采集函数，仍负责请求间隔、超时、原始保存和恢复。
    先核验详情 game_id=2，才允许该帖评论请求。列表中的原始 game_id=0 不被改写。
    """
    for value in (max_discovery_pages, max_posts, max_pages, max_sub_pages, max_floors):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            raise ValueError("批次上限必须是非负整数或 None。")
    if max_discovery_pages == 0:
        raise ValueError("资讯发现至少为一页，或设为 None。")
    if (isinstance(discovery_batch_pages, bool) or not isinstance(discovery_batch_pages, int)
            or discovery_batch_pages < 1):
        raise ValueError("每批资讯发现页数必须为正整数。")
    game_root, batch_dir = Path(game_root).resolve(), Path(batch_dir).resolve()
    legacy_path = game_root.parent / "_batches/genshin/batch_manifest.json"
    # 原三游戏混合批次可能是唯一旧索引；读取其原神候选，但不改写该文件。
    if not legacy_path.exists():
        combined_path = game_root.parent / "_batches/genshin_starrail_zzz/batch_manifest.json"
        if combined_path.exists():
            legacy_path = combined_path
    manifest_path = batch_dir / "batch_manifest.json"
    with run_lock(batch_dir):
        envelope = None

        def create_batch(legacy=None):
            result = {
                "schema_version": 1, "kind": "news_collection_batch", "game": "genshin",
                "source": SOURCE, "game_root": str(game_root), "started_at": utc_now(),
                "status": "pending", "completed": False, "tasks": [], "attempts": [],
                "scope": "当前官方资讯 type=3 与继承的首页候选，评论按热门接口遍历；非全站全部历史数据",
            }
            if legacy is not None:
                previous_news = legacy.get("news_collection")
                if previous_news is None and legacy.get("kind") == "news_collection_batch":
                    previous_news = legacy
                if previous_news is not None:
                    sources = [source for task in previous_news["tasks"] for source in task["sources"]]
                else:
                    sources = [task["source"] for task in legacy["batches"]["genshin"]["tasks"]]
                append_candidates(result, sources)
                result["legacy_batch"] = str(legacy_path)
                result["legacy_candidate_count"] = len(sources)
            return result

        def save_batch():
            if envelope is None:
                save_json(batch, manifest_path)
            else:
                # 保留旧首页候选、完成状态和历史执行记录；新调度状态放入同一文件的子字典。
                envelope["news_collection"] = batch
                envelope["active_collection"] = "news_collection"
                envelope["status"] = batch["status"]
                envelope["completed"] = batch["completed"]
                envelope["phase"] = batch.get("phase")
                envelope["updated_at"] = batch.get("updated_at", utc_now())
                envelope["active_game"] = "genshin" if batch["status"] == "running" else None
                save_json(envelope, manifest_path)

        if manifest_path.exists():
            stored = read_json(manifest_path)
            if "news_collection" in stored:
                envelope, batch = stored, stored["news_collection"]
            elif stored.get("kind") == "news_collection_batch":
                batch = stored
            elif (stored.get("source") == "webHome/recommended_posts"
                  and "genshin" in stored.get("batches", {})):
                envelope = stored
                batch = create_batch(stored)
            else:
                raise ValueError("批次文件格式不支持原神资讯续采。")
            if (batch.get("schema_version") != 1 or batch.get("kind") != "news_collection_batch"
                    or batch.get("game") != "genshin" or batch.get("source") != SOURCE
                    or batch.get("game_root") != str(game_root)):
                raise ValueError("资讯批次的来源、游戏或数据根目录不一致。")
        else:
            if any(path.name != ".collector.lock" for path in batch_dir.iterdir()):
                raise ValueError("资讯批次目录已有未登记文件，不覆盖。")
            batch = create_batch(read_json(legacy_path) if legacy_path.exists() else None)
            save_batch()
        attempt = {
            "started_at": utc_now(), "max_discovery_pages": max_discovery_pages,
            "max_posts": max_posts, "max_pages": max_pages,
            "max_sub_pages": max_sub_pages, "max_floors": max_floors,
            "discovery_batch_pages": discovery_batch_pages,
            "retry_unavailable": retry_unavailable,
        }
        batch["attempts"].append(attempt)
        batch.update(status="running", phase="discovery", completed=False, active_post_id=None)
        batch.pop("error", None)
        current = None

        def checkpoint():
            batch["updated_at"] = utc_now()
            batch["posts_completed"] = sum(task["completed"] for task in batch["tasks"])
            batch["posts_known"] = len(batch["tasks"])
            batch["posts_skipped"] = sum(task["status"] == "skipped_unavailable" for task in batch["tasks"])
            save_batch()

        checkpoint()
        try:
            discovery_path = game_root / "_discovery/news_type_3/discovery_manifest.json"
            # 旧程序可能已发现很多页却尚未采评论。恢复时只读取并核验已保存页，
            # 不再向后发现；先把这些页的帖子加入队列。待请求 raw 的恢复仍由发现器负责。
            if discovery_path.exists():
                print("原神资讯批次：先核验本地已保存列表并采集其评论，此阶段不请求新列表页。", flush=True)
            target_pages = 1 if discovery_path.exists() else discovery_batch_pages
            if max_discovery_pages is not None:
                target_pages = min(target_pages, max_discovery_pages)
            verified_this_run = set()
            while True:
                batch.update(phase="discovery", active_post_id=None,
                             discovery_target_pages=target_pages)
                checkpoint()
                print(f"原神资讯批次：阶段=发现列表，本轮累计目标 {target_pages} 页；"
                      f"每批 {discovery_batch_pages} 页，总上限={max_discovery_pages}。", flush=True)
                discovery, candidates = discover_news_pages(
                    "genshin", game_root, target_pages, request_json, preview_saved_pages=False,
                )
                append_candidates(batch, candidates)
                batch["discovery"] = {
                    "manifest": str(discovery_path),
                    "status": discovery["status"], "completed": discovery["completed"],
                    "pages_saved": len(discovery["pages"]), "next_last_id": discovery["next_last_id"],
                    "unique_post_count": len(candidates),
                }
                batch["phase"] = "collection"
                checkpoint()
                print(f"原神资讯批次：阶段=采集评论；已发现 {len(batch['tasks'])} 帖，"
                      f"已完成 {batch['posts_completed']} 帖。", flush=True)
                # tasks 是去重后的有序列表；同一帖子出现在后续资讯页时不会重复排队。
                for index, task in enumerate(batch["tasks"]):
                    current = None
                    if max_posts is not None and index >= max_posts:
                        if not task["completed"] and task["status"] != "skipped_unavailable":
                            task.update(status="limited", stop_reason="达到累计帖子数上限")
                        continue
                    current = task
                    post_id = post_id_string(task["post_id"])
                    if task["run_dir"] != f"{post_id}/full_collection":
                        raise ValueError("资讯批次单帖路径与帖子 ID 不一致。")
                    if not task["sources"]:
                        raise ValueError("批次候选缺少发现来源。")
                    if any(post_id_string(source["post_id"]) != post_id for source in task["sources"]):
                        raise ValueError("批次帖子 ID 与发现来源不一致。")
                    if task["status"] == "skipped_unavailable" and (
                            not retry_unavailable or post_id in verified_this_run):
                        continue
                    # 每次启动仍核验旧断点；同一次运行后续轮次不反复扫描已核验完成的帖子。
                    if post_id in verified_this_run and task["completed"]:
                        continue
                    # 仍调用原采集器核验已完成的本地断点，成功页不会再次请求。
                    task.update(status="running", completed=False)
                    task.pop("error", None)
                    batch["active_post_id"] = post_id
                    checkpoint()
                    print(f"\n原神资讯批次：第 {index + 1}/{len(batch['tasks'])} 帖 {post_id}；"
                          f"已完成 {batch['posts_completed']} 帖；{task['subject']}", flush=True)
                    state = collect(
                        game_root / task["run_dir"], max_pages=max_pages,
                        max_sub_pages=max_sub_pages, max_floors=max_floors,
                        game="genshin", post_id=post_id, require_game_id=True,
                        retry_unavailable=retry_unavailable,
                    )
                    if state["status"] == "skipped_unavailable":
                        task.update(status="skipped_unavailable", completed=False,
                                    stop_reason=state["stop_reason"],
                                    unavailable=deepcopy(state["unavailable"]), updated_at=utc_now())
                        verified_this_run.add(post_id)
                        checkpoint()
                        continue
                    task.pop("unavailable", None)
                    validated = state["tasks"]["post"].get("validated_game_id")
                    if str(validated) != str(MIYOUSHE_GAME_IDS["genshin"]):
                        raise ValueError("单帖详情未通过原神游戏归属核验。")
                    task.update(
                        status=state["status"], completed=state["completed"], detail_validated=True,
                        validated_game_id=validated, stop_reason=state["stop_reason"], updated_at=utc_now(),
                        top_unique_comments=state["tasks"]["top"]["unique_comments"],
                        sub_unique_comments=sum(value["unique_comments"] for key, value in state["tasks"].items()
                                                if key.startswith("floor_")),
                    )
                    if state["completed"]:
                        verified_this_run.add(post_id)
                    checkpoint()
                    if state["status"] == "interrupted":
                        raise RuntimeError(f"帖子 {post_id} 采集异常中断：{state['stop_reason']}")
                current = None
                batch["active_post_id"] = None
                # 本轮仍有未完成任务（帖子数、评论页数或楼层上限），就保留 limited
                # 断点并停止，不继续发现新的帖子。下次放宽上限后优先恢复这些任务。
                if not all(task["completed"] or task["status"] == "skipped_unavailable"
                           for task in batch["tasks"]):
                    break
                if discovery["completed"]:
                    break
                pages_saved = len(discovery["pages"])
                if max_discovery_pages is not None and pages_saved >= max_discovery_pages:
                    break
                if max_posts is not None and len(batch["tasks"]) >= max_posts:
                    break
                # max_discovery_pages 是累计总上限；discovery_batch_pages 仅决定切换频率。
                # None 取消总上限，但每轮仍只增加固定数量的发现页。
                target_pages = pages_saved + discovery_batch_pages
                if max_discovery_pages is not None:
                    target_pages = min(target_pages, max_discovery_pages)
            batch["collection_completed"] = bool(batch["tasks"]) and all(t["completed"] for t in batch["tasks"])
            batch["collection_drained"] = bool(batch["tasks"]) and all(
                t["completed"] or t["status"] == "skipped_unavailable" for t in batch["tasks"])
            batch["completed"] = discovery["completed"] and batch["collection_completed"]
            batch["status"] = ("completed" if batch["completed"] else
                               "finished_with_skips" if discovery["completed"] and batch["collection_drained"]
                               and batch["posts_skipped"] else "limited")
            batch["stop_reason"] = (
                "当前任务调度结束，但存在详情不可用的未完成帖子"
                if batch["status"] == "finished_with_skips" else
                "当前来源及评论接口遍历结束" if batch["completed"] else "达到设定上限")
            batch["phase"] = "finished"
        except (Exception, KeyboardInterrupt) as error:
            batch.update(status="interrupted", completed=False,
                         stop_reason="异常中断", error=f"{type(error).__name__}: {error}")
            if current is not None:
                current.update(status="interrupted", completed=False, error=batch["error"])
            raise
        finally:
            attempt.update(finished_at=utc_now(), status=batch["status"], error=batch.get("error"))
            batch["active_post_id"] = None
            checkpoint()
            print("原神资讯批次状态：", batch["status"], "；断点：", manifest_path, flush=True)
        return batch
