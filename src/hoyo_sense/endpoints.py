"""按平台集中管理接口地址；地址已知不代表本地请求或分页已经验证。"""

# 外层字典的键是平台；内层字典的键是接口用途，值是完整 URL 字符串。
# 米游社三个游戏使用同一套接口，通过请求参数 gids 区分。
PLATFORM_ENDPOINTS = {
    "miyoushe": {
        "web_home": "https://bbs-api-static.miyoushe.com/apihub/wapi/webHome",
        "news_list": "https://bbs-api-static.miyoushe.com/painter/wapi/getNewsList",
        "post": "https://bbs-api.miyoushe.com/post/wapi/getPostFull",
        "comments": "https://bbs-api.miyoushe.com/post/wapi/getPostReplies",
        "sub_replies": "https://bbs-api.miyoushe.com/post/wapi/getSubReplies",
    },
    "bilibili": {
        "discovery": "https://api.bilibili.com/x/space/wbi/arc/search",
        "detail": "https://www.bilibili.com/video/{work_id}/",
        "comments": "https://api.bilibili.com/x/v2/reply/wbi/main",
        "replies": "https://api.bilibili.com/x/v2/reply/reply",
        "nav": "https://api.bilibili.com/x/web-interface/nav",
    },
    "douyin": {
        "discovery": "https://www.douyin.com/aweme/v1/web/aweme/post/",
        "comments": "https://www.douyin.com/aweme/v1/web/comment/list/",
        "replies": "https://www.douyin.com/aweme/v1/web/comment/list/reply/",
        "danmaku": "https://www.douyin.com/aweme/v1/web/danmaku/get_v2/",
    },
}

# 取出米游社的内层字典，采集入口直接导入使用。
MIYOUSHE_ENDPOINTS = PLATFORM_ENDPOINTS["miyoushe"]
