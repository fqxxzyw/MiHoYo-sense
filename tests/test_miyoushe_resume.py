"""使用临时目录与模拟接口验证恢复，绝不请求真实平台。"""

import importlib.util
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def collector(monkeypatch) -> Any:
    path = Path(__file__).resolve().parents[1] / "scripts/01_collect_miyoushe.py"
    spec = importlib.util.spec_from_file_location("miyoushe_collector", path)
    assert spec is not None and spec.loader is not None
    module: Any = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def forbidden_network(*args, **kwargs):
        raise AssertionError("离线测试禁止真实网络请求")

    monkeypatch.setitem(vars(module.requests), "get", forbidden_network)
    return module


def reply(reply_id, floor=35, children=0):
    return {
        "reply": {"reply_id": str(reply_id), "post_id": "78918177",
                  "floor_id": floor, "content": ""},
        "stat": {}, "sub_reply_count": children, "sub_replies": [],
    }


def page(items, last_id, is_last):
    return {"retcode": 0, "data": {"list": items, "last_id": last_id, "is_last": is_last}}


def fake_api(collector, monkeypatch, top, sub=None):
    calls = []

    def request(url, params):
        marker = params.get("last_id")
        calls.append((url, marker))
        if url == collector.POST_URL:
            return {"retcode": 0, "data": {"post": {"post": {
                "post_id": "78918177", "subject": "离线测试", "content": "<p>原文</p>",
            }}}}
        if url == collector.COMMENTS_URL:
            return top[marker]
        return sub[marker]

    monkeypatch.setitem(vars(collector), "request_json", request)
    return calls


def test_limited_then_resume_no_refetch_and_completed_rerun(collector, monkeypatch, tmp_path):
    c = collector
    calls = fake_api(c, monkeypatch, {
        None: page([reply("a", children=2)], "20", False),
        "20": page([reply("b", floor=36)], "40", True),
    }, {
        None: page([reply("s1")], "2110599200696823809", False),
        "2110599200696823809": page([reply("s2")], "2110605758023241729", True),
    })
    state = c.run_collection(tmp_path, max_pages=1, max_sub_pages=1)
    assert state["status"] == "limited" and not state["completed"]
    old_hashes = {p: c.digest(tmp_path / p) for p in [
        "post_78918177.json", "comments_78918177_hot_page1.json",
        "sub_replies/floor_35/page1.json",
    ]}
    calls.clear()
    state = c.run_collection(tmp_path)
    assert calls == [(c.COMMENTS_URL, "20"), (c.SUB_REPLIES_URL, "2110599200696823809")]
    assert state["status"] == "completed" and state["completed"]
    assert state["tasks"]["floor_35"]["parent_reply_id"] == "a"
    assert all(c.digest(tmp_path / p) == value for p, value in old_hashes.items())
    calls.clear()
    c.run_collection(tmp_path)
    assert calls == []


def test_raw_saved_before_checkpoint_interrupt_recovers_without_refetch(collector, monkeypatch, tmp_path):
    c = collector
    calls = fake_api(c, monkeypatch, {
        None: page([reply("a")], "20", False),
        "20": page([reply("b")], "40", True),
    })
    original = c.save_json
    crashed = False

    def crash_after_raw(data, path):
        nonlocal crashed
        original(data, path)
        if Path(path).name == "comments_78918177_hot_page1.json" and not crashed:
            crashed = True
            raise KeyboardInterrupt("模拟 raw 已落盘而断点未更新")

    monkeypatch.setitem(vars(c), "save_json", crash_after_raw)
    with pytest.raises(KeyboardInterrupt):
        c.run_collection(tmp_path)
    interrupted = c.read_json(tmp_path / "manifest.json")
    assert interrupted["status"] == "interrupted"
    assert interrupted["tasks"]["top"]["pages"] == []
    assert interrupted["tasks"]["top"]["pending_request"]["last_id"] is None
    calls.clear()
    state = c.run_collection(tmp_path)
    assert calls == [(c.COMMENTS_URL, "20")]
    assert state["tasks"]["top"]["unique_comments"] == 2
    assert state["completed"]


