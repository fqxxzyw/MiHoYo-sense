"""按帖子 ID 将两列清洗评论分为 train/dev/test，默认 8:1:1、seed=42。"""

import argparse
import csv
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hoyo_sense.config import load_config, project_path
from hoyo_sense.modeling.splitting import split_comments


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--ratios", type=float, nargs=3, default=(8, 1, 1), metavar=("TRAIN", "DEV", "TEST"))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--overwrite", action="store_true", help="允许修改已有划分；开始标注后不要使用")
    args = parser.parse_args()
    config = load_config(args.config)
    source = project_path(args.input) if args.input else config.paths["processed_dir"] / "miyoushe/comments_clean.csv"
    output = project_path(args.output_dir) if args.output_dir else config.paths["splits_dir"]
    reports = project_path(args.report_dir) if args.report_dir else config.paths["outputs_dir"] / "splitting/miyoushe"
    try:
        report = split_comments(source, output, reports, ratios=args.ratios, seed=args.seed, overwrite=args.overwrite)
    except (OSError, ValueError, csv.Error) as error:
        parser.exit(1, f"划分失败：{error}\n")
    print(f"{'复用一致划分' if report['reused'] else '划分完成'}：{output}")
    for name, stats in report["splits"].items():
        print(f"{name}: {stats['posts']} 帖，{stats['rows']} 条评论；评论占比 {stats['row_fraction']:.2%}")
    print(f"帖子交叉数：0；跨集合相同文本组：{report['cross_split_duplicate_text_groups']}，复核清单：{reports}")


if __name__ == "__main__":
    main()
