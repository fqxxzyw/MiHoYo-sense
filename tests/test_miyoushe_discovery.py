"""帖子发现只请求第一页，候选清单不触发既有采集任务。"""

import importlib.util
from pathlib import Path
import sys
from typing import Any

import pytest


@pytest.fixture
def collector():
    path = Path(__file__).resolve().parents[1] / "scripts/01_collect_miyoushe.py"
    spec = importlib.util.spec_from_file_location("discovery_collector", path)
    assert spec is not None and spec.loader is not None
    module: Any = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def response():
    return {"retcode": 0, "data": {
        "recommended_posts": [
            {"post": {"post_id": 123, "subject": "首次标题", "game_id": 2}},
            {"post": {"post_id": "123", "subject": "另一标题", "game_id": 2}},
            {"post": {"post_id": "456", "subject": "跨游戏记录仍保留", "game_id": 6}},
        ],
        "other_posts": [{"post": {"post_id": "999", "subject": "不取其他栏目", "game_id": 2}}],
    }}


def test_discovery_cli_one_request_dedup_provenance_and_state_untouched(collector, monkeypatch, tmp_path):
    c = collector
    monkeypatch.setattr(c, "RAW_DIR", tmp_path)
    old_state = tmp_path / c.POST_ID / "full_collection" / "manifest.json"
    c.save_json({"status": "completed", "tasks": {"top": {"completed": True}}}, old_state)
    old_bytes = old_state.read_bytes()
    calls = []

    def request(url, params):
        calls.append((url, dict(params)))
        assert url == c.WEB_HOME_URL  # 任意评论或帖子详情请求都会让测试失败。
        return response()

    def no_collection(*_args, **_kwargs):
        raise AssertionError("发现模式不允许进入单帖采集")

    monkeypatch.setitem(vars(c), "request_json", request)
    monkeypatch.setitem(vars(c), "run_collection", no_collection)
    monkeypatch.setattr(sys, "argv", ["collector", "--discover-posts"])
    c.main()
    runs = list((tmp_path / "_discovery").iterdir())
    assert len(runs) == 1 and calls == [(c.WEB_HOME_URL, {"gids": 2, "page": 1, "page_size": 20})]
    raw = c.read_json(runs[0] / "webHome_page1.json")
    assert raw == response()
    task_list = c.read_json(runs[0] / "discovered_posts.json")
    assert (task_list["record_count"], task_list["unique_post_count"], task_list["duplicate_records"]) == (3, 2, 1)
    assert [task["post_id"] for task in task_list["tasks"]] == ["123", "456"]
    first = task_list["tasks"][0]
    assert first["subject"] == "首次标题" and first["source_indices"] == [1, 2]
    assert first["source"] == "webHome/recommended_posts" and first["discovered_at"]
    assert first["request_params"] == calls[0][1]
    assert task_list["tasks"][1]["game_id"] == 6
    manifest = c.read_json(runs[0] / "discovery_manifest.json")
    assert manifest["status"] == "discovered" and manifest["pagination_verified"] is False
    assert manifest["collection_started"] is False
    assert old_state.read_bytes() == old_bytes
    # 再次发现会创建新目录，原响应和原任务清单不被覆盖。
    saved = {p: p.read_bytes() for p in runs[0].iterdir()}
    c.main()
    assert len(list((tmp_path / "_discovery").iterdir())) == 2
    assert all(p.read_bytes() == content for p, content in saved.items())
    assert old_state.read_bytes() == old_bytes


def test_schema_failure_preserves_raw_and_records_error(collector, monkeypatch, tmp_path):
    c = collector
    bad = {"retcode": 0, "data": {"recommended_posts": [{"post": {"post_id": "123"}}]}}
    monkeypatch.setitem(vars(c), "request_json", lambda *_args: bad)
    with pytest.raises(KeyError):
        c.discover_posts(tmp_path)
    run = next(tmp_path.iterdir())
    assert c.read_json(run / "webHome_page1.json") == bad
    assert c.read_json(run / "discovery_manifest.json")["status"] == "interrupted"
    assert not (run / "discovered_posts.json").exists()


def test_empty_recommendations_is_valid_not_full_traversal(collector, monkeypatch, tmp_path):
    c = collector
    monkeypatch.setitem(vars(c), "request_json", lambda *_args: {
        "retcode": 0, "data": {"recommended_posts": []},
    })
    run, tasks = c.discover_posts(tmp_path)
    assert tasks == []
    manifest = c.read_json(run / "discovery_manifest.json")
    assert manifest["status"] == "discovered" and manifest["pagination_verified"] is False
