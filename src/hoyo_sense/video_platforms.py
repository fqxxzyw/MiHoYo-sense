"""两个平台的业务解析；分页只使用接口返回游标或已声明的页码。"""

from dataclasses import dataclass, field
import json
import re

from .endpoints import PLATFORM_ENDPOINTS
from .video_http import CollectionError
from .video_storage import string_ids


def identifier(value, *, bvid=False):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise CollectionError("ID 必须为字符串或整数。")
    value = str(value)
    pattern = r"BV[0-9A-Za-z]{10}" if bvid else r"[0-9]+"
    if not re.fullmatch(pattern, value):
        raise CollectionError("作品或评论 ID 格式错误。")
    return value


def number(value):
    if type(value) is not int or value < 0:
        raise CollectionError("分页计数必须为非负整数。")
    return value


def marker(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise CollectionError("响应缺少有效游标。")
    return str(value)


def flag(value):
    if type(value) is bool:
        return value
    if type(value) is int and value in (0, 1):
        return bool(value)
    raise CollectionError("响应缺少明确的分页结束标志。")


def records(value):
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(x, dict) for x in value):
        raise CollectionError("响应中的记录数组结构错误。")
    return value


@dataclass
class ParsedPage:
    items: list
    next_cursor: object
    ended: bool
    reported_total: object = None
    info: dict = field(default_factory=dict)


