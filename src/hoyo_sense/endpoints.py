"""按平台集中管理已验证的接口地址，不在 URL 中写死游戏、帖子或分页参数。"""

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
    # 这里只保留平台分组。尚未验证接口，不填猜测的地址，也不代表已经支持采集。
    "bilibili": {},
    "douyin": {},
}

# 取出米游社的内层字典，采集入口直接导入使用。
MIYOUSHE_ENDPOINTS = PLATFORM_ENDPOINTS["miyoushe"]
