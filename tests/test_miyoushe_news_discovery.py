"""资讯列表发现与恢复。测试只操作临时目录，不接触真实评论采集。"""

import importlib.util
from pathlib import Path
import sys
from typing import Any

import pytest


@pytest.fixture
def collector(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "scripts/01_collect_miyoushe.py"
    spec = importlib.util.spec_from_file_location("news_collector", path)
    assert spec is not None and spec.loader is not None
    module: Any = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setitem(vars(module.requests), "get",
                        lambda *_args, **_kwargs: pytest.fail("测试禁止真实网络请求"))
    return module


def news(ids, last_id="20", is_last=False):
    return {"retcode": 0, "data": {
        "list": [{"post": {
            "post_id": str(post_id), "subject": "资讯 " + str(post_id),
            "game_id": 0, "content": "", "uid": "0",
        }, "user": None, "stat": None} for post_id in ids],
        "last_id": last_id, "is_last": is_last,
    }}


def api(c, monkeypatch, pages):
    calls = []

    def request(url, params):
        assert url == c.MIYOUSHE_ENDPOINTS["news_list"]
        assert set(params) == {"client_type", "gids", "last_id", "page_size", "type"}
        assert (params["client_type"], params["gids"], params["page_size"], params["type"]) == (4, 2, 20, 3)
        calls.append(dict(params))
        return pages[params["last_id"]]

    monkeypatch.setitem(vars(c), "request_json", request)
    return calls


def run(c, root, limit=2):
    return c.discover_news_pages("genshin", root, limit, c.request_json)


def test_cursor_dedup_raw_zero_game_id_and_cumulative_resume(collector, monkeypatch, tmp_path, capsys):
    c = collector
    calls = api(c, monkeypatch, {
        "": news(["1", "1", "2"]), "20": news(["2", "3"], "40", True),
    })
    state, tasks = run(c, tmp_path, 1)
    assert state["status"] == "limited" and not state["completed"]
    assert calls[0]["last_id"] == ""
    first_file = tmp_path / state["pages"][0]["file"]
    before = first_file.read_bytes()
    assert c.read_json(first_file) == news(["1", "1", "2"])
    assert tasks[0]["game_id"] == 0 and tasks[0]["game"] == "genshin"
    assert tasks[0]["requires_detail_validation"]
    assert tasks[0]["source"] == "getNewsList/list" and tasks[0]["source_indices"] == [1, 2]
    assert tasks[0]["request_params"] == calls[0]
    first_time = tasks[0]["discovered_at"]
    calls.clear()
    run(c, tmp_path, 1)
    assert calls == []
    state, tasks = run(c, tmp_path)
    assert [p["last_id"] for p in calls] == ["20"]
    assert state["completed"] and state["status"] == "completed"
    assert (state["total_records"], state["unique_post_count"], state["duplicate_records"]) == (5, 3, 2)
    assert [t["post_id"] for t in tasks] == ["1", "2", "3"]
    assert tasks[0]["discovered_at"] == first_time and first_file.read_bytes() == before
    calls.clear()
    capsys.readouterr()
    run(c, tmp_path, None)
    assert calls == []
    output = capsys.readouterr().out
    assert "第 1 页候选预览" in output and "第 2 页候选预览" in output
    assert "1  game_id=0  资讯 1" in output


def test_crash_after_raw_saved_recovers_without_refetch(collector, monkeypatch, tmp_path):
    c = collector
    calls = api(c, monkeypatch, {"": news(["1"]), "20": news(["2"], "40", True)})
    import hoyo_sense.miyoushe_news_discovery as module
    original = module.save_json
    crashed = False

    def save_then_crash(data, path):
        nonlocal crashed
        original(data, path)
        if Path(path).name == "page_000001.json" and not crashed:
            crashed = True
            raise KeyboardInterrupt("模拟 raw 已写入，断点未登记")

    monkeypatch.setitem(vars(module), "save_json", save_then_crash)
    with pytest.raises(KeyboardInterrupt):
        run(c, tmp_path)
    manifest = tmp_path / "_discovery/news_type_3/discovery_manifest.json"
    assert c.read_json(manifest)["status"] == "interrupted"
    calls.clear()
    state, tasks = run(c, tmp_path)
    assert state["completed"] and len(tasks) == 2
    assert [p["last_id"] for p in calls] == ["20"]


def test_http_failure_resumes_only_failed_page(collector, monkeypatch, tmp_path):
    c = collector
    api(c, monkeypatch, {"": news(["1"])})
    run(c, tmp_path, 1)

    def timeout(_url, _params):
        raise c.requests.Timeout("模拟资讯第 2 页超时")

    monkeypatch.setitem(vars(c), "request_json", timeout)
    with pytest.raises(c.requests.Timeout):
        run(c, tmp_path)
    manifest = tmp_path / "_discovery/news_type_3/discovery_manifest.json"
    state = c.read_json(manifest)
    assert len(state["pages"]) == 1 and state["next_last_id"] == "20"
    calls = api(c, monkeypatch, {"20": news(["2"], "40", True)})
    assert run(c, tmp_path)[0]["completed"]
    assert [p["last_id"] for p in calls] == ["20"]


@pytest.mark.parametrize("bad_page", [
    news(["2"], "20", False),
    news(["2"], None, False),
    news([], "40", False),
    news(["2"], 2110599200696823809.0, False),
    news(["2"], "40", "false"),
    {"retcode": 0, "data": {"list": [{"post": {"post_id": "2"}}], "last_id": "40", "is_last": False}},
    news(["1"], "40", False),
])
def test_bad_saved_page_stops_without_refetch(collector, monkeypatch, tmp_path, bad_page):
    c = collector
    calls = api(c, monkeypatch, {"": news(["1"]), "20": bad_page})
    with pytest.raises((ValueError, KeyError)):
        run(c, tmp_path, None)
    root = tmp_path / "_discovery/news_type_3"
    assert c.read_json(root / "page_000002.json") == bad_page
    assert c.read_json(root / "discovery_manifest.json")["status"] == "interrupted"
    assert c.read_json(root / "discovery_manifest.json")["completed"] is False
    calls.clear()
    with pytest.raises((ValueError, KeyError)):
        run(c, tmp_path, None)
    assert calls == []


def test_tampered_or_missing_raw_not_refetched(collector, monkeypatch, tmp_path):
    c = collector
    calls = api(c, monkeypatch, {"": news(["1"], "20", True)})
    state, _ = run(c, tmp_path)
    path = tmp_path / state["pages"][0]["file"]
    c.save_json(news(["999"], "20", True), path)
    calls.clear()
    with pytest.raises(ValueError, match="修改"):
        run(c, tmp_path)
    assert calls == []
    path.unlink()
    with pytest.raises(FileNotFoundError):
        run(c, tmp_path)
    assert calls == []


def test_empty_last_page_completes(collector, monkeypatch, tmp_path):
    c = collector
    api(c, monkeypatch, {"": news([], None, True)})
    state, tasks = run(c, tmp_path)
    assert state["completed"] and tasks == []


def test_cli_news_default_two_pages_and_old_states_unchanged(collector, monkeypatch, tmp_path):
    c = collector
    monkeypatch.setitem(vars(c), "RAW_DIR", tmp_path / "genshin")
    originals = {}
    for relative in [
        "genshin/78918177/full_collection/manifest.json",
        "genshin/_discovery/home_pages/discovery_manifest.json",
        "_batches/genshin/batch_manifest.json",
    ]:
        path = tmp_path / relative
        c.save_json({"existing_state": relative}, path)
        originals[path] = path.read_bytes()
    calls = api(c, monkeypatch, {"": news(["1"]), "20": news(["2"], "40", False)})

    def forbidden(*_args, **_kwargs):
        pytest.fail("资讯发现模式不应调用评论或批次采集")

    monkeypatch.setitem(vars(c), "run_collection", forbidden)
    monkeypatch.setitem(vars(c), "run_batch", forbidden)
    monkeypatch.setattr(sys, "argv", ["collector", "--discover-news", "--game", "genshin"])
    c.main()
    assert [p["last_id"] for p in calls] == ["", "20"]
    state = c.read_json(tmp_path / "genshin/_discovery/news_type_3/discovery_manifest.json")
    assert state["status"] == "limited" and not state["collection_started"]
    assert all(path.read_bytes() == value for path, value in originals.items())
    calls.clear()
    c.main()
    assert calls == [] and all(path.read_bytes() == value for path, value in originals.items())


def test_news_cli_rejects_unverified_game_and_conflicting_modes(collector, monkeypatch, tmp_path):
    c = collector
    monkeypatch.setattr(sys, "argv", ["collector", "--discover-news", "--game", "starrail"])
    with pytest.raises(SystemExit):
        c.main()
    monkeypatch.setattr(sys, "argv", ["collector", "--discover-news", "--batch"])
    with pytest.raises(SystemExit):
        c.main()
    with pytest.raises(ValueError, match="仅验证原神"):
        c.discover_news_pages("zzz", tmp_path, 2, c.request_json)
    assert not list(tmp_path.iterdir())
