"""交接字段构造的离线模拟数据，不冒充完整浏览器原始样本。"""

from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from hoyo_sense import video_collection as engine
from hoyo_sense import video_http as http
from hoyo_sense.storage import read_json, digest
from hoyo_sense.video_config import load_video_config
from hoyo_sense.video_platforms import VideoPlatform
from hoyo_sense.video_storage import string_ids


AWEME = "7684640572371488015"
CID = "7684907540819952421"
REPLY = "7689439909694063360"
BVID = "BV1zi7Y6BEdS"
AID = "116810225555111"
ROOT = "303897942961"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("离线测试禁止真实网络")
    monkeypatch.setattr(http.requests.Session, "get", forbidden)


def dycomment(cid=CID, parent="0", replies=1):
    return {"cid": int(cid), "aweme_id": int(AWEME), "text": "", "create_time": 1780000000,
            "digg_count": 0, "user": {"uid": 9007199254740993}, "reply_id": int(parent),
            "reply_to_reply_id": "0", "root_comment_id": int(cid),
            "reply_comment_total": replies, "reply_comment": []}


def dypage(items, cursor=10, more=1, total=3312):
    return {"status_code": 0, "comments": items, "cursor": cursor, "has_more": more, "total": total}


def dywork(aweme=AWEME):
    return {"aweme_id": int(aweme), "desc": "测试", "author": {"uid": 1736848482507527},
            "statistics": {"play_count": 0}, "create_time": 1780000000, "aweme_type": 0}


def bcomment(rpid=ROOT, root="0"):
    return {"rpid": int(rpid), "rpid_str": rpid, "oid": int(AID), "root": int(root), "parent": int(root),
            "ctime": 1780000000, "like": 0, "content": {"message": ""}, "member": {"mid": 123},
            "count": 3992 if root == "0" else 0, "rcount": 4000 if root == "0" else 0, "replies": []}


def btop(items=None, offset="next", end=False):
    return {"code": 0, "data": {"replies": [bcomment()] if items is None else items,
                                "cursor": {"is_end": end, "mode": 3, "all_count": 70897,
                                           "pagination_reply": {"next_offset": offset}}}}


def breplies(pn=1, count=3992):
    return {"code": 0, "data": {"replies": [bcomment(str(9007199254740993 + pn), ROOT)],
                                "root": bcomment(), "page": {"num": pn, "size": 10, "count": count}}}


def html():
    video = {"bvid": BVID, "aid": int(AID), "title": "测试", "desc": "视频真实简介",
             "desc_v2": [], "owner": {"mid": 401742377, "name": "原神"}, "pubdate": 1782446400,
             "ctime": 1782382949, "pages": [{"cid": 9007199254740993}], "stat": {"view": 1}}
    return ('<meta name="description" content="不是正文">\n<script>window.__INITIAL_STATE__ = '
            + json.dumps({"videoData": video}, ensure_ascii=False) + '; alert("不得执行");</script>').encode()


class FakeClient:
    def __init__(self, responder):
        self.calls, self.responder = [], responder

    def fetch(self, kind, url, params, archive):
        self.calls.append((kind, deepcopy(params)))
        value = self.responder(kind, params)
        status, value = value if isinstance(value, tuple) else (200, value)
        body = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
        metadata = {"fetched_at": "2026-10-21T00:00:00+00:00", "http_status": status,
                    "request_params": http.redacted(params), "attempt": 1}
        return body, metadata, archive(body, metadata)


def collector(tmp_path, platform, responder, **limits):
    config = load_video_config(platform)
    config.limits.update(limits)
    client = FakeClient(responder)
    return engine.VideoCollector(config, "genshin", client, tmp_path / platform), client