class VideoPlatform:
    def __init__(self, config, game):
        self.config, self.game = config, game
        self.platform = config.platform
        self.account = config.accounts[game]
        self.endpoints = PLATFORM_ENDPOINTS[self.platform]

    def initial(self, kind):
        if self.platform == "bilibili":
            return "" if kind == "comments" else 1
        return "0"

    def request(self, kind, position, work=None, parent=None):
        p = {**self.config.params.get("common", {}), **self.config.params.get(kind, {})}
        url = self.endpoints[kind]
        if kind == "detail":
            return url.format(work_id=work["work_id"]), {}
        if self.platform == "bilibili":
            if kind == "discovery":
                p.update(mid=self.account["mid"], pn=position)
            elif kind == "comments":
                p.update(oid=work["aid"], type=1, mode=3,
                         pagination_str=json.dumps({"offset": position}, separators=(",", ":")))
            else:
                p.update(oid=work["aid"], type=1, root=parent, pn=position)
        elif kind == "discovery":
            # 0 是待实测的第一页候选值；绝不使用交接中的中间页时间戳作起点。
            p.update(sec_user_id=self.account["sec_user_id"], max_cursor=position)
        elif kind == "comments":
            p.update(aweme_id=work["work_id"], cursor=position)
        else:
            p.update(item_id=work["work_id"], comment_id=parent, cursor=position)
        return url, p

    def decode(self, kind, body):
        if kind == "detail":
            text = body.decode("utf-8-sig")
            match = re.search(r"window\.__INITIAL_STATE__\s*=\s*", text)
            if match is None:
                raise CollectionError("HTML 中没有初始状态，可能返回了验证页面。")
            # raw_decode 只解析赋值后的一个 JSON 值，不执行后续 JavaScript。
            value, _ = json.JSONDecoder().raw_decode(text[match.end():])
        else:
            value = json.loads(body)
        if not isinstance(value, dict):
            raise CollectionError("响应最外层不是 JSON 对象。")
        return string_ids(value)

    def parse(self, kind, value, position, work=None, parent=None):
        if kind == "detail":
            video = value["videoData"]
            if identifier(video["bvid"], bvid=True) != work["work_id"]:
                raise CollectionError("详情中的 bvid 不匹配。")
            aid = identifier(video["aid"])
            if identifier(video["owner"]["mid"]) != self.account["mid"]:
                raise CollectionError("作品不属于所选官方账号。")
            if not isinstance(video.get("desc"), str):
                raise CollectionError("视频详情缺少 desc。")
            return ParsedPage([video], None, True, info={"aid": aid, "metadata": video})
        status_key = "code" if self.platform == "bilibili" else "status_code"
        if type(value.get(status_key)) is not int or value[status_key] != 0:
            raise CollectionError("接口业务状态失败或缺失。")
        return (self._bilibili(kind, value["data"], position, work, parent)
                if self.platform == "bilibili" else self._douyin(kind, value, work, parent))

    def _bilibili(self, kind, data, position, work, parent):
        if kind == "discovery":
            items = records(data["list"]["vlist"])
            page = data["page"]
            pn, ps, total = number(page["pn"]), number(page["ps"]), number(page["count"])
            if pn != position or ps < 1:
                raise CollectionError("作品列表返回的页码或大小不匹配。")
            for item in items:
                identifier(item["bvid"], bvid=True)
                identifier(item["aid"])
                if identifier(item["mid"]) != self.account["mid"]:
                    raise CollectionError("作品列表包含其他账号的作品。")
            return ParsedPage(items, pn + 1, pn * ps >= total, total)
        items = records(data["replies"])
        for item in items:
            identifier(item.get("rpid_str") or item["rpid"])
            if identifier(item["oid"]) != work["aid"]:
                raise CollectionError("评论 oid 与视频 aid 不匹配。")
            if kind == "replies" and identifier(item["root"]) != parent:
                raise CollectionError("回复 root 与请求的父评论不匹配。")
            if kind == "comments" and str(item["root"]) != "0":
                raise CollectionError("一级评论返回了非根评论。")
        if kind == "comments":
            cursor = data["cursor"]
            ended = flag(cursor["is_end"])
            if cursor["mode"] != 3:
                raise CollectionError("返回的评论排序模式不匹配。")
            offset = None if ended else marker(cursor["pagination_reply"]["next_offset"])
            return ParsedPage(items, offset, ended, cursor.get("all_count"), {"sort_mode": 3})
        page = data["page"]
        pn, ps, total = number(page["num"]), number(page["size"]), number(page["count"])
        if pn != position or ps < 1:
            raise CollectionError("回复列表返回的页码或大小不匹配。")
        root = data["root"]
        if identifier(root.get("rpid_str") or root["rpid"]) != parent:
            raise CollectionError("返回的根评论不匹配。")
        if identifier(root["oid"]) != work["aid"]:
            raise CollectionError("根评论 oid 与请求作品不匹配。")
        # root.count 和 root.rcount 原样保存在完整 JSON 中，不用它们代替 page.count。
        return ParsedPage(items, pn + 1, pn * ps >= total, total)

    def _douyin(self, kind, data, work, parent):
        ended = not flag(data["has_more"])
        if kind == "discovery":
            items = records(data["aweme_list"])
            for item in items:
                identifier(item["aweme_id"])
                author = item["author"]
                # 部分响应不带 sec_uid，此时不猜测昵称映射，保留请求账号作为来源上下文。
                sec_uid = author.get("sec_uid") or author.get("sec_user_id")
                if sec_uid and sec_uid != self.account["sec_user_id"]:
                    raise CollectionError("作品作者标识与请求账号不匹配。")
            return ParsedPage(items, marker(data["max_cursor"]), ended,
                              info={"author_identity": "sec_uid_if_present_else_request_context"})
        items = records(data["comments"])
        for item in items:
            identifier(item["cid"])
            if identifier(item["aweme_id"]) != work["work_id"]:
                raise CollectionError("评论 aweme_id 不匹配。")
            expected_parent = "0" if kind == "comments" else parent
            if identifier(item["reply_id"]) != expected_parent:
                raise CollectionError("评论 reply_id 与请求分支不匹配。")
            # root_comment_id 可能等于回复自身，不参与一级父评论归属判断。
        return ParsedPage(items, marker(data["cursor"]), ended, data.get("total"))

    def item_id(self, kind, item):
        if kind == "discovery":
            return identifier(item["bvid"], bvid=True) if self.platform == "bilibili" else identifier(item["aweme_id"])
        return (identifier(item.get("rpid_str") or item["rpid"]) if self.platform == "bilibili"
                else identifier(item["cid"]))

    def reply_candidates(self, items):
        result = []
        for item in items:
            if self.platform == "bilibili":
                has_replies = any(number(item.get(k, 0)) > 0 for k in ("count", "rcount")) or bool(item.get("replies"))
            else:
                has_replies = number(item.get("reply_comment_total", 0)) > 0 or bool(item.get("reply_comment"))
            if has_replies:
                result.append(self.item_id("comments", item))
        return result
