"""串行请求、动态 WBI 和脱敏；不打印响应 URL 或原始异常中的凭据。"""

from hashlib import md5
import importlib
import json
import os
import time
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

import requests

from .config import project_path
from .endpoints import PLATFORM_ENDPOINTS
from .storage import utc_now


PUBLIC_PARAMS = set("mid order ps pn index order_avoided platform web_location type mode plat seek_rpid "
                    "oid root pagination_str sec_user_id max_cursor count locate_query show_live_replay_strategy "
                    "need_time_list time_list_query cut_version publish_video_strategy_type from_user_page "
                    "device_platform aid channel pc_img_format aweme_id cursor item_type insert_ids item_id "
                    "comment_id".split())
# 这两个空字段来自浏览器样本；非空时可能携带会话信息，因此不在白名单里公开值。
PUBLIC_PARAMS |= {"whale_cut_token", "rcFT"}


def redacted(params):
    return {str(k): (v if k in PUBLIC_PARAMS and k not in {"whale_cut_token", "rcFT"} else "[REDACTED]")
            for k, v in params.items()}


def wire_params(params):
    return {k: (str(v).lower() if isinstance(v, bool) else str(v)) for k, v in params.items()}


class CollectionError(ValueError):
    """错误原因使用固定文本，避免平台 message 或带查询参数的异常泄露会话。"""


def wbi_sign(params, img_key, sub_key, timestamp=None):
    """每次请求重算 wts/w_rid；不重用浏览器中的旧签名。"""
    order = (46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
             33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
             61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
             36, 20, 34, 44, 52)
    original = img_key + sub_key
    if len(original) != 64:
        raise CollectionError("WBI 密钥结构不符合预期。")
    mixed = "".join(original[i] for i in order)[:32]
    values = wire_params({k: v for k, v in params.items() if k not in {"wts", "w_rid"}})
    values["wts"] = str(round(time.time()) if timestamp is None else timestamp)
    values = {k: "".join(c for c in values[k] if c not in "!'()*") for k in sorted(values)}
    values["w_rid"] = md5((urlencode(values) + mixed).encode()).hexdigest()
    return values


class VideoHTTP:
    def __init__(self, config, game_root, context=None, signer=None):
        self.config, self.game_root = config, game_root
        self.session = requests.Session()
        host = "https://www.bilibili.com/" if config.platform == "bilibili" else "https://www.douyin.com/"
        self.session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                                                  "Chrome/130.0.0.0 Safari/537.36",
                                     "Referer": host, "Accept": "application/json, text/plain, */*"})
        self.context = {}
        if context:
            self.context = json.loads(project_path(context).read_text(encoding="utf-8-sig"))
            if not isinstance(self.context, dict) or set(self.context) - {"headers", "params"}:
                raise ValueError("私密上下文只接受 headers 和按接口用途分组的 params。")
            headers, params = self.context.get("headers", {}), self.context.get("params", {})
            if (not isinstance(headers, dict) or not all(isinstance(v, str) for v in headers.values())
                    or not isinstance(params, dict) or set(params) - {"common", "discovery", "comments", "replies", "detail"}
                    or not all(isinstance(v, dict) for v in params.values())):
                raise ValueError("私密上下文 headers/params 结构错误。")
            self.session.headers.update(headers)
        cookie = os.environ.get(f"HOYOSENSE_{config.platform.upper()}_COOKIE")
        if cookie:
            self.session.headers["Cookie"] = cookie
        self.keys = None
        self.signer = None
        if signer:
            module, name = signer.rsplit(":", 1)
            self.signer = getattr(importlib.import_module(module), name)
            if not callable(self.signer):
                raise ValueError("签名扩展必须是本地可调用函数。")

    def _request(self, kind, url, params, archive):
        """只重试超时、连接错误和 502/503/504；每次尝试分别保存，不丢失败响应。"""
        policy = self.config.request
        for attempt in range(policy["retries"] + 1):
            time.sleep(policy["interval_seconds"])
            try:
                response = self.session.get(url, params=params, timeout=policy["timeout_seconds"], allow_redirects=False)
            except (requests.Timeout, requests.ConnectionError):
                archive(None, {"fetched_at": utc_now(), "http_status": None, "attempt": attempt + 1,
                               "request_params": redacted(params), "stop_reason": "temporary_network_error"})
                if attempt < policy["retries"]:
                    continue
                raise CollectionError("临时网络错误达到重试上限。") from None
            except requests.RequestException:
                raise CollectionError("请求异常；未推进分页。") from None
            metadata = {"fetched_at": utc_now(), "http_status": response.status_code, "attempt": attempt + 1,
                        "request_params": redacted(params)}
            record = archive(response.content, metadata)
            print(f"{kind}: HTTP {response.status_code}", flush=True)
            if response.status_code in {502, 503, 504} and attempt < policy["retries"]:
                continue
            return response.content, metadata, record
        raise AssertionError("unreachable")

    def fetch(self, kind, url, params, archive):
        extras = self.context.get("params", {})
        # 调度器生成的作品、父评论和分页参数优先，私密上下文不能意外把游标固定在旧页。
        params = {**extras.get("common", {}), **extras.get(kind, {}), **params}
        if self.config.platform == "bilibili" and kind in {"discovery", "comments"}:
            if self.keys is None:
                from .video_storage import archive_response
                nav_url = PLATFORM_ENDPOINTS["bilibili"]["nav"]
                def nav_archive(body, meta):
                    return archive_response(self.game_root, f"_session/{uuid4().hex}", body,
                                            {**meta, "endpoint": nav_url, "kind": "nav"})
                body, meta, _ = self._request("nav", nav_url, {}, nav_archive)
                if meta["http_status"] != 200:
                    raise CollectionError("WBI 初始化 HTTP 失败。")
                try:
                    nav = json.loads(body)
                    # 未登录 nav 可能为 -101；只用于取公开密钥，不能视为登录成功。
                    if nav.get("code") not in (0, -101):
                        raise ValueError
                    images = nav["data"]["wbi_img"]
                    self.keys = tuple(urlsplit(images[k]).path.rsplit("/", 1)[-1].split(".")[0]
                                      for k in ("img_url", "sub_url"))
                except (ValueError, TypeError, KeyError, AttributeError):
                    raise CollectionError("WBI 初始化未返回可用密钥。") from None
            params = wbi_sign(params, *self.keys)
        if self.signer:
            # 本地扩展每次根据当前完整业务参数生成动态参数，签名函数无需写进仓库。
            signed = self.signer(kind=kind, url=url, params=dict(params), headers=dict(self.session.headers))
            if not isinstance(signed, dict):
                raise CollectionError("签名扩展未返回参数字典。")
            if any(str(signed.get(k)) != str(v) for k, v in params.items() if k in PUBLIC_PARAMS):
                raise CollectionError("签名扩展不能改变业务参数。")
            params = signed
        return self._request(kind, url, wire_params(params), archive)