def test_douyin_handoff_relations_and_string_ids(tmp_path):
    def api(kind, p):
        if kind == "comments":
            return dypage([dycomment()])
        assert p["item_id"] == AWEME and p["comment_id"] == CID and p["cursor"] == "0"
        return dypage([dycomment(REPLY, CID, 0)], 3, 0, 1)
    c, client = collector(tmp_path, "douyin", api)
    state = c.run(work_id=AWEME)
    work = state["works"][AWEME]
    assert state["status"] == "limited" and not state["completed"]
    assert work["replies"][CID]["completed"]
    top = work["comments"]["pages"][0]
    assert top["record_count"] == 1 and top["reported_total"] == 3312
    assert top["output_cursor"] == "10"
    parsed = read_json(c.root / top["parsed_file"])
    comment = parsed["comments"][0]
    assert comment["cid"] == CID and comment["user"]["uid"] == "9007199254740993"
    branch = work["replies"][CID]["pages"][0]
    reply = read_json(c.root / branch["parsed_file"])["comments"][0]
    assert reply["root_comment_id"] == REPLY and reply["reply_id"] == CID
    assert branch["request_parent_comment_id"] == CID
    assert len(client.calls) == 2


def test_douyin_limit_resume_and_zero_request_rerun(tmp_path):
    def api(kind, p):
        if p["cursor"] == "0":
            return dypage([dycomment(replies=0)], cursor=47)
        assert p["cursor"] == "47"  # 响应游标，不是 0 + count。
        return dypage([dycomment(REPLY, replies=0)], cursor=99, more=0)
    c, client = collector(tmp_path, "douyin", api)
    state = c.run(work_id=AWEME, metadata=string_ids(dywork()))
    old = {p: digest(p) for p in c.root.rglob("*") if p.suffix in {".json", ".body"} and p != c.manifest}
    c.run(work_id=AWEME)
    assert len(client.calls) == 1
    c.config.limits["max_pages"] = 2
    state = c.run(work_id=AWEME)
    assert len(client.calls) == 2 and state["completed"]
    c.run(work_id=AWEME)
    assert len(client.calls) == 2
    assert all(digest(p) == sha for p, sha in old.items())


@pytest.mark.parametrize("failure_point", ["body", "http", "parsed", "commit"])
def test_crash_after_response_written_then_resume(tmp_path, monkeypatch, failure_point):
    from hoyo_sense import video_storage as storage
    c, client = collector(tmp_path, "douyin", lambda k, p: dypage([dycomment(replies=0)], more=0))
    crashed = False
    if failure_point in {"body", "http"}:
        original = storage.immutable_bytes
        def write(path, content):
            nonlocal crashed
            original(path, content)
            suffix = ".body" if failure_point == "body" else ".http.json"
            if str(path).endswith(suffix) and not crashed:
                crashed = True
                raise KeyboardInterrupt("模拟响应落盘后中断")
        monkeypatch.setattr(storage, "immutable_bytes", write)
    elif failure_point == "parsed":
        original = engine.immutable_json
        def save(path, value):
            nonlocal crashed
            original(path, value)
            if not crashed:
                crashed = True
                raise KeyboardInterrupt
        monkeypatch.setattr(engine, "immutable_json", save)
    else:
        original = c.checkpoint
        def checkpoint():
            nonlocal crashed
            task = c.state["works"].get(AWEME, {}).get("comments", {})
            if task.get("pages") and not crashed:
                crashed = True
                raise KeyboardInterrupt
            original()
        monkeypatch.setattr(c, "checkpoint", checkpoint)
    with pytest.raises(KeyboardInterrupt):
        c.run(work_id=AWEME, metadata=string_ids(dywork()))
    c.run(work_id=AWEME)
    expected = 2 if failure_point == "body" else 1
    assert len(client.calls) == expected
    assert c.state["works"][AWEME]["comments"]["completed"]


@pytest.mark.parametrize("pages,reason", [
    ([dypage([dycomment(replies=0)], 0)], "repeated_cursor"),
    ([dypage([], 10)], "empty_page_with_more"),
    ([dypage([dycomment(replies=0)], 10), dypage([dycomment(REPLY, replies=0)], 0)], "repeated_cursor"),
    ([dypage([dycomment(replies=0)], 10), dypage([dycomment(replies=0)], 20)], "no_new_record_ids"),
])
def test_pagination_stalls_never_complete_or_spin(tmp_path, pages, reason):
    iterator = iter(pages)
    c, client = collector(tmp_path, "douyin", lambda k, p: next(iterator), max_pages=None)
    state = c.run(work_id=AWEME)
    task = state["works"][AWEME]["comments"]
    assert not task["completed"] and task["stop_reason"] == reason
    assert len(client.calls) == len(pages)
    c.run(work_id=AWEME)
    assert len(client.calls) == len(pages)


