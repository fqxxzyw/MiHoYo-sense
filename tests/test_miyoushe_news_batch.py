"""资讯候选接入原采集器；离线验证详情归属、断点复用、批次恢复及无限制参数。"""

import importlib.util
from pathlib import Path
import sys
from typing import Any

import pytest


@pytest.fixture
def collector(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "scripts/01_collect_miyoushe.py"
    spec = importlib.util.spec_from_file_location("news_batch_collector", path)
    assert spec is not None and spec.loader is not None
    c: Any = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(c)
    monkeypatch.setitem(vars(c.requests), "get",
                        lambda *_args, **_kwargs: pytest.fail("离线测试禁止网络"))
    return c


def install_api(c, monkeypatch, *, wrong_game=None, missing_game=False, failing_id=None):
    calls = []

    def reply(post_id, reply_id, floor=35):
        return {"reply": {"post_id": post_id, "reply_id": reply_id,
                          "floor_id": floor, "content": "保留评论"},
                "sub_reply_count": 0, "sub_replies": []}

    def request(url, params):
        calls.append((url, dict(params)))
        post_id = params.get("post_id")
        if url == c.MIYOUSHE_ENDPOINTS["news_list"]:
            return {"retcode": 0, "data": {
                "list": [{"post": {"post_id": pid, "subject": "资讯 " + pid, "game_id": 0}}
                         for pid in ["800", "801"]],
                "last_id": "20", "is_last": True,
            }}
        if url == c.POST_URL:
            post = {"post_id": post_id, "game_id": wrong_game or 2,
                    "subject": "详情 " + post_id, "content": "<p>完整原文</p>"}
            if missing_game:
                post.pop("game_id")
            return {"retcode": 0, "data": {"post": {"post": post}}}
        if post_id == failing_id:
            raise c.requests.Timeout("模拟评论超时")
        if url == c.COMMENTS_URL:
            if params.get("last_id") is None:
                item = reply(post_id, post_id + "1")
                item["sub_reply_count"] = 2
                return {"retcode": 0, "data": {"list": [item], "last_id": "20", "is_last": False}}
            return {"retcode": 0, "data": {
                "list": [reply(post_id, post_id + "2", 36)], "last_id": "40", "is_last": True}}
        assert url == c.SUB_REPLIES_URL
        marker = params.get("last_id")
        return {"retcode": 0, "data": {
            "list": [reply(post_id, post_id + ("s1" if marker is None else "s2"))],
            "last_id": "long-string-20" if marker is None else "long-string-40",
            "is_last": marker is not None,
        }}

    monkeypatch.setitem(vars(c), "request_json", request)
    return calls


def run(c, root, **kwargs):
    return c.run_news_batch(root / "_batches/genshin_news", root / "genshin",
                            c.request_json, c.run_collection, **kwargs)


def test_unlimited_news_batch_uses_original_comment_interfaces_and_resumes(collector, monkeypatch, tmp_path):
    c = collector
    calls = install_api(c, monkeypatch)
    # 先按用户要求少量抽样，再由无上限批次复用这些页面。
    sample_dir = tmp_path / "genshin/800/full_collection"
    c.run_collection(sample_dir, game="genshin", post_id="800",
                     max_pages=1, max_sub_pages=1, max_floors=1)
    before = {path: path.read_bytes() for path in sample_dir.rglob("*.json") if path.name != "manifest.json"}
    calls.clear()
    batch = run(c, tmp_path)
    assert batch["completed"] and batch["collection_completed"]
    assert batch["attempts"][-1]["max_discovery_pages"] is None
    for name in ("max_posts", "max_pages", "max_sub_pages", "max_floors"):
        assert batch["attempts"][-1][name] is None
    assert [t["post_id"] for t in batch["tasks"]] == ["800", "801"]
    assert all(t["detail_validated"] and t["validated_game_id"] == 2 for t in batch["tasks"])
    assert batch["tasks"][0]["sources"][0]["game_id"] == 0
    assert not any(url == c.POST_URL and p["post_id"] == "800" for url, p in calls)
    assert not any(url == c.COMMENTS_URL and p["post_id"] == "800" and "last_id" not in p for url, p in calls)
    assert not any(url == c.SUB_REPLIES_URL and p["post_id"] == "800" and "last_id" not in p for url, p in calls)
    assert all(path.read_bytes() == value for path, value in before.items())
    calls.clear()
    run(c, tmp_path)
    assert calls == []


def test_transient_checkpoint_access_denied_does_not_interrupt_batch(collector, monkeypatch, tmp_path):
    from hoyo_sense import storage

    c = collector
    calls = install_api(c, monkeypatch)
    original_replace = storage.os.replace
    failed = False
    checkpoint = tmp_path / "genshin/800/full_collection/manifest.json"

    def replace(source, target):
        nonlocal failed
        if Path(target) == checkpoint and not failed:
            state = storage.read_json(source)
            if state["tasks"]["top"].get("pages"):
                failed = True
                error = PermissionError("模拟评论页落盘后断点被占用")
                error.winerror = 5
                raise error
        original_replace(source, target)

    monkeypatch.setattr(storage.os, "replace", replace)
    monkeypatch.setattr(storage.time, "sleep", lambda _: None)
    assert run(c, tmp_path)["completed"]
    assert failed
    # 同一评论游标没有因断点替换重试而重复请求。
    requests = [(url, tuple(sorted(params.items()))) for url, params in calls]
    assert len(requests) == len(set(requests))
    calls.clear()
    assert run(c, tmp_path)["completed"]
    assert calls == []


@pytest.mark.parametrize("wrong_game,missing_game", [(6, False), (None, True)])
def test_detail_game_must_validate_before_any_comment_request(
        collector, monkeypatch, tmp_path, wrong_game, missing_game):
    c = collector
    calls = install_api(c, monkeypatch, wrong_game=wrong_game, missing_game=missing_game)
    with pytest.raises(ValueError):
        run(c, tmp_path)
    assert all(url not in (c.COMMENTS_URL, c.SUB_REPLIES_URL) for url, _ in calls)
    batch = c.read_json(tmp_path / "_batches/genshin_news/batch_manifest.json")
    assert batch["status"] == "interrupted"
    assert batch["tasks"][0]["status"] == "interrupted"
    assert not batch["tasks"][0]["detail_validated"]


def test_legacy_home_sources_merged_once_and_old_batch_untouched(collector, monkeypatch, tmp_path):
    c = collector
    install_api(c, monkeypatch)
    old_path = tmp_path / "_batches/genshin/batch_manifest.json"
    c.save_json({"batches": {"genshin": {"tasks": [
        {"source": {"post_id": "800", "subject": "首页同帖", "game_id": 2,
                    "source": "webHome/recommended_posts", "request_params": {"gids": 2}}},
    ]}}}, old_path)
    before = old_path.read_bytes()
    batch = run(c, tmp_path)
    assert batch["completed"] and batch["legacy_candidate_count"] == 1
    assert len(batch["tasks"]) == 2 and len(batch["tasks"][0]["sources"]) == 2
    assert old_path.read_bytes() == before


def test_comment_failure_saves_batch_and_resumes_only_unfinished_pages(collector, monkeypatch, tmp_path):
    c = collector
    calls = install_api(c, monkeypatch, failing_id="801")
    with pytest.raises(c.requests.Timeout):
        run(c, tmp_path)
    batch = c.read_json(tmp_path / "_batches/genshin_news/batch_manifest.json")
    assert batch["status"] == "interrupted" and batch["tasks"][0]["completed"]
    assert batch["tasks"][1]["status"] == "interrupted"
    calls = install_api(c, monkeypatch)
    assert run(c, tmp_path)["completed"]
    assert all(p.get("post_id") != "800" for _, p in calls)
    assert all(url != c.MIYOUSHE_ENDPOINTS["news_list"] for url, _ in calls)


def test_post_limit_is_cumulative_not_rotated(collector, monkeypatch, tmp_path):
    c = collector
    calls = install_api(c, monkeypatch)
    batch = run(c, tmp_path, max_posts=1)
    assert batch["status"] == "limited" and batch["tasks"][1]["status"] == "limited"
    calls.clear()
    run(c, tmp_path, max_posts=1)
    assert calls == []
    assert run(c, tmp_path, max_posts=None)["completed"]
    assert all(p.get("post_id") != "800" for _, p in calls)


def test_cli_news_batch_explicit_none_and_game_restriction(collector, monkeypatch, tmp_path):
    c = collector
    monkeypatch.setitem(vars(c), "RAW_DIR", tmp_path / "genshin")
    calls = []
    monkeypatch.setitem(vars(c), "run_news_batch", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(sys, "argv", [
        "collector", "--batch-news", "--game", "genshin",
        "--max-discovery-pages", "None", "--max-posts", "None", "--max-pages", "None",
        "--max-sub-pages", "None", "--max-floors", "None",
    ])
    c.main()
    assert len(calls) == 1 and calls[0][0][0] == tmp_path / "_batches/genshin"
    assert calls[0][1]["discovery_batch_pages"] == 20
    assert calls[0][1]["retry_unavailable"] is False
    assert all(value is None for key, value in calls[0][1].items()
               if key not in ("discovery_batch_pages", "retry_unavailable"))
    monkeypatch.setattr(sys, "argv", ["collector", "--batch-news", "--game", "all"])
    with pytest.raises(SystemExit):
        c.main()


def test_original_batch_directory_preserves_legacy_and_reuses_news_substate(
        collector, monkeypatch, tmp_path):
    c = collector
    calls = install_api(c, monkeypatch)
    old_path = tmp_path / "_batches/genshin/batch_manifest.json"
    legacy_group = {"tasks": [
        {"source": {"post_id": "800", "subject": "旧首页", "game_id": 2,
                    "source": "webHome/recommended_posts", "request_params": {"gids": 2}},
         "status": "completed", "completed": True},
    ]}
    legacy_attempts = [{"status": "interrupted", "error": "保留旧执行记录"}]
    c.save_json({
        "source": "webHome/recommended_posts", "batches": {"genshin": legacy_group},
        "attempts": legacy_attempts,
    }, old_path)
    state = c.run_news_batch(old_path.parent, tmp_path / "genshin", c.request_json, c.run_collection)
    assert state["completed"]
    merged = c.read_json(old_path)
    assert merged["batches"]["genshin"] == legacy_group
    assert merged["attempts"] == legacy_attempts
    assert merged["news_collection"]["completed"] and merged["status"] == "completed"
    calls.clear()
    c.run_news_batch(old_path.parent, tmp_path / "genshin", c.request_json, c.run_collection)
    assert calls == []
    assert not (tmp_path / "_batches/genshin_news").exists()


def paged_news_api(c, monkeypatch, end_page=41):
    # 列表每页一个帖子便于检查调度顺序；详情/顶层/楼中楼仍走原采集器。
    install_api(c, monkeypatch)
    base = c.request_json
    events = []
    def request(url, params):
        if url == c.MIYOUSHE_ENDPOINTS["news_list"]:
            page = int(params["last_id"] or "0") + 1
            events.append(("list", page))
            return {"retcode": 0, "data": {
                "list": [{"post": {"post_id": str(1000 + page), "subject": "资讯", "game_id": 0}}],
                "last_id": str(page), "is_last": page == end_page,
            }}
        events.append(("post" if url == c.POST_URL else "comment", params["post_id"]))
        return base(url, params)
    monkeypatch.setattr(c, "request_json", request)
    return events


def test_news_twenty_page_rounds_collect_before_more_discovery(collector, monkeypatch, tmp_path):
    c = collector
    events = paged_news_api(c, monkeypatch)
    collected = []
    original = c.run_collection
    def collect(*args, **kwargs):
        collected.append(kwargs["post_id"])
        return original(*args, **kwargs)
    monkeypatch.setattr(c, "run_collection", collect)
    state = run(c, tmp_path)
    assert state["completed"]
    for next_page in (21, 41):
        position = events.index(("list", next_page))
        assert {pid for kind, pid in events[:position] if kind == "comment"} == {
            str(1000 + n) for n in range(1, next_page)}
    # 同一次运行不重复调用已完成帖的采集器；下一次运行仍核验原始页。
    assert collected == [str(1000 + n) for n in range(1, 42)]


def test_news_saved_backlog_collected_before_new_list_requests(collector, monkeypatch, tmp_path):
    c = collector
    events = paged_news_api(c, monkeypatch)
    c.discover_news_pages("genshin", tmp_path / "genshin", 23, c.request_json, preview_saved_pages=False)
    originals = {p: p.read_bytes() for p in (tmp_path / "genshin/_discovery").rglob("page_*.json")}
    events.clear()
    state = run(c, tmp_path, max_discovery_pages=24)
    position = events.index(("list", 24))
    assert events[0] == ("post", "1001")
    assert {pid for kind, pid in events[:position] if kind == "comment"} == {
        str(1000 + n) for n in range(1, 24)}
    assert state["status"] == "limited" and state["collection_completed"]
    assert all(p.read_bytes() == value for p, value in originals.items())


def test_news_comment_failure_never_advances_discovery_on_resume(collector, monkeypatch, tmp_path):
    c = collector
    events = paged_news_api(c, monkeypatch)
    normal = c.request_json
    def fail(url, params):
        if url == c.COMMENTS_URL and params["post_id"] == "1002":
            raise c.requests.Timeout("模拟评论中断")
        return normal(url, params)
    monkeypatch.setattr(c, "request_json", fail)
    with pytest.raises(c.requests.Timeout):
        run(c, tmp_path)
    assert [n for kind, n in events if kind == "list"] == list(range(1, 21))
    events.clear()
    monkeypatch.setattr(c, "request_json", normal)
    run(c, tmp_path, max_discovery_pages=21)
    assert events[0] == ("comment", "1002")
    assert events.index(("comment", "1020")) < events.index(("list", 21))


def test_news_post_cap_stops_before_second_discovery_round(collector, monkeypatch, tmp_path):
    c = collector
    events = paged_news_api(c, monkeypatch)
    state = run(c, tmp_path, max_posts=1)
    assert state["status"] == "limited"
    assert [n for kind, n in events if kind == "list"] == list(range(1, 21))
    assert {pid for kind, pid in events if kind == "comment"} == {"1001"}
    events.clear()
    run(c, tmp_path, max_posts=1)
    assert events == []


def test_cli_genshin_batch_routes_to_news_and_forwards_round_size(collector, monkeypatch, tmp_path):
    c = collector
    calls = []
    monkeypatch.setattr(c, "run_news_batch", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(c, "run_batch", lambda *a, **k: pytest.fail("原神自动批次应使用资讯"))
    monkeypatch.setattr(sys, "argv", ["collector", "--batch", "--game", "genshin",
                                     "--raw-root", str(tmp_path), "--discovery-batch-pages", "7"])
    c.main()
    assert calls[0][0][0] == tmp_path / "_batches/genshin"
    assert calls[0][1]["discovery_batch_pages"] == 7


@pytest.mark.parametrize("value", ["0", "None", "-1"])
def test_bad_round_size_cli_stops_before_collection(collector, monkeypatch, value):
    monkeypatch.setattr(sys, "argv", ["collector", "--batch-news", "--discovery-batch-pages", value])
    with pytest.raises(SystemExit):
        collector.main()


def test_auto_batch_imports_legacy_combined_candidates_without_modifying_file(collector, monkeypatch, tmp_path):
    c = collector
    install_api(c, monkeypatch)
    path = tmp_path / "_batches/genshin_starrail_zzz/batch_manifest.json"
    c.save_json({"batches": {"genshin": {"tasks": [
        {"source": {"post_id": "799", "subject": "旧混合批次独有帖", "game_id": 2,
                    "source": "webHome/recommended_posts", "request_params": {"gids": 2}}}
    ]}}}, path)
    before = path.read_bytes()
    state = c.run_news_batch(tmp_path / "_batches/genshin", tmp_path / "genshin",
                             c.request_json, c.run_collection)
    assert [task["post_id"] for task in state["tasks"]] == ["799", "800", "801"]
    assert state["completed"] and path.read_bytes() == before


def test_explicit_home_cli_preserves_legacy_source_and_combined_directory(collector, monkeypatch, tmp_path):
    c = collector
    calls = []
    monkeypatch.setattr(c, "run_batch", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(c, "run_news_batch", lambda *a, **k: pytest.fail("显式首页模式不能切换来源"))
    monkeypatch.setattr(sys, "argv", ["collector", "--batch-home", "--game", "all",
                                     "--raw-root", str(tmp_path)])
    c.main()
    assert calls[0][0][0] == tmp_path / "_batches/genshin_starrail_zzz"
    assert calls[0][0][1] == ["genshin", "starrail", "zzz"]
