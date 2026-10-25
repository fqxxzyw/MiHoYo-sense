"""分页发现、旧批次追加与崩溃恢复；所有接口均离线模拟。"""

import importlib.util
from pathlib import Path
import sys

import pytest


@pytest.fixture
def collector(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "scripts/01_collect_miyoushe.py"
    spec = importlib.util.spec_from_file_location("paged_collector", path)
    c = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(c)
    monkeypatch.setattr(c.requests, "get", lambda *a, **k: pytest.fail("禁止真实网络"))
    return c


def home(ids, gid=8):
    return {"retcode": 0, "data": {"recommended_posts": [
        {"post": {"post_id": pid, "game_id": gid, "subject": "帖子 " + pid}} for pid in ids
    ]}}


def install_api(c, monkeypatch, pages):
    calls = []

    def request(url, params):
        calls.append((url, dict(params)))
        if url == c.WEB_HOME_URL:
            assert params["gids"] == 8 and params["page_size"] == 20
            return pages[params["page"]]
        if url == c.POST_URL:
            return {"retcode": 0, "data": {"post": {"post": {
                "post_id": params["post_id"], "game_id": 8, "subject": "正文", "content": "<p>原文</p>",
            }}}}
        assert url == c.COMMENTS_URL
        return {"retcode": 0, "data": {"list": [], "is_last": True, "last_id": "0"}}

    monkeypatch.setattr(c, "request_json", request)
    return calls, request


def seed(c, root):
    return c.discover_posts(root / "zzz/_discovery", game="zzz")[0]


def test_old_completed_batch_appends_new_posts_and_cumulative_resume(collector, monkeypatch, tmp_path):
    c = collector
    pages = {1: home(["800", "801"]), 2: home(["801", "802", "802"]), 3: home([])}
    calls, _ = install_api(c, monkeypatch, pages)
    root, batch_dir = tmp_path / "raw", tmp_path / "batch"
    old = c.run_batch(batch_dir, ["zzz"], raw_root=root, max_posts=None)
    assert old["completed"]
    group = old["batches"]["zzz"]
    source = root / group["discovery_dir"]
    before = {p: p.read_bytes() for p in source.iterdir()}
    old_raw = {p: c.digest(p) for p in (root / "zzz").glob("*/full_collection/*.json")
               if p.name != "manifest.json"}
    calls.clear()
    new = c.run_batch(batch_dir, ["zzz"], raw_root=root, max_posts=None, max_discovery_pages=2)
    tasks = new["batches"]["zzz"]["tasks"]
    assert [t["post_id"] for t in tasks] == ["800", "801", "802"]
    assert calls == [
        (c.WEB_HOME_URL, {"gids": 8, "page": 2, "page_size": 20}),
        (c.POST_URL, {"gids": 8, "post_id": "802", "read": 1}),
        (c.COMMENTS_URL, {"gids": 8, "post_id": "802", "size": 20, "is_hot": "true"}),
    ]
    assert new["status"] == "limited" and new["collection_completed"] and not new["discovery_finished"]
    assert all(p.read_bytes() == data for p, data in before.items())
    assert all(c.digest(p) == sha for p, sha in old_raw.items())
    raw_page2 = root / "zzz/_discovery/home_pages/page_000002.json"
    assert c.read_json(raw_page2) == pages[2]  # 重复记录原样保留。
    calls.clear()
    c.run_batch(batch_dir, ["zzz"], raw_root=root, max_posts=None, max_discovery_pages=2)
    assert calls == []
    end = c.run_batch(batch_dir, ["zzz"], raw_root=root, max_posts=None, max_discovery_pages=3)
    assert end["completed"] and end["discovery_finished"]
    assert calls == [(c.WEB_HOME_URL, {"gids": 8, "page": 3, "page_size": 20})]
    calls.clear()
    c.run_batch(batch_dir, ["zzz"], raw_root=root, max_posts=None, max_discovery_pages=None)
    assert calls == []


def test_discovery_failure_keeps_old_post_states_then_resumes(collector, monkeypatch, tmp_path):
    c = collector
    calls, normal = install_api(c, monkeypatch, {1: home(["800"]), 2: home(["801"]), 3: home([])})
    root, batch_dir = tmp_path / "raw", tmp_path / "batch"
    c.run_batch(batch_dir, ["zzz"], raw_root=root, max_posts=None)
    post_path = root / "zzz/800/full_collection/manifest.json"
    before = post_path.read_bytes()

    def fail(url, params):
        if url == c.WEB_HOME_URL and params["page"] == 2:
            raise c.requests.Timeout("模拟发现第2页超时")
        return normal(url, params)

    monkeypatch.setattr(c, "request_json", fail)
    with pytest.raises(c.requests.Timeout):
        c.run_batch(batch_dir, ["zzz"], raw_root=root, max_posts=None, max_discovery_pages=3)
    state = c.read_json(batch_dir / "batch_manifest.json")
    assert state["status"] == "interrupted" and state["batches"]["zzz"]["tasks"][0]["completed"]
    assert post_path.read_bytes() == before
    monkeypatch.setattr(c, "request_json", normal)
    calls.clear()
    state = c.run_batch(batch_dir, ["zzz"], raw_root=root, max_posts=None, max_discovery_pages=3)
    assert state["completed"]
    assert [params["page"] for url, params in calls if url == c.WEB_HOME_URL] == [2, 3]
    assert all(params.get("post_id") != "800" for _, params in calls)


def test_raw_saved_before_discovery_checkpoint_is_recovered_without_refetch(collector, monkeypatch, tmp_path):
    c = collector
    calls, _ = install_api(c, monkeypatch, {1: home(["800"]), 2: home(["801"]), 3: home([])})
    source = seed(c, tmp_path)
    import hoyo_sense.collectors.miyoushe.discovery as discovery_module
    original = discovery_module.save_json
    crashed = False

    def save_then_crash(data, path):
        nonlocal crashed
        original(data, path)
        if Path(path).name == "page_000002.json" and not crashed:
            crashed = True
            raise KeyboardInterrupt("模拟发现 raw 已落盘")

    monkeypatch.setattr(discovery_module, "save_json", save_then_crash)
    with pytest.raises(KeyboardInterrupt):
        c.discover_home_pages("zzz", tmp_path / "zzz", 3, c.request_json, c.build_discovery_tasks, source)
    raw = tmp_path / "zzz/_discovery/home_pages/page_000002.json"
    before = raw.read_bytes()
    calls.clear()
    state, tasks = c.discover_home_pages("zzz", tmp_path / "zzz", 3, c.request_json, c.build_discovery_tasks, source)
    assert raw.read_bytes() == before and state["status"] == "exhausted"
    assert [t["post_id"] for t in tasks] == ["800", "801"]
    assert calls == [(c.WEB_HOME_URL, {"gids": 8, "page": 3, "page_size": 20})]


def test_repeated_page_stops_without_deleting_raw_or_looping(collector, monkeypatch, tmp_path):
    c = collector
    calls, _ = install_api(c, monkeypatch, {1: home(["800", "801"]), 2: home(["801", "800", "800"])})
    source = seed(c, tmp_path)
    state, tasks = c.discover_home_pages("zzz", tmp_path / "zzz", None, c.request_json, c.build_discovery_tasks, source)
    assert state["status"] == "stalled" and not state["exhausted"]
    assert len(tasks) == 2
    assert len(c.read_json(tmp_path / "zzz/_discovery/home_pages/page_000002.json")["data"]["recommended_posts"]) == 3
    calls.clear()
    c.discover_home_pages("zzz", tmp_path / "zzz", None, c.request_json, c.build_discovery_tasks, source)
    assert calls == []


def test_bad_saved_discovery_page_is_not_requested_again(collector, monkeypatch, tmp_path):
    c = collector
    bad = {"retcode": 0, "data": {"recommended_posts": [{"post": {"post_id": "801"}}]}}
    calls, _ = install_api(c, monkeypatch, {1: home(["800"]), 2: bad})
    source = seed(c, tmp_path)
    with pytest.raises(KeyError):
        c.discover_home_pages("zzz", tmp_path / "zzz", 2, c.request_json, c.build_discovery_tasks, source)
    assert c.read_json(tmp_path / "zzz/_discovery/home_pages/page_000002.json") == bad
    calls.clear()
    with pytest.raises(KeyError):
        c.discover_home_pages("zzz", tmp_path / "zzz", 2, c.request_json, c.build_discovery_tasks, source)
    assert calls == []


def test_tampered_discovery_stops_without_any_requests(collector, monkeypatch, tmp_path):
    c = collector
    calls, _ = install_api(c, monkeypatch, {1: home(["800"]), 2: home(["801"])})
    source = seed(c, tmp_path)
    c.discover_home_pages("zzz", tmp_path / "zzz", 2, c.request_json, c.build_discovery_tasks, source)
    path = tmp_path / "zzz/_discovery/home_pages/page_000002.json"
    c.save_json(home(["999"]), path)
    calls.clear()
    with pytest.raises(ValueError, match="修改"):
        c.discover_home_pages("zzz", tmp_path / "zzz", 2, c.request_json, c.build_discovery_tasks, source)
    assert calls == []


def test_new_posts_deferred_when_collection_limit_unchanged(collector, monkeypatch, tmp_path):
    c = collector
    calls, _ = install_api(c, monkeypatch, {1: home(["800"]), 2: home(["801"]), 3: home([])})
    batch = c.run_batch(tmp_path / "batch", ["zzz"], raw_root=tmp_path / "raw",
                        max_posts=1, max_discovery_pages=3)
    assert [t["status"] for t in batch["batches"]["zzz"]["tasks"]] == ["completed", "deferred"]
    assert batch["discovery_finished"] and not batch["collection_completed"]
    calls.clear()
    end = c.run_batch(tmp_path / "batch", ["zzz"], raw_root=tmp_path / "raw",
                      max_posts=None, max_discovery_pages=3)
    assert end["completed"] and all(url != c.WEB_HOME_URL for url, _ in calls)


def test_cli_discover_more_only_discovers_and_keeps_original_raw(collector, monkeypatch, tmp_path):
    c = collector
    calls, _ = install_api(c, monkeypatch, {1: home(["800"]), 2: home(["801"]), 3: home([])})
    monkeypatch.setattr(c, "RAW_DIR", tmp_path / "genshin")
    monkeypatch.setattr(sys, "argv", ["collector", "--discover-more", "--game", "zzz", "--max-discovery-pages", "3"])
    c.main()
    assert all(url == c.WEB_HOME_URL for url, _ in calls)
    assert [params["page"] for _, params in calls] == [1, 2, 3]
    assert not (tmp_path / "zzz/800/full_collection").exists()
    calls.clear()
    c.main()
    assert calls == []


def test_cross_game_discovery_records_saved_but_excluded_from_collection(collector, monkeypatch, tmp_path):
    c = collector
    calls, _ = install_api(c, monkeypatch, {1: home(["800"]), 2: home(["999"], 6), 3: home([])})
    state = c.run_batch(tmp_path / "batch", ["zzz"], raw_root=tmp_path / "raw",
                        max_posts=None, max_discovery_pages=3)
    assert [t["post_id"] for t in state["batches"]["zzz"]["tasks"]] == ["800"]
    assert state["batches"]["zzz"]["excluded"][0]["post_id"] == "999"
    assert not (tmp_path / "raw/zzz/999").exists()
    assert all(params.get("post_id") != "999" for _, params in calls)


@pytest.mark.parametrize("value", [0, -1, True, "10", 1.5])
def test_bad_discovery_config(collector, tmp_path, value):
    path = tmp_path / "config.json"
    collector.save_json({"discovery": {"max_pages": value}}, path)
    with pytest.raises(ValueError):
        collector.load_config(path)


def test_no_arguments_defaults_to_three_game_batch(collector, monkeypatch):
    calls = []
    monkeypatch.setattr(sys, "argv", ["collector"])
    monkeypatch.setattr(collector, "run_news_batch", lambda path, *args, **kwargs:
                        calls.append((path, ["genshin"], kwargs)))
    monkeypatch.setattr(collector, "run_batch", lambda path, games, **kwargs:
                        calls.append((path, games, kwargs)))
    collector.main()
    assert [call[1] for call in calls] == [["genshin"], ["starrail"], ["zzz"]]
    assert [call[0].name for call in calls] == ["genshin", "starrail", "zzz"]
    assert all(call[2]["discovery_batch_pages"] == 20 for call in calls)
    assert all(call[2]["max_discovery_pages"] is None for call in calls)


def test_home_collects_after_twenty_pages_before_requesting_page_twenty_one(collector, monkeypatch, tmp_path):
    c = collector
    pages = {n: home([str(1000 + n)]) for n in range(1, 22)}
    pages[22] = home([])
    calls, _ = install_api(c, monkeypatch, pages)
    completed = []
    original = c.run_collection
    def collect(*args, **kwargs):
        completed.append(kwargs["post_id"])
        return original(*args, **kwargs)
    monkeypatch.setattr(c, "run_collection", collect)
    result = c.run_batch(tmp_path / "batch", ["zzz"], raw_root=tmp_path / "raw",
                         max_posts=None, max_discovery_pages=None)
    news21 = calls.index((c.WEB_HOME_URL, {"gids": 8, "page": 21, "page_size": 20}))
    assert sum(url == c.COMMENTS_URL for url, _ in calls[:news21]) == 20
    assert completed == [str(1000 + n) for n in range(1, 22)]
    assert result["completed"]


def test_home_resume_collects_saved_candidates_before_requesting_new_page(collector, monkeypatch, tmp_path):
    c = collector
    pages = {n: home([str(1000 + n)]) for n in range(1, 24)}
    calls, _ = install_api(c, monkeypatch, pages)
    root = tmp_path / "raw"
    source, _ = c.discover_posts(root / "zzz/_discovery", game="zzz")
    c.discover_home_pages("zzz", root / "zzz", 21, c.request_json, c.build_discovery_tasks, source)
    calls.clear()
    result = c.run_batch(tmp_path / "batch", ["zzz"], raw_root=root, max_posts=None, max_discovery_pages=22)
    news22 = calls.index((c.WEB_HOME_URL, {"gids": 8, "page": 22, "page_size": 20}))
    assert calls[0][0] == c.POST_URL
    assert sum(url == c.COMMENTS_URL for url, _ in calls[:news22]) == 21
    assert result["status"] == "limited"
