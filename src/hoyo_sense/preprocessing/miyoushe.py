"""只读提取米游社单帖断点和已登记 raw；按 ID 合并分页及预览，不推断情感标签。"""

from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import unicodedata

from bs4 import BeautifulSoup

from ..games import MIYOUSHE_GAME_IDS
from ..storage import check_digest, save_json, utc_now


POST_COLUMNS = (
    "platform", "game", "post_id", "author_id", "title", "text",
    "text_source", "created_at", "updated_at", "like_count", "reply_count",
    "view_count", "image_count", "is_empty_text", "collection_status", "collection_completed",
)
COMMENT_COLUMNS = (
    "platform", "game", "post_id", "comment_id", "author_id", "root_comment_id",
    "floor_id", "raw_f_reply_id", "replied_to_user_id", "level", "text",
    "created_at", "updated_at", "like_count", "reply_count", "image_count",
    "is_empty_text", "is_deleted", "embedded_only", "occurrence_count", "has_text_variants",
    "collection_status", "collection_completed",
)


def write_flat_comment(writer, row):
    """CSV 单条评论严格占一行；换行和反斜杠可逆转义，JSONL 中的文字不变。"""
    writer.writerow({key: value.replace("\\", "\\\\").replace("\r", "\\r").replace("\n", "\\n")
                     if isinstance(value, str) else value for key, value in row.items()})


def export_flat_comments(snapshot_dir):
    """从已完成快照生成单行评论表，无需重新读取 raw 或请求网络。"""
    snapshot_dir = Path(snapshot_dir).resolve()
    report_path = snapshot_dir / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("status") != "completed":
        raise ValueError("只能展开 status=completed 的成功快照，不能使用中断结果。")
    output = snapshot_dir / "comments_flat.csv"
    temporary = output.with_name(output.name + ".tmp")
    counts = Counter()
    with (snapshot_dir / "comments.jsonl").open(encoding="utf-8") as source, temporary.open(
            "w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=COMMENT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for line in source:
            row = json.loads(line)
            if row["level"] not in ("top", "sub"):
                raise ValueError("评论层级必须为 top 或 sub。")
            write_flat_comment(writer, row)
            counts[row["level"]] += 1
        target.flush()
        os.fsync(target.fileno())
    expected = report["totals"]
    if (counts["top"] != expected["top_comments"] or counts["sub"] != expected["sub_comments"]
            or sum(counts.values()) != expected["comments"]):
        raise ValueError("展开后的评论数量与成功快照报告不一致。")
    temporary.replace(output)
    if output.name not in report["files"]:
        report["files"].append(output.name)
    report["flat_comments"] = dict(file=output.name, records=sum(counts.values()),
                                   top=counts["top"], sub=counts["sub"],
                                   physical_lines=sum(counts.values()) + 1,
                                   text_encoding="反斜杠转义；换行写成字面量\\n，每条评论一个物理行")
    save_json(report, report_path)
    return report["flat_comments"]


def identifier(value, *, optional=False):
    """长整数 ID 始终输出字符串，拒绝已经损失精度的浮点 ID。"""
    if optional and value in (None, ""):
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f"ID 必须是数字字符串或整数：{value!r}")
    result = str(value)
    if not result.isascii() or not result.isdecimal():
        raise ValueError(f"ID 必须是 ASCII 数字：{value!r}")
    return result


