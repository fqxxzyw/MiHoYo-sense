"""两个入口共享 CLI，默认原神、一个作品和少量页面。"""

import argparse
import json

from ...config import project_path
from ...games import GAME_NAMES
from .collection import VideoCollector
from .config import load_video_config
from .http import CollectionError, VideoHTTP
from .platforms import identifier
from .storage import string_ids


def cli_limit(value):
    if value.lower() in {"none", "null"}:
        return None
    if not value.isascii() or not value.isdecimal():
        raise argparse.ArgumentTypeError("请输入非负整数或 None。")
    return int(value)


def main(platform, argv=None):
    parser = argparse.ArgumentParser(description=f"{platform} 官方账号作品与评论采集，默认小样本并自动恢复。")
    parser.add_argument("--config", help=f"默认 configs/{platform}.json")
    parser.add_argument("--game", choices=[*GAME_NAMES, "all"], default="genshin")
    parser.add_argument("--work-id", "--bvid" if platform == "bilibili" else "--aweme-id", dest="work_id")
    parser.add_argument("--parent-comment-id", action="append", default=[], help="已知父评论 ID；可重复指定，受分支上限约束")
    parser.add_argument("--metadata-file", help="抖音指定作品时可提供完整作品 JSON，否则基本信息暂缺，不阻塞评论")
    parser.add_argument("--raw-root", help="平台数据根目录；相对路径基于项目根目录")
    for name in ("max-posts", "max-discovery-pages", "max-pages", "max-sub-pages", "max-floors"):
        parser.add_argument("--" + name, type=cli_limit, default=argparse.SUPPRESS, help="累计上限；None 仅取消人工上限")
    parser.add_argument("--discover-only", action="store_true", help="仅发现官方账号作品")
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True, help="默认恢复；关闭时拒绝已有断点")
    parser.add_argument("--request-context", help="被 Git 忽略的本地 headers/params JSON；不能当作永久有效签名")
    parser.add_argument("--signer", help="可选本地 module:function，每次请求动态生成参数；本项目不内置抖音签名算法")
    args = parser.parse_args(argv)
    if args.game == "all" and args.work_id:
        parser.error("指定作品时必须选择单个游戏。")
    if args.parent_comment_id and not args.work_id:
        parser.error("指定父评论必须同时指定作品。")
    if args.discover_only and (args.work_id or args.metadata_file):
        parser.error("仅发现模式不能同时指定作品或作品 JSON。")
    if args.metadata_file and (platform != "douyin" or not args.work_id):
        parser.error("metadata-file 仅用于抖音的指定作品。")
    try:
        config = load_video_config(platform, args.config)
        for key in config.limits:
            if hasattr(args, key):
                config.limits[key] = getattr(args, key)
        raw_root = project_path(args.raw_root) if args.raw_root else config.raw_root
        metadata = None
        if args.metadata_file:
            metadata = string_ids(json.loads(project_path(args.metadata_file).read_text(encoding="utf-8-sig")))
            if not isinstance(metadata, dict) or identifier(metadata["aweme_id"]) != identifier(args.work_id):
                raise CollectionError("本地作品 JSON 的 aweme_id 不匹配。")
            if not isinstance(metadata.get("author"), dict) or not isinstance(metadata.get("desc"), str):
                raise CollectionError("本地作品 JSON 至少需要 author 与 desc。")
            sec = metadata["author"].get("sec_uid") or metadata["author"].get("sec_user_id")
            if sec and sec != config.accounts[args.game]["sec_user_id"]:
                raise CollectionError("本地作品 JSON 的作者与所选账号不匹配。")
        for game in GAME_NAMES if args.game == "all" else [args.game]:
            client = VideoHTTP(config, raw_root / game, args.request_context, args.signer)
            try:
                collector = VideoCollector(config, game, client, raw_root)
                state = collector.run(work_id=args.work_id, parents=args.parent_comment_id,
                                      discover_only=args.discover_only, resume=args.resume, metadata=metadata)
                print(f"{platform}/{game}: {state['status']}；断点：{collector.manifest}", flush=True)
                if state["status"] == "stalled":
                    return 2
            finally:
                client.session.close()
        return 0
    except KeyboardInterrupt:
        print("采集已中断；请用相同目录恢复。", flush=True)
        return 130
    except Exception as error:
        # 第三方签名扩展或 Requests 的异常文本可能带令牌，只输出类型或我们自己的固定错误。
        reason = str(error) if isinstance(error, CollectionError) else type(error).__name__
        print(f"采集停止：{reason.rstrip('。')}。检查配置/会话及断点，未将失败响应标为成功。", flush=True)
        return 1