@pytest.mark.parametrize("response", [
    b"", b"<html>login</html>", {"status_code": 0}, {"status_code": 8, "comments": []},
    (403, b"forbidden"), (429, b"rate limit"),
    dypage([{**dycomment(), "aweme_id": "42"}]), dypage([{**dycomment(), "reply_id": "42"}]),
    dypage([{**dycomment(), "cid": 9.007199254740993e15}]),
])
def test_invalid_http_business_and_identity_do_not_advance(tmp_path, response):
    c, client = collector(tmp_path, "douyin", lambda k, p: response)
    with pytest.raises(http.CollectionError):
        c.run(work_id=AWEME)
    task = read_json(c.manifest)["works"][AWEME]["comments"]
    assert not task["pages"] and task["next_cursor"] == "0" and not task["completed"]
    assert list(c.root.rglob("*.http.json"))
    assert c.state["status"] == "interrupted"


def test_bilibili_html_aid_offset_and_three_reply_pages(tmp_path):
    def api(kind, p):
        if kind == "detail":
            return html()
        assert p["oid"] == AID
        if kind == "comments":
            offset = json.loads(p["pagination_str"])["offset"]
            if offset == "":
                return btop(offset="opaque-not-a-number")
            assert offset == "opaque-not-a-number"
            return btop([bcomment("303897942962")], end=True)
        assert p["root"] == ROOT
        return breplies(p["pn"])
    c, client = collector(tmp_path, "bilibili", api)
    state = c.run(work_id=BVID)
    work = state["works"][BVID]
    assert work["metadata"]["desc"] == "视频真实简介"
    assert work["aid"] == AID and len(work["metadata"]["pages"]) == 1
    assert work["comments"]["pages"][0]["sort_mode"] == 3
    assert not state["completed"]
    c.config.limits.update(max_pages=2, max_sub_pages=3)
    c.run(work_id=BVID)
    branch = c.state["works"][BVID]["replies"][ROOT]
    assert len(branch["pages"]) == 3 and len({i for p in branch["pages"] for i in p["record_ids"]}) == 3
    assert branch["next_cursor"] == 4 and branch["status"] == "limited"
    assert [p["pn"] for k, p in client.calls if k == "replies"] == [1, 2, 3]


def test_discovery_restores_queue_before_fetching_next_page(tmp_path):
    def api(kind, p):
        if kind == "discovery":
            if p["max_cursor"] == "0":
                return {"status_code": 0, "aweme_list": [dywork()], "has_more": 1, "max_cursor": 1786420971000}
            assert p["max_cursor"] == "1786420971000"
            return {"status_code": 0, "aweme_list": [dywork("7684640572371488016")], "has_more": 0, "max_cursor": 123}
        assert kind == "comments"
        return dypage([], more=0)
    c, client = collector(tmp_path, "douyin", api)
    c.run(discover_only=True)
    assert [k for k, _ in client.calls] == ["discovery"]
    c.run()
    assert [k for k, _ in client.calls] == ["discovery", "comments"]
    c.run()
    assert len(client.calls) == 2
    c.config.limits.update(max_posts=2, max_discovery_pages=2)
    c.run()
    assert [k for k, _ in client.calls] == ["discovery", "comments", "discovery", "comments"]
    assert c.state["completed"]


def test_bilibili_discovery_uses_returned_count_and_page_number(tmp_path):
    def api(kind, p):
        assert kind == "discovery"
        item = {"bvid": BVID if p["pn"] == 1 else "BV1Rmh96ZEXh", "aid": int(AID), "mid": 401742377}
        return {"code": 0, "data": {"list": {"vlist": [item]}, "page": {"pn": p["pn"], "ps": 1, "count": 2}}}
    c, client = collector(tmp_path, "bilibili", api, max_discovery_pages=2)
    state = c.run(discover_only=True)
    assert state["completed"] and len(state["works"]) == 2
    assert [p["pn"] for _, p in client.calls] == [1, 2]