def test_http_failure_resumes_from_failed_request_only(collector, monkeypatch, tmp_path):
    c = collector
    calls = fake_api(c, monkeypatch, {
        None: page([reply("a")], "20", False),
        "20": page([reply("b")], "40", True),
    })
    original = c.request_json

    def fail_second(url, params):
        if url == c.COMMENTS_URL and params.get("last_id") == "20":
            raise c.requests.Timeout("模拟超时")
        return original(url, params)

    monkeypatch.setitem(vars(c), "request_json", fail_second)
    with pytest.raises(c.requests.Timeout):
        c.run_collection(tmp_path)
    state = c.read_json(tmp_path / "manifest.json")
    assert state["tasks"]["top"]["next_last_id"] == "20"
    assert len(state["tasks"]["top"]["pages"]) == 1
    monkeypatch.setitem(vars(c), "request_json", original)
    calls.clear()
    assert c.run_collection(tmp_path)["completed"]
    assert calls == [(c.COMMENTS_URL, "20")]


@pytest.mark.parametrize("bad_page", [
    page([reply("b")], "20", False),
    page([reply("b")], None, False),
    page([], "40", False),
    page([reply("b")], 2110599200696823809.0, False),
    page([reply("b")], "40", "false"),
    page([dict(reply("b"), reply={"reply_id": "b", "post_id": "other"})], "40", True),
])
def test_bad_pagination_stops_and_does_not_retry_saved_bad_page(
        collector, monkeypatch, tmp_path, bad_page):
    c = collector
    calls = fake_api(c, monkeypatch, {
        None: page([reply("a")], "20", False), "20": bad_page,
    })
    with pytest.raises((ValueError, KeyError)):
        c.run_collection(tmp_path)
    assert c.read_json(tmp_path / "manifest.json")["status"] == "interrupted"
    assert (tmp_path / "comments_78918177_hot_page2.json").exists()
    calls.clear()
    with pytest.raises((ValueError, KeyError)):
        c.run_collection(tmp_path)
    assert calls == []


def test_duplicate_records_preserved_but_counted(collector, monkeypatch, tmp_path):
    c = collector
    fake_api(c, monkeypatch, {
        None: page([reply("a")], "20", False),
        "20": page([reply("a"), reply("b")], "40", True),
    })
    task = c.run_collection(tmp_path)["tasks"]["top"]
    assert (task["total_records"], task["unique_comments"], task["duplicate_records"]) == (3, 2, 1)
    assert len(c.read_json(tmp_path / task["pages"][1]["file"])["data"]["list"]) == 2


def test_missing_or_tampered_file_does_not_refetch(collector, monkeypatch, tmp_path):
    c = collector
    calls = fake_api(c, monkeypatch, {None: page([reply("a")], "20", True)})
    c.run_collection(tmp_path)
    path = tmp_path / "comments_78918177_hot_page1.json"
    c.save_json(page([reply("tampered")], "20", True), path)
    calls.clear()
    with pytest.raises(ValueError, match="修改"):
        c.run_collection(tmp_path)
    assert calls == []
    path.unlink()
    with pytest.raises(FileNotFoundError):
        c.run_collection(tmp_path)
    assert calls == []


def test_floor_limit_and_same_limits_make_no_additional_requests(collector, monkeypatch, tmp_path):
    c = collector
    calls = fake_api(c, monkeypatch, {
        None: page([reply("a", children=1), reply("b", floor=36, children=1)], "20", True),
    }, {None: page([reply("s1")], "20", True)})
    state = c.run_collection(tmp_path, max_floors=1)
    assert state["status"] == "limited"
    assert state["tasks"]["floor_36"]["status"] == "limited"
    assert state["tasks"]["floor_35"]["completed"]
    calls.clear()
    assert c.run_collection(tmp_path, max_floors=1)["status"] == "limited"
    assert calls == []


def test_import_real_two_page_sample_offline(collector, monkeypatch, tmp_path):
    c = collector
    seed = c.RAW_DIR / c.POST_ID / "20261018_062634_980587_UTC"
    if not seed.exists():
        pytest.skip("本地原始采样未随仓库发布")
    state = c.run_collection(tmp_path, max_pages=5, max_sub_pages=2, max_floors=1, seed_run=seed)
    sub = state["tasks"]["floor_35"]
    assert sub["pages_saved"] == 2 and sub["unique_comments"] == 40
    assert sub["duplicate_records"] == 0 and not sub["completed"]
    assert sub["pages"][1]["request_last_id"] == sub["pages"][0]["response_last_id"]
    assert sub["next_last_id"] == "2110605758023241729"
    assert state["tasks"]["top"]["unique_comments"] == 99
    assert len([key for key in state["tasks"] if key.startswith("floor_")]) == 59
