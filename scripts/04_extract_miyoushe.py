"""从本地米游社 JSON 提取分析表；不发网络请求、不启动采集器。"""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hoyo_sense.config import load_config, project_path
from hoyo_sense.games import MIYOUSHE_GAME_IDS
from hoyo_sense.preprocessing.miyoushe import extract_dataset, export_flat_comments, snapshot_name


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--raw-root", type=Path, help="其下直接是 genshin/starrail/zzz")
    parser.add_argument("--output-dir", type=Path, help="必须为尚不存在的新目录")
    parser.add_argument("--game", choices=[*MIYOUSHE_GAME_IDS, "all"], default="all")
    parser.add_argument("--flatten-snapshot", type=Path, help="从已有成功快照生成每条评论独占一行的 comments_flat.csv")
    args = parser.parse_args()
    if args.flatten_snapshot:
        if args.config or args.raw_root or args.output_dir or args.game != "all":
            parser.error("--flatten-snapshot 单独使用，不接受其他提取选项。")
        snapshot = project_path(args.flatten_snapshot)
        result = export_flat_comments(snapshot)
        print(f"单行评论表：{snapshot / result['file']}")
        print(f"顶层 {result['top']} 条，楼中楼 {result['sub']} 条；共 {result['records']} 条，含表头 {result['physical_lines']} 行。")
        return
    config = load_config(args.config)
    raw_root = project_path(args.raw_root) if args.raw_root else config.miyoushe_root
    output_dir = (project_path(args.output_dir) if args.output_dir else
                  config.paths["processed_dir"] / "miyoushe" / snapshot_name())
    games = list(MIYOUSHE_GAME_IDS) if args.game == "all" else [args.game]
    report = extract_dataset(raw_root, output_dir, games)
    print(f"提取状态：{report['status']}；结果目录：{output_dir}")
    print(f"帖子 {report['totals']['posts']}，不同评论 ID {report['totals']['comments']}；质量统计见 report.json")


if __name__ == "__main__":
    main()