def test_parent_cap_and_branch_cursor_resume(tmp_path):
    second = "7684907540819952422"
    def api(kind, p):
        if kind == "comments":
            return dypage([dycomment(), dycomment(second)], more=0)
        items = [dycomment(REPLY if p["cursor"] == "0" else "7689439909694063361", p["comment_id"], 0)]
        return dypage(items, cursor=73 if p["cursor"] == "0" else 90, more=int(p["cursor"] == "0"))
    c, client = collector(tmp_path, "douyin", api)
    c.run(work_id=AWEME, metadata=string_ids(dywork()))
    assert len(client.calls) == 2
    assert c.state["works"][AWEME]["replies"][second]["status"] == "limited"
    c.config.limits.update(max_floors=2, max_sub_pages=2)
    c.run(work_id=AWEME)
    assert c.state["completed"]
    assert [p["cursor"] for k, p in client.calls if k == "replies"] == ["0", "73", "0", "73"]


def test_duplicate_raw_records_preserved(tmp_path):
    c, client = collector(tmp_path, "douyin", lambda k, p: dypage([dycomment(replies=0)] * 2, more=0))
    c.run(work_id=AWEME)
    page = c.state["works"][AWEME]["comments"]["pages"][0]
    assert page["record_count"] == 2 and page["new_record_count"] == 1
    assert len(read_json(c.root / page["parsed_file"])["comments"]) == 2


def test_known_parent_selection_does_not_collect_other_branches(tmp_path):
    c, client = collector(tmp_path, "douyin", lambda k, p: dypage([dycomment()], more=0), max_floors=0)
    c.run(work_id=AWEME)
    c.config.limits["max_floors"] = 1
    client.responder = lambda k, p: dypage([dycomment(REPLY, p["comment_id"], 0)], more=0)
    selected = "7684907540819952422"
    c.run(work_id=AWEME, parents=[selected])
    assert [p["comment_id"] for k, p in client.calls if k == "replies"] == [selected]
    assert not c.state["works"][AWEME]["replies"][CID]["completed"]


def test_immutable_archive_never_overwrites(tmp_path):
    from hoyo_sense.video_storage import immutable_bytes
    path = tmp_path / "sample.body"
    immutable_bytes(path, b"old")
    with pytest.raises(FileExistsError):
        immutable_bytes(path, b"new")
    assert path.read_bytes() == b"old"


def test_no_resume_and_changed_identity_rejected(tmp_path):
    c, client = collector(tmp_path, "douyin", lambda k, p: dypage([], more=0))
    c.run(work_id=AWEME)
    with pytest.raises(http.CollectionError):
        c.run(work_id=AWEME, resume=False)
    c.config.accounts["genshin"]["sec_user_id"] += "changed"
    with pytest.raises(http.CollectionError):
        c.run(work_id=AWEME)
    assert len(client.calls) == 1


@pytest.mark.parametrize("platform", ["bilibili", "douyin"])
def test_cli_from_other_directory_and_zero_limits(tmp_path, monkeypatch, platform):
    from hoyo_sense.video_cli import main
    monkeypatch.chdir(tmp_path)
    # 项目默认三游戏目录预先带有空占位文件，不能因此拒绝第一次启动。
    placeholders = []
    for game in ("genshin", "starrail", "zzz"):
        for relative in (".gitkeep", "comments/.gitkeep"):
            path = tmp_path / platform / game / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"")
            placeholders.append(path)
    assert main(platform, ["--game", "all", "--raw-root", str(tmp_path / platform),
                           "--discover-only", "--max-discovery-pages", "0"]) == 0
    assert all(path.read_bytes() == b"" for path in placeholders)
    for game in ("genshin", "starrail", "zzz"):
        state = read_json(tmp_path / platform / game / "_batch" / "manifest.json")
        assert not state["discovery"]["pages"] and not state["completed"]


@pytest.mark.parametrize("platform", ["bilibili", "douyin"])
@pytest.mark.parametrize("filename,content", [("unregistered.json", b"old data"),
                                              (".gitkeep", b"not a placeholder"), ("empty.json", b"")])
