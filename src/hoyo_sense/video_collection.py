"""视频采集调度：先落盘响应，再推进断点；恢复时先处理已有队列。"""

from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4

from .storage import utc_now, read_json, save_json, check_digest, run_lock
from .video_http import CollectionError, redacted
from .video_platforms import VideoPlatform, identifier
from .video_storage import archive_response, immutable_json, restore_response


def new_task(kind, position):
    return {"kind": kind, "status": "pending", "completed": False, "next_cursor": position,
            "pending_request": None, "pages": [], "attempts": [], "stop_reason": "not_started"}


class VideoCollector:
    def __init__(self, config, game, client, raw_root=None):
        self.config, self.game, self.client = config, game, client
        self.adapter = VideoPlatform(config, game)
        self.root = Path(raw_root or config.raw_root) / game
        self.manifest = self.root / "_batch" / "manifest.json"
        self.state = None

    def checkpoint(self):
        self.state["updated_at"] = utc_now()
        save_json(self.state, self.manifest)

    def _load(self, resume):
        identity = {"platform": self.config.platform, "game": self.game,
                    "account": self.adapter.account, "params": self.config.params}
        if self.manifest.exists():
            if not resume:
                raise CollectionError("已有断点；请使用默认恢复模式或指定新的 --raw-root。")
            self.state = read_json(self.manifest)
            if self.state.get("schema_version") != 1 or self.state.get("identity") != identity:
                raise CollectionError("断点来源或业务参数发生变化，请改用新目录。")
        else:
            # 空 .gitkeep 只是项目预建目录的占位文件，不是采集成果，保留它并正常初始化。
            # 其他文件（包括非空 .gitkeep）仍须保护，不能在缺少断点时误认成功或覆盖。
            files = [p for p in self.root.rglob("*")
                     if p.is_file() and p != self.root / ".collector.lock"
                     and not (p.name == ".gitkeep" and p.stat().st_size == 0)]
            if files:
                raise CollectionError(f"目录 {self.root} 中有未登记文件且缺少断点 "
                                      f"{self.manifest}；请检查原始文件或使用新的 --raw-root。")
            self.state = {"schema_version": 1, "identity": identity, "created_at": utc_now(),
                          "scope": "指定官方账号作品与评论；不代表整个游戏社区",
                          "status": "pending", "completed": False, "works": {},
                          "discovery": new_task("discovery", self.adapter.initial("discovery"))}
            self.checkpoint()
        for task in self.tasks():
            for page in task["pages"]:
                for key, sha_key in (("body_file", "sha256"), ("parsed_file", "parsed_sha256")):
                    path = self.safe_path(page[key])
                    check_digest(path, page[sha_key])
                # 校验输入输出游标链，避免损坏断点在恢复时跳过页面。
            for previous, following in zip(task["pages"], task["pages"][1:]):
                if previous["output_cursor"] != following["input_cursor"]:
                    raise CollectionError("断点分页链不连续。")
            if task["pages"] and task["next_cursor"] != task["pages"][-1]["output_cursor"]:
                raise CollectionError("断点下一页游标与最后一页不匹配。")

    def safe_path(self, name):
        path = (self.root / name).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise CollectionError("断点中的路径超出当前游戏目录。")
        return path

    def tasks(self):
        yield self.state["discovery"]
        for work in self.state["works"].values():
            if work.get("detail"):
                yield work["detail"]
            yield work["comments"]
            yield from work["replies"].values()

    def add_work(self, work_id, metadata=None, source=None):
        work_id = identifier(work_id, bvid=self.config.platform == "bilibili")
        works = self.state["works"]
        if work_id not in works:
            works[work_id] = {"work_id": work_id, "metadata": metadata, "metadata_source": source,
                              "detail": (new_task("detail", None) if self.config.platform == "bilibili" else None),
                              "comments": new_task("comments", self.adapter.initial("comments")),
                              "replies": {}, "status": "pending", "completed": False}
        elif metadata is not None and works[work_id]["metadata"] is None:
            works[work_id].update(metadata=metadata, metadata_source=source)
        return works[work_id]

    def add_parent(self, work, parent):
        parent = identifier(parent)
        if parent not in work["replies"]:
            work["replies"][parent] = new_task("replies", self.adapter.initial("replies"))

    def _prefix(self, kind, work, parent):
        if kind == "discovery":
            return "_discovery"
        base = f"{work['work_id']}/full_collection"
        return f"{base}/replies/{parent}" if kind == "replies" else f"{base}/{kind}"

    def _one_page(self, task, work=None, parent=None):
        kind, position = task["kind"], task["next_cursor"]
        url, params = self.adapter.request(kind, position, work, parent)
        pending = task["pending_request"]
        if pending is not None and (pending["input_cursor"] != position or pending["params"] != redacted(params)):
            raise CollectionError("待恢复请求与当前游标或参数不一致。")
        if pending is None:
            pending = {"input_cursor": position, "params": redacted(params), "stem": None}
            task["pending_request"] = pending
            self.checkpoint()

        def archive(body, metadata):
            stem = f"{self._prefix(kind, work, parent)}/{uuid4().hex}"
            pending["stem"] = stem
            self.checkpoint()  # 先登记即将写入的文件名，支持落盘后断点未推进的中断恢复。
            record = archive_response(self.root, stem, body, {
                **metadata, "platform": self.config.platform, "game": self.game, "kind": kind,
                "endpoint": url, "work_id": work["work_id"] if work else None,
                "request_parent_comment_id": parent, "input_cursor": position,
            })
            task["attempts"].append(record)
            self.checkpoint()
            return record

        stem = pending.get("stem")
        if stem and self.safe_path(stem + ".http.json").exists():
            metadata = read_json(self.safe_path(stem + ".http.json"))
            record = {"http_file": stem + ".http.json", **metadata}
            body, metadata, record = restore_response(self.root, record)
            if record not in task["attempts"]:
                task["attempts"].append(record)
        else:
            body, metadata, record = self.client.fetch(kind, url, params, archive)
        # 失败响应也完整保留，但永远不会计入 pages 或推动游标。
        value = None
        try:
            if metadata["http_status"] != 200:
                raise CollectionError(f"HTTP {metadata['http_status']}；响应已保留，分页未推进。")
            value = self.adapter.decode(kind, body)
            parsed = self.adapter.parse(kind, value, position, work, parent)
            ids = ([self.adapter.item_id(kind, x) for x in parsed.items] if kind != "detail"
                   else [work["work_id"]])
            roots = self.adapter.reply_candidates(parsed.items) if kind == "comments" else []
        except (ValueError, KeyError, TypeError, AttributeError, UnicodeError, IndexError) as error:
            task["pending_request"] = None
            task.update(status="interrupted", completed=False,
                        stop_reason=str(error) if isinstance(error, CollectionError) else "invalid_response_structure")
            task["last_failure"] = {"http_file": record["http_file"], "reason": task["stop_reason"],
                                    "at": utc_now(), "input_cursor": position, "output_cursor": None,
                                    "record_count": None,
                                    "business_status": (value.get("code" if self.config.platform == "bilibili" else "status_code")
                                                        if isinstance(value, dict) else None)}
            self.checkpoint()
            raise CollectionError(task["stop_reason"]) from None

        parsed_file = record["http_file"].removesuffix(".http.json") + ".json"
        parsed_path = self.safe_path(parsed_file)
        if parsed_path.exists():
            # 只在上次中断已生成 JSON 时读取校验，绝不再次覆盖。
            if read_json(parsed_path) != value:
                raise CollectionError("恢复时已解析 JSON 与响应原文不一致。")
        else:
            immutable_json(parsed_path, value)
        from .storage import digest
        previous_ids = {item for page in task["pages"] for item in page["record_ids"]}
        new_ids = set(ids) - previous_ids
        used = {json.dumps(page["input_cursor"], sort_keys=True) for page in task["pages"]}
        used.add(json.dumps(position, sort_keys=True))
        # 有结束标记才能称接口结束；空页仍有更多、游标循环、重复页都属于停滞。
        if not parsed.items and not parsed.ended:
            status, reason = "stalled", "empty_page_with_more"
        elif not parsed.ended and (parsed.next_cursor is None or json.dumps(parsed.next_cursor, sort_keys=True) in used):
            status, reason = "stalled", "repeated_cursor"
        elif not parsed.ended and not new_ids:
            status, reason = "stalled", "no_new_record_ids"
        elif parsed.ended:
            status, reason = "completed", "endpoint_end_under_current_conditions"
        else:
            status, reason = "pending", "next_page_available"
        page = {**record, "parsed_file": parsed_file, "parsed_sha256": digest(parsed_path),
                "output_cursor": parsed.next_cursor, "record_count": len(parsed.items), "record_ids": ids,
                "new_record_count": len(new_ids), "business_status": 0 if kind != "detail" else "html_parsed",
                "reported_total": parsed.reported_total, "stop_reason": reason, **parsed.info}
        if self.config.platform == "bilibili" and kind == "comments":
            page["sort_mode"] = 3
        # 保存完整解析结果后再一次性提交页面、队列和游标。
        task["pages"].append(page)
        task.update(next_cursor=parsed.next_cursor, pending_request=None, status=status,
                    completed=parsed.ended and status == "completed", stop_reason=reason)
        if kind == "discovery":
            for item in parsed.items:
                self.add_work(self.adapter.item_id(kind, item), deepcopy(item), parsed_file)
        elif kind == "detail":
            work.update(parsed.info)
            work["metadata_source"] = parsed_file
        elif kind == "comments":
            for root in roots:
                self.add_parent(work, root)
        self.checkpoint()
        print(f"{kind}: 保存 {len(parsed.items)} 条，状态 {status}", flush=True)
        return page

    def collect_task(self, task, cap, work=None, parent=None):
        if task["completed"] or task["status"] == "stalled":
            return
        try:
            while not task["completed"]:
                # 已落盘但未提交的请求优先恢复，即使此次将上限调小也不能丢掉已取回的页。
                if not task["pending_request"] and cap is not None and len(task["pages"]) >= cap:
                    task.update(status="limited", stop_reason="cumulative_page_limit", completed=False)
                    self.checkpoint()
                    return
                self._one_page(task, work, parent)
                if task["status"] == "stalled":
                    return
        except (Exception, KeyboardInterrupt):
            # 最后一页可能已落盘并在内存提交；不能在随后写断点中断时抹掉结束标记。
            if not task["completed"] and task["status"] != "stalled":
                task.update(status="interrupted", completed=False)
            if task["stop_reason"] not in {"invalid_response_structure"} and not task.get("last_failure"):
                task["stop_reason"] = "request_or_local_write_interrupted"
            self.checkpoint()
            raise

    def _collect_work(self, work, parents):
        limits = self.config.limits
        if work["detail"]:
            self.collect_task(work["detail"], 1, work)
        for parent in parents:
            self.add_parent(work, parent)
        self.checkpoint()
        # 先恢复旧回复分支，再继续顶层分页；新发现的分支在分页后再次调度。
        self._collect_replies(work, parents)
        self.collect_task(work["comments"], limits["max_pages"], work)
        self._collect_replies(work, parents)
        detail_ok = work["detail"]["completed"] if work["detail"] else work["metadata"] is not None
        tasks = [work["comments"], *work["replies"].values()]
        work["completed"] = detail_ok and all(task["completed"] for task in tasks)
        work["status"] = ("completed" if work["completed"] else
                          "stalled" if any(t["status"] == "stalled" for t in tasks) else "limited")
        work["stop_reason"] = ("metadata_not_available_without_discovery" if not detail_ok else
                               "all_branches_ended" if work["completed"] else "unfinished_comment_or_reply_tasks")
        self.checkpoint()

    def _collect_replies(self, work, parents=()):
        limit = self.config.limits["max_floors"]
        # 指定父评论的测试只处理这些分支；普通模式按发现顺序使用累计前 N 个。
        selected = [(parent, work["replies"][parent]) for parent in dict.fromkeys(parents)] if parents else work["replies"].items()
        for index, (parent, task) in enumerate(selected):
            if limit is not None and index >= limit:
                if not task["completed"]:
                    task.update(status="limited", stop_reason="cumulative_parent_limit")
                continue
            self.collect_task(task, self.config.limits["max_sub_pages"], work, parent)

    def run(self, *, work_id=None, parents=(), discover_only=False, resume=True, metadata=None):
        if parents and not work_id:
            raise CollectionError("指定父评论时必须同时指定测试作品。")
        if work_id:
            work_id = identifier(work_id, bvid=self.config.platform == "bilibili")
        parents = [identifier(p) for p in parents]
        with run_lock(self.root):
            self._load(resume)
            self.state.update(status="running", completed=False)
            try:
                if work_id:
                    self.add_work(work_id, metadata, "explicit_local_metadata" if metadata else "explicit_work_id")
                    self.checkpoint()
                limits = self.config.limits
                self.state["run_scope"] = {"work_id": work_id, "discover_only": discover_only,
                                           "limits": dict(limits), "started_at": utc_now()}
                def process_queue():
                    queue = ([self.state["works"][work_id]] if work_id else self.state["works"].values())
                    for index, work in enumerate(queue):
                        if limits["max_posts"] is not None and index >= limits["max_posts"]:
                            break
                        # 普通模式累计队列前 N 项；指定作品模式只处理该作品。
                        self._collect_work(work, parents if work["work_id"] == work_id else ())
                if not discover_only:
                    process_queue()
                if not work_id:
                    discovery = self.state["discovery"]
                    # 每获取一页作品，就先处理队列，避免无上限发现阻塞所有评论采集。
                    while not discovery["completed"] and discovery["status"] != "stalled":
                        if (not discover_only and not discovery["pending_request"] and limits["max_posts"] is not None
                                and len(self.state["works"]) >= limits["max_posts"]):
                            discovery.update(status="limited", stop_reason="work_limit")
                            break
                        cap = limits["max_discovery_pages"]
                        if not discovery["pending_request"] and cap is not None and len(discovery["pages"]) >= cap:
                            discovery.update(status="limited", stop_reason="cumulative_page_limit")
                            break
                        self._one_page(discovery)
                        if not discover_only:
                            process_queue()
                        # 当前作品配额已满时不继续扩大队列。发现仍标 limited，不伪称已结束。
                        if (not discover_only and limits["max_posts"] is not None
                                and len(self.state["works"]) >= limits["max_posts"]):
                            if not discovery["completed"] and discovery["status"] != "stalled":
                                discovery.update(status="limited", stop_reason="work_limit")
                            break
                works = ([self.state["works"][work_id]] if work_id else list(self.state["works"].values()))
                relevant = [self.state["discovery"]] if discover_only else works
                ended = all(w["completed"] for w in relevant)
                if not work_id:
                    ended = ended and self.state["discovery"]["completed"]
                self.state.update(completed=ended, status="completed" if ended else
                                  "stalled" if any(t["status"] == "stalled" for t in relevant) else "limited",
                                  stop_reason="requested_scope_ended" if ended else "partial_collection")
            except (Exception, KeyboardInterrupt):
                discovery = self.state["discovery"]
                if discovery["pending_request"]:
                    discovery.update(status="interrupted", completed=False, stop_reason="request_or_local_write_interrupted")
                self.state.update(status="interrupted", completed=False, stop_reason="error_or_user_interrupt")
                self.checkpoint()
                raise
            self.checkpoint()
            return self.state
