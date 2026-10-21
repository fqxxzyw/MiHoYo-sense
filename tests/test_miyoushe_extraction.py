"""提取只读、归属校验、预览合并、长 ID、空文本和部分采集覆盖。"""

import csv
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hoyo_sense.preprocessing.miyoushe import extract_dataset, export_flat_comments, normalize_text, post_text
from hoyo_sense.storage import digest, save_json


def reply(cid, text="你好_[派蒙]🥰\x00", *, children=None, images=None):
    return {"reply": {"post_id": "800", "reply_id": cid, "floor_id": 35,
                      "uid": 123, "f_reply_id": "100", "r_uid": "123", "content": text,
                      "created_at": 1786335003, "is_deleted": False},
            "stat": {"like_num": 2, "reply_num": 0}, "images": images or [],
            "sub_replies": children or []}


def build_dataset(tmp_path):
    root = tmp_path / "raw/miyoushe"
    run = root / "genshin/800/full_collection"
    post = run / "post_800.json"
    save_json({"retcode": 0, "data": {"post": {"post": {
        "post_id": 800, "game_id": 2, "uid": 456, "subject": "测试帖子", "view_type": 1,
        "content": "<p>你好<b>旅行者</b>！</p><p>下一行 &amp; 表情🥰</p>",
    }, "stat": {"reply_num": 999}}}}, post)
    top_path = run / "comments_800_hot_page1.json"
    sub_path = run / "sub_replies/floor_35/page1.json"
    long_id = 2110605758023241729
    preview = reply(long_id, "预览旧版本")
    save_json({"retcode": 0, "data": {"list": [reply("100", children=[preview, reply("103", "只有预览")]),
                                                                 reply("101", "", images=[{"url": "a"}])]}}, top_path)
    save_json({"retcode": 0, "data": {"list": [reply(long_id, "完整分页"), reply(long_id, "完整分页"),
                                                                 reply("104", "你好_[派蒙]🥰")]}}, sub_path)
    def record(path, count):
        return {"file": path.relative_to(run).as_posix(), "sha256": digest(path),
                "record_count": count, "fetched_at": "2026-10-21T00:00:00+00:00"}
    state = {"schema_version": 2, "game": "genshin", "game_id": 2, "post_id": "800",
             "status": "interrupted", "completed": False, "tasks": {
        "post": {"file": post.name, "sha256": digest(post), "completed": True},
        "top": {"pages": [record(top_path, 2)], "floor_id": None},
        "floor_35": {"pages": [record(sub_path, 3)], "floor_id": "35", "parent_reply_id": "100"},
    }}
    save_json(state, run / "manifest.json")
    return root, run, state


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_extracts_registered_data_merges_ids_and_preserves_raw(tmp_path):
    root, run, state = build_dataset(tmp_path)
    # 旧日期采样不再导入；正文中附带的子回复预览也需去重。
    save_json({"old": True}, run.parent / "old_sample/manifest.json")
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    output = tmp_path / "processed/snapshot"
    report = extract_dataset(root, output)
    assert report["status"] == "completed"
    assert report["totals"]["posts"] == 1
    assert report["totals"]["comments"] == 5
    assert report["totals"]["duplicate_id_occurrences"] == 2
    assert report["totals"]["empty_comments"] == 1
    assert report["totals"]["embedded_only_comments"] == 1
    assert report["repeated_nonempty_text_groups"] == 1
    assert all(path.read_bytes() == content for path, content in before.items())
    assert {p for p in root.rglob("*") if p.is_file()} == set(before)
    post = rows(output / "posts.jsonl")[0]
    assert post["post_id"] == "800"
    assert post["text"] == "你好旅行者！\n下一行 & 表情🥰"
    assert "<p>" in post["text_raw"] and post["reply_count"] == 999
    comments = {row["comment_id"]: row for row in rows(output / "comments.jsonl")}
    child = comments["2110605758023241729"]
    assert child["text"] == "完整分页" and child["has_text_variants"]
    assert child["root_comment_id"] == "100" and child["floor_id"] == "35"
    assert child["occurrence_count"] == 3 and not child["embedded_only"]
    assert child["sources"][-1]["pointer"] == "/data/list/0/sub_replies/0"
    assert comments["103"]["embedded_only"]
    assert comments["101"]["is_empty_text"] and comments["101"]["image_count"] == 1
    assert comments["104"]["text"] == "你好_[派蒙]🥰"
    assert comments["100"]["text_raw"].endswith("\x00")
    assert all(not row["collection_completed"] for row in comments.values())
    with (output / "comments.csv").open(encoding="utf-8", newline="") as file:
        csv_rows = list(csv.DictReader(file))
    assert len(csv_rows) == 5 and any(row["comment_id"] == "2110605758023241729" for row in csv_rows)
    assert "\x00" not in (output / "comments.csv").read_text(encoding="utf-8")
    assert rows(output / "collection_runs.jsonl")[0]["manifest_sha256"] == digest(run / "manifest.json")
    with pytest.raises(FileExistsError):
        extract_dataset(root, output)


