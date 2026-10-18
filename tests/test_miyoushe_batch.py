"""离线验证三个游戏隔离、批次累计上限、异常恢复和旧发现快照复用。"""

import importlib.util
from pathlib import Path
import sys

import pytest


@pytest.fixture
def collector(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "scripts/01_collect_miyoushe.py"
    spec = importlib.util.spec_from_file_location("batch_collector", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.requests, "get", lambda *a, **k: pytest.fail("测试不得访问网络"))
    return module


def install_api(c, monkeypatch):
    calls = []

    def request(url, params):
        calls.append((url, dict(params)))
        gid = params["gids"]
        if url == c.WEB_HOME_URL:
            assert params == {"gids": gid, "page": 1, "page_size": 20}
            posts = [{"post": {"post_id": str(gid * 100 + i), "game_id": gid,
                               "subject": f"游戏 {gid} 的帖子 {i}"}} for i in range(3)]
            # 重复记录不能变成两个采集任务，跨游戏记录应保留但不采集。
            return {"retcode": 0, "data": {"recommended_posts": [*posts, posts[0],
                {"post": {"post_id": "9999", "game_id": 99, "subject": "跨游戏"}}]}}
        post_id = params["post_id"]
        assert int(post_id) // 100 == gid
        if url == c.POST_URL:
            return {"retcode": 0, "data": {"post": {"post": {
                "post_id": post_id, "game_id": gid, "subject": "测试", "content": "<p>原文</p>",
            }}}}
        is_top = url == c.COMMENTS_URL
        if is_top:
            assert params["is_hot"] == "true"
        else:
            assert params["floor_id"] == "35"
        return {"retcode": 0, "data": {"list": [{
            "reply": {"post_id": post_id, "reply_id": post_id + ("1" if is_top else "2"),
                      "floor_id": 35, "content": ""},
            "stat": {}, "sub_reply_count": 1 if is_top else 0, "sub_replies": [],
        }], "is_last": True, "last_id": "2110605758023241729"}}

    monkeypatch.setattr(c, "request_json", request)
    return calls, request


@pytest.mark.parametrize("game,gid", [("genshin", 2), ("starrail", 6), ("zzz", 8)])
def test_single_game_post_and_reply_relations_and_no_refetch(collector, monkeypatch, tmp_path, game, gid):
    c = collector
    calls, _ = install_api(c, monkeypatch)
    post_id = str(gid * 100)
    state = c.run_collection(tmp_path, game=game, post_id=post_id)
    assert state["game"] == game and state["game_id"] == gid and state["completed"]
    assert state["tasks"]["floor_35"]["parent_reply_id"] == post_id + "1"
    files = {p: p.read_bytes() for p in tmp_path.rglob("*.json") if p.name != "manifest.json"}
    assert tmp_path / f"post_{post_id}.json" in files
    calls.clear()
    c.run_collection(tmp_path, game=game, post_id=post_id)
    assert calls == [] and all(p.read_bytes() == content for p, content in files.items())


def test_three_game_batch_cumulative_limits_reuse_and_exclusion(collector, monkeypatch, tmp_path):
    c = collector
    calls, _ = install_api(c, monkeypatch)
    batch_dir, raw_root = tmp_path / "batch", tmp_path / "raw"
    state = c.run_batch(batch_dir, raw_root=raw_root, max_posts=1)
    assert state["status"] == "limited" and not state["completed"]
    assert len(calls) == 12  # 每游戏：发现、详情、顶层、楼中楼各一次。
    for game, group in state["batches"].items():
        assert len(group["tasks"]) == 3 and len(group["excluded"]) == 1
        assert [t["status"] for t in group["tasks"]] == ["completed", "deferred", "deferred"]
        assert group["tasks"][0]["source"]["source_indices"] == [1, 4]
        assert group["tasks"][0]["run_dir"].startswith(game + "/")
    raw_files = {p: c.digest(p) for p in raw_root.rglob("*.json") if p.name != "manifest.json"}
    calls.clear()
    c.run_batch(batch_dir, raw_root=raw_root, max_posts=1)
    assert calls == []
    assert all(c.digest(p) == sha for p, sha in raw_files.items())
    c.run_batch(batch_dir, raw_root=raw_root, max_posts=2)
    assert len(calls) == 9 and all(url != c.WEB_HOME_URL for url, _ in calls)
    assert {params["post_id"] for _, params in calls} == {"201", "601", "801"}
    calls.clear()
    state = c.run_batch(batch_dir, raw_root=raw_root, max_posts=3)
    assert state["status"] == "completed" and len(calls) == 9


def test_existing_discovery_is_reused_without_changing_source(collector, monkeypatch, tmp_path):
    c = collector
    calls, _ = install_api(c, monkeypatch)
    raw_root = tmp_path / "raw"
    discovery_dir, _ = c.discover_posts(raw_root / "starrail" / "_discovery", game="starrail")
    before = {p: p.read_bytes() for p in discovery_dir.iterdir()}
    calls.clear()
    c.run_batch(tmp_path / "batch", ["starrail"], raw_root=raw_root, max_posts=1)
    assert all(url != c.WEB_HOME_URL for url, _ in calls)
    assert all(p.read_bytes() == data for p, data in before.items())


def test_batch_failure_and_resume_only_failed_request(collector, monkeypatch, tmp_path):
    c = collector
    calls, normal = install_api(c, monkeypatch)

    def fail_sub(url, params):
        if url == c.SUB_REPLIES_URL:
            raise c.requests.Timeout("离线模拟超时")
        return normal(url, params)

    monkeypatch.setattr(c, "request_json", fail_sub)
    with pytest.raises(c.requests.Timeout):
        c.run_batch(tmp_path / "batch", ["zzz"], raw_root=tmp_path / "raw", max_posts=1)
    state = c.read_json(tmp_path / "batch" / "batch_manifest.json")
    assert state["status"] == "interrupted"
    assert state["batches"]["zzz"]["tasks"][0]["status"] == "interrupted"
    calls.clear()
    monkeypatch.setattr(c, "request_json", normal)
    c.run_batch(tmp_path / "batch", ["zzz"], raw_root=tmp_path / "raw", max_posts=1)
    assert len(calls) == 1 and calls[0][0] == c.SUB_REPLIES_URL


def test_cross_game_post_detail_is_rejected_and_raw_retained(collector, monkeypatch, tmp_path):
    c = collector
    _, normal = install_api(c, monkeypatch)

    def wrong_game(url, params):
        result = normal(url, params)
        if url == c.POST_URL:
            result["data"]["post"]["post"]["game_id"] = 2
        return result

    monkeypatch.setattr(c, "request_json", wrong_game)
    with pytest.raises(ValueError, match="所属游戏"):
        c.run_collection(tmp_path, game="zzz", post_id="800")
    assert (tmp_path / "post_800.json").exists()
    calls = []
    monkeypatch.setattr(c, "request_json", lambda *a: calls.append(a))
    with pytest.raises(ValueError, match="所属游戏"):
        c.run_collection(tmp_path, game="zzz", post_id="800")
    assert calls == []


def test_mismatched_game_cannot_overwrite_existing_checkpoint(collector, monkeypatch, tmp_path):
    c = collector
    install_api(c, monkeypatch)
    c.run_collection(tmp_path, game="zzz", post_id="800")
    before = (tmp_path / "manifest.json").read_bytes()
    with pytest.raises(ValueError, match="采样配置"):
        c.run_collection(tmp_path, game="starrail", post_id="800")
    assert (tmp_path / "manifest.json").read_bytes() == before


def test_tampered_discovery_stops_without_network(collector, monkeypatch, tmp_path):
    c = collector
    calls, _ = install_api(c, monkeypatch)
    root = tmp_path / "raw"
    batch = c.run_batch(tmp_path / "batch", ["genshin"], raw_root=root, max_posts=1)
    source = root / batch["batches"]["genshin"]["discovery_dir"] / "webHome_page1.json"
    source.write_text("{}", encoding="utf-8")
    calls.clear()
    with pytest.raises(ValueError, match="发生修改"):
        c.run_batch(tmp_path / "batch", ["genshin"], raw_root=root, max_posts=1)
    assert calls == []


def test_empty_discovery_is_not_claimed_as_complete(collector, monkeypatch, tmp_path):
    c = collector
    monkeypatch.setattr(c, "request_json", lambda *a: {"retcode": 0, "data": {"recommended_posts": []}})
    batch = c.run_batch(tmp_path / "batch", ["zzz"], raw_root=tmp_path / "raw")
    assert batch["status"] == "no_posts" and not batch["completed"]


def test_cli_all_discovery_never_starts_collection(collector, monkeypatch, tmp_path):
    c = collector
    calls, _ = install_api(c, monkeypatch)
    monkeypatch.setattr(c, "RAW_DIR", tmp_path / "genshin")
    monkeypatch.setattr(sys, "argv", ["collector", "--discover-posts", "--game", "all"])
    c.main()
    assert len(calls) == 3 and all(url == c.WEB_HOME_URL for url, _ in calls)
    assert [params["gids"] for _, params in calls] == [2, 6, 8]


def test_cli_single_game_requires_explicit_post_id(collector, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["collector", "--game", "zzz"])
    with pytest.raises(SystemExit) as error:
        collector.main()
    assert error.value.code == 2


def test_git_line_ending_conversion_resumes_without_rewriting_raw(collector, monkeypatch, tmp_path):
    c = collector
    calls, _ = install_api(c, monkeypatch)
    c.run_collection(tmp_path, game="zzz", post_id="800")
    files = [p for p in tmp_path.rglob("*.json") if p.name != "manifest.json"]
    for p in files:
        # 确实切换一次物理换行：Windows 原本就是 CRLF，不能再次写相同字节。
        content = p.read_bytes()
        p.write_bytes(content.replace(b"\r\n", b"\n") if b"\r\n" in content
                      else content.replace(b"\n", b"\r\n"))
    expected = {p: p.read_bytes() for p in files}
    calls.clear()
    state = c.run_collection(tmp_path, game="zzz", post_id="800")
    assert calls == [] and state["completed"]
    assert all(p.read_bytes() == content for p, content in expected.items())
    assert state["tasks"]["post"]["original_sha256"]
    assert state["tasks"]["top"]["pages"][0]["original_sha256"]
    c.run_collection(tmp_path, game="zzz", post_id="800")
    assert calls == []
    # 即使同时转了换行，真实文本篡改也必须被拒绝。
    post = tmp_path / "post_800.json"
    post.write_bytes(post.read_bytes().replace("原文".encode(), "修改".encode()))
    with pytest.raises(ValueError, match="发生修改"):
        c.run_collection(tmp_path, game="zzz", post_id="800")
