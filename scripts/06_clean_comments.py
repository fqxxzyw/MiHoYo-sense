"""清洗两列评论数据，生成成品、异常审计与清洗前后 EDA。"""

import argparse
import csv
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hoyo_sense.config import load_config, project_path
from hoyo_sense.preprocessing.cleaning import clean_comments


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--dedup", choices=["post", "none"], default="post", help="post同帖文本去重；none保留重复频次")
    parser.add_argument("--short-length", type=int, default=2, help="短于此长度仅标记，默认2")
    parser.add_argument("--long-length", type=int, default=2000, help="长于此长度仅标记，默认2000")
    args = parser.parse_args()
    config = load_config(args.config)
    input_path = project_path(args.input) if args.input else config.paths["processed_dir"] / "miyoushe/comments.csv"
    output_path = project_path(args.output) if args.output else config.paths["processed_dir"] / "miyoushe/comments_clean.csv"
    report_dir = project_path(args.report_dir) if args.report_dir else config.paths["outputs_dir"] / "cleaning/miyoushe"
    try:
        report = clean_comments(input_path, output_path, report_dir, dedup=args.dedup,
                                short_length=args.short_length, long_length=args.long_length)
    except (OSError, ValueError, csv.Error) as error:
        parser.exit(1, f"清洗失败：{error}\n")
    counts = report["counts"]
    print(f"{'复用一致结果' if report['reused'] else '清洗完成'}：{output_path}")
    print(f"输入 {counts['input_rows']}；去空 {counts['empty_removed']}；同帖去重 {counts['duplicate_rows_removed']}；"
          f"无效记录 {counts['malformed_rows_removed'] + counts['invalid_id_removed']}；保留 {counts['output_rows']}。")
    print(f"仅保留 post_id、comment 两列；清洗报告与异常记录：{report_dir}")


if __name__ == "__main__":
    main()