def test_pending_saved_raw_is_reported_but_not_imported(tmp_path):
    root, run, state = build_dataset(tmp_path)
    filename = "comments_800_hot_page2.json"
    save_json({"retcode": 0, "data": {"list": [reply("999")]}}, run / filename)
    state["tasks"]["top"]["pending_request"] = {"file": filename}
    save_json(state, run / "manifest.json")
    output = tmp_path / "processed"
    report = extract_dataset(root, output)
    assert report["totals"]["pending_raw_excluded"] == 1
    assert "999" not in {row["comment_id"] for row in rows(output / "comments.jsonl")}


@pytest.mark.parametrize("bad", ["hash", "post", "floor", "float_id", "escape"])
def test_corruption_stops_export_and_marks_report_interrupted(tmp_path, bad):
    root, run, state = build_dataset(tmp_path)
    record = state["tasks"]["top"]["pages"][0]
    path = run / record["file"]
    data = json.loads(path.read_text(encoding="utf-8"))
    if bad == "escape":
        record["file"] = "../../outside.json"
    else:
        if bad == "hash":
            data["data"]["list"][0]["reply"]["content"] = "被改写"
        elif bad == "post":
            data["data"]["list"][0]["reply"]["post_id"] = "801"
        elif bad == "floor":
            sub_record = state["tasks"]["floor_35"]["pages"][0]
            path = run / sub_record["file"]
            data = json.loads(path.read_text(encoding="utf-8"))
            data["data"]["list"][0]["reply"]["floor_id"] = 36
            record = sub_record
        else:
            data["data"]["list"][0]["reply"]["reply_id"] = 2110605758023241729.0
        save_json(data, path)
        if bad != "hash":
            record["sha256"] = digest(path)
    save_json(state, run / "manifest.json")
    output = tmp_path / "processed"
    with pytest.raises(ValueError):
        extract_dataset(root, output)
    assert json.loads((output / "report.json").read_text(encoding="utf-8"))["status"] == "interrupted"


def test_unavailable_run_stays_in_coverage_without_a_post(tmp_path):
    root, run, state = build_dataset(tmp_path)
    (run / state["tasks"]["post"]["file"]).unlink()
    state["status"] = "skipped_unavailable"
    state["tasks"]["post"]["completed"] = False
    save_json(state, run / "manifest.json")
    output = tmp_path / "processed"
    report = extract_dataset(root, output)
    assert report["totals"]["posts"] == 0
    assert report["collection_statuses"]["genshin"]["skipped_unavailable"] == 1
    assert len(rows(output / "collection_runs.jsonl")) == 1


def test_does_not_write_inside_or_above_raw(tmp_path):
    root, _, _ = build_dataset(tmp_path)
    for output in (root, root / "processed", root.parent):
        with pytest.raises(ValueError, match="原始数据目录"):
            extract_dataset(root, output)


def test_text_cleanup_keeps_sentiment_signals_and_rich_text_fallback():
    assert normalize_text("  不好！🥰\r\n\u200b  角色A <3  https://a.b  ") == "不好！🥰\n角色A <3 https://a.b"
    raw, text, source = post_text({"content": "", "structured_content":
                                  '[{"insert":"剧情！\\n"},{"insert":{"image":"url"}}]'})
    assert (raw, text, source) == ("", "剧情！", "structured_content")


def test_flat_csv_keeps_every_reply_on_one_physical_line(tmp_path):
    root, run, state = build_dataset(tmp_path)
    record = state["tasks"]["floor_35"]["pages"][0]
    path = run / record["file"]
    data = json.loads(path.read_text(encoding="utf-8"))
    data["data"]["list"][0]["reply"]["content"] = '第一行\n第二行，"引用"\\n字面量'
    save_json(data, path)
    record["sha256"] = digest(path)
    save_json(state, run / "manifest.json")
    output = tmp_path / "processed"
    extract_dataset(root, output)
    flat_path = output / "comments_flat.csv"
    assert len(flat_path.read_bytes().splitlines()) == 6  # 五条评论加表头。
    before = flat_path.read_bytes()
    result = export_flat_comments(output)
    assert flat_path.read_bytes() == before
    assert result["records"] == 5 and result["physical_lines"] == 6
    assert result["top"] == 2 and result["sub"] == 3
    with flat_path.open(encoding="utf-8", newline="") as file:
        flat_rows = {row["comment_id"]: row for row in csv.DictReader(file)}
    child = flat_rows["2110605758023241729"]
    assert child["root_comment_id"] == "100" and child["level"] == "sub"
    assert child["text"] == '第一行\\n第二行，"引用"\\\\n字面量'
    assert rows(output / "comments.jsonl")[2]["text"].startswith("第一行\n第二行")


def test_flat_export_refuses_interrupted_snapshot(tmp_path):
    save_json({"status": "interrupted"}, tmp_path / "report.json")
    with pytest.raises(ValueError, match="成功快照"):
        export_flat_comments(tmp_path)