def normalize_text(value, *, html=False):
    """只整理 HTML/空白与 NFC；保留表情、标点、数字、URL 和停用词。"""
    if not isinstance(value, str):
        raise ValueError("文本必须是字符串。")
    if html:
        soup = BeautifulSoup(value, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        for tag in soup.find_all("br"):
            tag.replace_with("\n")
        for tag in soup.find_all("img"):
            tag.replace_with(tag.get("alt", ""))
        for tag in soup.find_all(["p", "div", "li", "h1", "h2", "h3", "blockquote"]):
            tag.insert_after("\n")
        value = soup.get_text()
    value = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    value = value.replace("\u200b", "").replace("\ufeff", "")
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", value)
    value = re.sub(r"[^\S\n]+", " ", value)
    return "\n".join(line.strip() for line in value.split("\n") if line.strip())


def post_text(post):
    raw = post.get("content") or ""
    text = normalize_text(raw, html=post.get("view_type") == 1)
    source = "content_html" if post.get("view_type") == 1 else "content"
    if not text and post.get("structured_content"):
        rich = post["structured_content"]
        try:
            rich = json.loads(rich) if isinstance(rich, str) else rich
        except json.JSONDecodeError:
            rich = None  # 原值保留；报告空正文，不把未知结构猜成文本。
        if isinstance(rich, list):
            text = normalize_text("".join(
                part["insert"] for part in rich
                if isinstance(part, dict) and isinstance(part.get("insert"), str)
            ))
            if text:
                source = "structured_content"
    return raw, text, source


def local_file(run_dir, value):
    if not isinstance(value, str) or not value:
        raise ValueError("断点缺少文件路径。")
    path = (run_dir / value).resolve()
    if run_dir not in path.parents:
        raise ValueError(f"断点引用了单帖目录以外的文件：{value}")
    return path


def checked_json(path, record):
    if record.get("sha256"):
        check_digest(path, record["sha256"])
    result = json.loads(path.read_text(encoding="utf-8"))
    if result.get("retcode") != 0:
        raise ValueError(f"原始响应不是成功结果：{path}")
    return result


def extract_run(manifest_path, raw_root):
    """每帖读取一份断点快照，只消费其已登记页；不改写或恢复采集断点。"""
    run_dir = manifest_path.parent.resolve()
    manifest_bytes = manifest_path.read_bytes()
    state = json.loads(manifest_bytes)
    game, post_id = state["game"], identifier(state["post_id"])
    if (state.get("schema_version") != 2 or game != manifest_path.parents[2].name
            or post_id != manifest_path.parents[1].name
            or state.get("game_id") != MIYOUSHE_GAME_IDS[game]):
        raise ValueError(f"单帖断点格式、目录或游戏不一致：{manifest_path}")
    common = dict(platform="miyoushe", game=game, post_id=post_id,
                  collection_status=state["status"], collection_completed=bool(state["completed"]))
    coverage = dict(common, source_manifest=manifest_path.relative_to(raw_root).as_posix(),
                    manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
                    manifest_updated_at=state.get("updated_at"), pages_read=0,
                    page_records=0, preview_records=0, pending_raw_excluded=[],
                    missing_root_comments=0)
    post_task = state["tasks"]["post"]
    post_path = local_file(run_dir, post_task["file"])
    post_row = None
    if post_path.exists():
        detail = checked_json(post_path, post_task)["data"]["post"]
        post = detail["post"]
        if (identifier(post["post_id"]) != post_id
                or str(post.get("game_id", state["game_id"])) != str(state["game_id"])):
            raise ValueError(f"详情帖子归属不一致：{post_path}")
        raw, text, source = post_text(post)
        stat = detail.get("stat") or {}
        images = post.get("images") or detail.get("image_list") or []
        post_row = dict(common, author_id=identifier(post.get("uid"), optional=True),
                        title=normalize_text(post.get("subject") or ""), title_raw=post.get("subject"),
                        text_raw=raw, text=text, text_source=source,
                        structured_content=post.get("structured_content"), images=images,
                        image_count=len(images), topics=detail.get("topics") or [],
                        created_at=post.get("created_at"), updated_at=post.get("updated_at"),
                        like_count=stat.get("like_num"), reply_count=stat.get("reply_num"),
                        view_count=stat.get("view_num"), is_empty_text=not bool(text),
                        source_file=post_path.relative_to(raw_root).as_posix())
    elif state["status"] != "skipped_unavailable" and post_task.get("completed"):
        raise FileNotFoundError(f"已完成详情的原始文件缺失：{post_path}")

    comments = {}
    previews = []

    def add(item, *, kind, root_id, floor_id, source):
        reply = item["reply"]
        cid = identifier(reply["reply_id"])
        if identifier(reply["post_id"]) != post_id:
            raise ValueError(f"评论所属帖子不一致：{source}")
        actual_floor = identifier(reply.get("floor_id"), optional=True)
        if floor_id is not None and actual_floor != identifier(floor_id):
            raise ValueError(f"楼中楼楼层不一致：{source}")
        raw = reply.get("content") or ""
        if cid in comments:
            row = comments[cid]
            if row["floor_id"] != actual_floor:
                raise ValueError(f"同一评论 ID 出现不同楼层：{cid}")
            row["occurrence_count"] += 1
            row["has_text_variants"] |= row["text_raw"] != raw
            row["sources"].append(source)
            return
        text = normalize_text(raw)
        stat = item.get("stat") or {}
        images = item.get("images") or []
        comments[cid] = dict(
            common, comment_id=cid, author_id=identifier(reply.get("uid"), optional=True),
            root_comment_id=root_id, floor_id=actual_floor,
            raw_f_reply_id=identifier(reply.get("f_reply_id"), optional=True),
            replied_to_user_id=identifier(reply.get("r_uid"), optional=True),
            level="top" if root_id is None else "sub", text_raw=raw, text=text,
            struct_content=reply.get("struct_content"), images=images, image_count=len(images),
            created_at=reply.get("created_at"), updated_at=reply.get("updated_at"),
            like_count=stat.get("like_num"), reply_count=stat.get("reply_num"),
            is_empty_text=not bool(text), is_deleted=bool(reply.get("is_deleted", False)),
            embedded_only=kind == "embedded_preview", occurrence_count=1,
            has_text_variants=False, sources=[source],
        )

    # 完整分页优先于顶层附带的楼中楼预览；同 ID 取第一个分页版本，所有来源保留。
    tasks = [("top", state["tasks"]["top"])] + [
        (key, task) for key, task in state["tasks"].items() if key.startswith("floor_")
    ]
    for key, task in tasks:
        root_id = identifier(task.get("parent_reply_id"), optional=True) if key != "top" else None
        if key != "top" and task.get("pages") and root_id is None:
            raise ValueError(f"楼中楼缺少顶层关联：{manifest_path} {key}")
        for record in task["pages"]:
            path = local_file(run_dir, record["file"])
            result = checked_json(path, record)
            items = result["data"]["list"]
            if not isinstance(items, list) or len(items) != record["record_count"]:
                raise ValueError(f"分页记录数与断点不一致：{path}")
            coverage["pages_read"] += 1
            coverage["page_records"] += len(items)
            for index, item in enumerate(items):
                source = dict(file=path.relative_to(raw_root).as_posix(),
                              pointer=f"/data/list/{index}", kind="top_page" if key == "top" else "sub_page",
                              fetched_at=record.get("fetched_at"))
                add(item, kind=source["kind"], root_id=root_id,
                    floor_id=task.get("floor_id"), source=source)
                for sub_index, preview in enumerate(item.get("sub_replies") or []):
                    previews.append((preview, root_id or identifier(item["reply"]["reply_id"]),
                                     item["reply"].get("floor_id"),
                                     dict(source, pointer=source["pointer"] + f"/sub_replies/{sub_index}",
                                          kind="embedded_preview")))
        pending = task.get("pending_request")
        if pending and local_file(run_dir, pending["file"]).exists():
            coverage["pending_raw_excluded"].append(pending["file"])
    for preview, root_id, floor_id, source in previews:
        add(preview, kind="embedded_preview", root_id=root_id, floor_id=floor_id, source=source)
    coverage["preview_records"] = len(previews)
    coverage["comments_exported"] = len(comments)
    coverage["missing_root_comments"] = sum(
        row["root_comment_id"] is not None and row["root_comment_id"] not in comments
        for row in comments.values()
    )
    return post_row, comments.values(), coverage


def extract_dataset(raw_root, output_dir, games=None):
    """生成一个全新快照；JSONL 是主数据，CSV 便于浏览，report.json 是完成标志。"""
    raw_root, output_dir = Path(raw_root).resolve(), Path(output_dir).resolve()
    if not raw_root.is_dir():
        raise FileNotFoundError(f"原始数据目录不存在：{raw_root}")
    if raw_root == output_dir or raw_root in output_dir.parents or output_dir in raw_root.parents:
        raise ValueError("输出目录不得与原始数据目录相同或互相包含。")
    games = list(MIYOUSHE_GAME_IDS) if games is None else list(games)
    if not games or len(set(games)) != len(games) or any(game not in MIYOUSHE_GAME_IDS for game in games):
        raise ValueError("游戏范围必须是非空、不重复的已支持游戏列表。")
    manifests = [path for game in games for path in sorted((raw_root / game).glob("*/full_collection/manifest.json"))]
    if not manifests:
        raise ValueError("未找到 full_collection/manifest.json 单帖断点。")
    output_dir.mkdir(parents=True, exist_ok=False)
    report = dict(schema_version=1, status="running", started_at=utc_now(), raw_root=str(raw_root),
                  output_dir=str(output_dir), games=games, manifests_selected=len(manifests),
                  totals=Counter(), by_game={game: Counter() for game in games},
                  collection_statuses={game: Counter() for game in games},
                  policy="仅 full_collection 已登记页；按游戏+帖子+评论ID合并；完整分页优先于预览；包含未完成帖子；不按文本删除",
                  files=["posts.jsonl", "comments.jsonl", "posts.csv", "comments.csv", "comments_flat.csv", "collection_runs.jsonl"],
                  notes=["只处理米游社；旧日期采样、发现列表及另外两个平台不参与提取。",
                         "逐帖读取断点快照；如采集仍在运行，后续新增页需再生成一次快照。",
                         "原始JSON不改写；pending_request已落盘但未登记的页只报告、不导入。",
                         "CSV仅含整理后的文字；原始text_raw保存在JSONL，允许包含NUL等控制字符。",
                         "comments_flat.csv每条顶层或楼中楼回复独占一行，内部换行写成字面量\\n。",
                         "CSV中的长ID在Excel中应按文本导入；JSONL中的ID始终是字符串。",
                         "collection_completed仅表示当前采集接口和范围结束，不代表全站完整。"])
    save_json(report, output_dir / "report.json")
    repeated_texts = Counter()
    from contextlib import ExitStack

    try:
        with ExitStack() as stack:
            streams = {name: stack.enter_context((output_dir / name).open("w", encoding="utf-8", newline=""))
                       for name in report["files"]}
            writers = {}
            for name, columns in (("posts", POST_COLUMNS), ("comments", COMMENT_COLUMNS)):
                writers[name] = csv.DictWriter(streams[name + ".csv"], fieldnames=columns, extrasaction="ignore")
                writers[name].writeheader()
            flat_writer = csv.DictWriter(streams["comments_flat.csv"], fieldnames=COMMENT_COLUMNS,
                                         extrasaction="ignore")
            flat_writer.writeheader()

            def emit(name, row):
                streams[name + ".jsonl"].write(json.dumps(row, ensure_ascii=False) + "\n")
                if name in writers:
                    writers[name].writerow(row)
                if name == "comments":
                    write_flat_comment(flat_writer, row)

            for index, path in enumerate(manifests, 1):
                post, comments, coverage = extract_run(path, raw_root)
                game = coverage["game"]
                report["collection_statuses"][game][coverage["collection_status"]] += 1
                counts = Counter(runs_processed=1, pages_read=coverage["pages_read"],
                                 page_records=coverage["page_records"], preview_records=coverage["preview_records"],
                                 pending_raw_excluded=len(coverage["pending_raw_excluded"]),
                                 missing_root_comments=coverage["missing_root_comments"])
                if post is not None:
                    emit("posts", post)
                    counts["posts"] += 1
                    counts["empty_posts"] += post["is_empty_text"]
                for comment in comments:
                    emit("comments", comment)
                    counts["comments"] += 1
                    counts["top_comments" if comment["level"] == "top" else "sub_comments"] += 1
                    counts["duplicate_id_occurrences"] += comment["occurrence_count"] - 1
                    counts["empty_comments"] += comment["is_empty_text"]
                    counts["image_comments"] += bool(comment["image_count"])
                    counts["embedded_only_comments"] += comment["embedded_only"]
                    counts["text_variant_comments"] += comment["has_text_variants"]
                    if comment["text"]:
                        repeated_texts[hashlib.sha256(comment["text"].encode("utf-8")).hexdigest()] += 1
                emit("collection_runs", coverage)
                report["totals"].update(counts)
                report["by_game"][game].update(counts)
                if index % 25 == 0 or index == len(manifests):
                    print(f"提取 {index}/{len(manifests)} 帖断点；帖子 {report['totals']['posts']}，评论 {report['totals']['comments']}", flush=True)
        report["repeated_nonempty_text_groups"] = sum(count > 1 for count in repeated_texts.values())
        report["repeated_nonempty_text_extra_comments"] = sum(count - 1 for count in repeated_texts.values())
        report["status"] = "completed"
    except (Exception, KeyboardInterrupt) as error:
        report.update(status="interrupted", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        report["finished_at"] = utc_now()
        save_json(report, output_dir / "report.json")
    return report


def snapshot_name():
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f_UTC")
