"""仅导出 post_id、comment 两列到 processed；运行记录保存在 outputs。"""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hoyo_sense.analysis.prepare import prepare_analysis
from hoyo_sense.config import load_config, project_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--snapshot", type=Path, help="默认选择最新的提取成功快照")
    parser.add_argument("--output-dir", type=Path, help="默认 data/processed/miyoushe；相同结果自动复用")
    parser.add_argument("--overwrite", action="store_true", help="已有结果不同时允许更新 comments.csv")
    args = parser.parse_args()
    config = load_config(args.config)
    if args.snapshot:
        snapshot = project_path(args.snapshot)
    else:
        snapshots = sorted((config.paths["processed_dir"] / "miyoushe").glob("*/report.json"), reverse=True)
        snapshot = next((p.parent for p in snapshots
                         if json.loads(p.read_text(encoding="utf-8")).get("status") == "completed"), None)
        if snapshot is None:
            parser.error("没有提取成功的米游社快照。")
    output = project_path(args.output_dir) if args.output_dir else config.paths["processed_dir"] / "miyoushe"
    report_dir = config.paths["outputs_dir"] / "eda" / "miyoushe" / snapshot.name
    result = prepare_analysis(snapshot, output, report_dir, overwrite=args.overwrite)
    print("复用已有结果（内容一致）。" if result["reused"] else "已生成两列评论表。")
    print(f"两列评论表：{result['output_file']}")
    print(f"列：post_id、comment；评论 {result['comments']} 条；运行记录：{report_dir}")


if __name__ == "__main__":
    main()