def test_unregistered_data_still_blocks_initialization(tmp_path, platform, filename, content):
    c, client = collector(tmp_path, platform, lambda k, p: pytest.fail("不应发起请求"))
    c.root.mkdir(parents=True)
    existing = c.root / filename
    existing.write_bytes(content)
    with pytest.raises(http.CollectionError, match="未登记文件") as error:
        c.run(discover_only=True)
    assert str(c.root) in str(error.value) and str(c.manifest) in str(error.value)
    assert existing.read_bytes() == content
    assert not c.manifest.exists() and not client.calls


def test_tampered_raw_or_config_refuses_resume(tmp_path):
    c, client = collector(tmp_path, "douyin", lambda k, p: dypage([], more=0))
    c.run(work_id=AWEME)
    body = c.root / c.state["works"][AWEME]["comments"]["pages"][0]["body_file"]
    body.write_bytes(b"modified")
    with pytest.raises(ValueError):
        c.run(work_id=AWEME)
    assert len(client.calls) == 1


def test_wbi_and_redaction():
    signed = http.wbi_sign({"foo": "114", "bar": "514", "baz": 1919810},
                           "7cd084941338484aae1ad9425b84077c", "4932caff0ff746eab6f01bf08b70ac45", 1702204169)
    assert signed["w_rid"] == "6149fdadf571698ca7e6a567265cd0ee"
    values = http.redacted({"aweme_id": AWEME, "a_bogus": "secret", "msToken": "secret", "unknown_device": "secret"})
    assert values["aweme_id"] == AWEME and "secret" not in json.dumps(values)


@pytest.mark.parametrize("status,expected", [(302, 1), (403, 1), (429, 1), (502, 2), (503, 2), (200, 1)])
def test_http_finite_retry_and_no_cookie_in_logs(tmp_path, monkeypatch, capsys, status, expected):
    config = load_video_config("douyin")
    config.request.update(interval_seconds=0, retries=1)
    monkeypatch.setenv("HOYOSENSE_DOUYIN_COOKIE", "never_print_this")
    client = http.VideoHTTP(config, tmp_path)
    assert client.session.headers["Cookie"] == "never_print_this"
    calls, saved = [], []
    def get(*args, **kwargs):
        calls.append(kwargs)
        result = http.requests.Response()
        result.status_code = status
        result._content = b"{}"
        return result
    monkeypatch.setattr(client.session, "get", get)
    client.fetch("comments", "https://www.douyin.com/test", {"cursor": "0", "msToken": "never_print_this"},
                 lambda body, meta: saved.append((body, meta)))
    assert len(calls) == expected and len(saved) == expected
    assert "never_print_this" not in capsys.readouterr().out + json.dumps(saved, default=str)


@pytest.mark.parametrize("exception", [http.requests.Timeout, http.requests.ConnectionError])
def test_network_retries_are_finite_and_sanitized(tmp_path, monkeypatch, exception):
    config = load_video_config("douyin")
    config.request.update(interval_seconds=0, retries=1)
    client = http.VideoHTTP(config, tmp_path)
    saved = []
    def fail(*args, **kwargs):
        raise exception("secret_url_with_cookie")
    monkeypatch.setattr(client.session, "get", fail)
    with pytest.raises(http.CollectionError) as error:
        client.fetch("comments", "https://www.douyin.com/test", {}, lambda body, meta: saved.append((body, meta)))
    assert len(saved) == 2 and all(body is None for body, _ in saved)
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("platform", ["bilibili", "douyin"])
@pytest.mark.parametrize("game", ["genshin", "starrail", "zzz"])
def test_three_account_contexts_and_paths(platform, game):
    config = load_video_config(platform)
    adapter = VideoPlatform(config, game)
    _, p = adapter.request("discovery", adapter.initial("discovery"))
    key = "mid" if platform == "bilibili" else "sec_user_id"
    assert p[key] == config.accounts[game][key]
    assert str(config.raw_root).endswith(platform)


@pytest.mark.parametrize("bad", [True, -1, 1.5, "3"])
def test_bad_limit_rejected(tmp_path, bad):
    data = {"accounts": load_video_config("douyin").accounts, "collection": {"max_pages": bad}}
    config_path = tmp_path / "invalid.json"
    config_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        load_video_config("douyin", config_path)
