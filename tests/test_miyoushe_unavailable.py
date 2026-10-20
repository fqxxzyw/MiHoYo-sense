"""离线验证：只跳过详情 1105、继续后续帖子，其他错误仍停止并保留断点。"""

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def api(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "scripts/01_collect_miyoushe.py"
    spec = importlib.util.spec_from_file_location("unavailable_collector", path)
    c = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(c)
    monkeypatch.setattr(c.time, "sleep", lambda *_: None)
    calls = []
    behavior = {"stage": "post", "retcode": 1105, "failure": None}

    def get(url, *, params, **kwargs):
        calls.append((url, dict(params)))
        gid = params["gids"]
        post_id = params.get("post_id")
        stage = "post" if url == c.POST_URL else "comment"
        if post_id == "68861273" and stage == behavior["stage"]:
            if behavior["failure"] == "timeout":
                raise c.requests.Timeout("模拟超时")
            if behavior["failure"] in (403, 429):
                code = behavior["failure"]
                response = SimpleNamespace(status_code=code, headers={}, url=url)
                def raise_for_status():
                    raise c.requests.HTTPError(str(code), response=response)
                response.raise_for_status = raise_for_status
                return response
            result = {"retcode": behavior["retcode"], "message": "数据迷了路", "data": None}
        elif url in (c.WEB_HOME_URL, c.MIYOUSHE_ENDPOINTS["news_list"]):
            posts = [{"post": {"post_id": pid, "subject": "样本 " + pid,
                               "game_id": 0 if url != c.WEB_HOME_URL else gid}}
                     for pid in ("68861273", "68861274")]
            result = {"retcode": 0, "data": (
                {"recommended_posts": posts} if url == c.WEB_HOME_URL else
                {"list": posts, "last_id": "20", "is_last": True})}
        elif url == c.POST_URL:
            result = {"retcode": 0, "data": {"post": {"post": {
                "post_id": post_id, "game_id": gid, "subject": "样本", "content": "<p>原文</p>"}}}}
        else:
            result = {"retcode": 0, "data": {"list": [], "last_id": "20", "is_last": True}}
        return SimpleNamespace(status_code=200, headers={}, url=url,
                               raise_for_status=lambda: None, json=lambda: result)

    monkeypatch.setattr(c.requests, "get", get)
    return c, calls, behavior


def batch(c, tmp_path, mode, **kwargs):
    if mode == "home":
        return c.run_batch(tmp_path / "batch", ["starrail"], raw_root=tmp_path / "raw",
                           max_posts=None, max_pages=None, max_sub_pages=None, max_floors=None,
                           max_discovery_pages=1, **kwargs)
    return c.run_news_batch(tmp_path / "batch", tmp_path / "raw/genshin",
                            c.request_json, c.run_collection, **kwargs)


@pytest.mark.parametrize("mode,game", [("home", "starrail"), ("news", "genshin")])
def test_detail_1105_continues_batch_without_claiming_completion(api, tmp_path, mode, game):
    c, calls, _ = api
    result = batch(c, tmp_path, mode)
    tasks = result["batches"][game]["tasks"] if mode == "home" else result["tasks"]
    assert [t["status"] for t in tasks] == ["skipped_unavailable", "completed"]
    assert result["status"] == "finished_with_skips" and not result["completed"]
    assert not result["collection_completed"] and result["posts_skipped"] == 1
    assert result["collection_drained"]
    bad_dir = tmp_path / "raw" / game / "68861273/full_collection"
    state = c.read_json(bad_dir / "manifest.json")
    assert state["status"] == "skipped_unavailable" and not state["completed"]
    assert not state["tasks"]["post"]["completed"]
    error_file = bad_dir / state["unavailable"]["response_file"]
    assert c.read_json(error_file) == {"retcode": 1105, "message": "数据迷了路", "data": None}
    assert state["unavailable"]["request_params"]["post_id"] == "68861273"
    assert not (bad_dir / "post_68861273.json").exists()
    assert not any(url == c.COMMENTS_URL and p["post_id"] == "68861273" for url, p in calls)
    original = error_file.read_bytes()
    calls.clear()
    # 单帖恢复和 --verify-only 所用的只读核验也必须认识跳过状态，不能读取不存在的成功详情。
    c.audit(state, bad_dir)
    assert c.run_collection(bad_dir, game=game, post_id="68861273")["status"] == "skipped_unavailable"
    assert calls == []
    batch(c, tmp_path, mode)
    assert calls == [] and error_file.read_bytes() == original


@pytest.mark.parametrize("mode", ["home", "news"])
@pytest.mark.parametrize("failure", ["timeout", 403, 429, "other_business", "comment_1105"])
def test_other_failures_stop_batch_and_never_request_next_post(api, tmp_path, mode, failure):
    c, calls, behavior = api
    if failure == "comment_1105":
        behavior["stage"] = "comment"
    elif failure == "other_business":
        behavior["retcode"] = 1001
    else:
        behavior["failure"] = failure
    with pytest.raises((c.requests.RequestException, c.APIBusinessError)):
        batch(c, tmp_path, mode)
    assert not any(p.get("post_id") == "68861274" for _, p in calls)
    result = c.read_json(tmp_path / "batch/batch_manifest.json")
    assert result["status"] == "interrupted" and not result["completed"]
    assert not list(tmp_path.rglob("post_unavailable_attempt_*.json"))


@pytest.mark.parametrize("mode,game", [("home", "starrail"), ("news", "genshin")])
def test_explicit_retry_restores_unavailable_post_and_retains_error_evidence(api, tmp_path, mode, game):
    c, calls, behavior = api
    batch(c, tmp_path, mode)
    files = {p: p.read_bytes() for p in tmp_path.rglob("post_unavailable_attempt_*.json")}
    behavior["stage"] = "never"
    calls.clear()
    result = batch(c, tmp_path, mode, retry_unavailable=True)
    assert result["completed"] and result["posts_skipped"] == 0
    assert {p["post_id"] for _, p in calls} == {"68861273"}
    state = c.read_json(tmp_path / "raw" / game / "68861273/full_collection/manifest.json")
    assert state["completed"] and "unavailable" not in state
    assert all(p.read_bytes() == content for p, content in files.items())


def test_retry_flag_is_forwarded_by_cli(api, monkeypatch, tmp_path):
    c, _, _ = api
    calls = []
    monkeypatch.setattr(c, "run_batch", lambda *a, **k: calls.append(k))
    monkeypatch.setattr(sys, "argv", ["collector", "--batch", "--game", "starrail",
                                     "--raw-root", str(tmp_path), "--retry-unavailable"])
    c.main()
    assert calls[0]["retry_unavailable"] is True


def test_skipped_news_post_does_not_block_next_round_or_retry_each_round(api, monkeypatch, tmp_path):
    c, calls, _ = api
    original_get = c.requests.get
    def get(url, *, params, **kwargs):
        if url != c.MIYOUSHE_ENDPOINTS["news_list"]:
            return original_get(url, params=params, **kwargs)
        calls.append((url, dict(params)))
        first = params["last_id"] == ""
        result = {"retcode": 0, "data": {
            "list": [{"post": {"post_id": "68861273" if first else "68861274",
                               "subject": "样本", "game_id": 0}}],
            "last_id": "20" if first else "40", "is_last": not first}}
        return SimpleNamespace(status_code=200, headers={}, url=url,
                               raise_for_status=lambda: None, json=lambda: result)
    monkeypatch.setattr(c.requests, "get", get)
    result = batch(c, tmp_path, "news", discovery_batch_pages=1, retry_unavailable=True)
    assert result["status"] == "finished_with_skips" and not result["completed"]
    assert sum(url == c.POST_URL and p["post_id"] == "68861273" for url, p in calls) == 1
    second_list = next(i for i, (url, p) in enumerate(calls)
                       if url == c.MIYOUSHE_ENDPOINTS["news_list"] and p["last_id"] == "20")
    bad_detail = next(i for i, (url, p) in enumerate(calls)
                      if url == c.POST_URL and p["post_id"] == "68861273")
    good_detail = next(i for i, (url, p) in enumerate(calls)
                       if url == c.POST_URL and p["post_id"] == "68861274")
    assert bad_detail < second_list < good_detail
