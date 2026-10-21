"""从成功提取快照生成仅含帖子 ID 与评论的分析输入。"""

import csv
import filecmp
import json
from pathlib import Path

from ..storage import save_json, utc_now


COMMENT_FIELDS = ("post_id", "comment")


def prepare_analysis(snapshot_dir, output_dir, report_dir, *, overwrite=False):
    snapshot_dir, output_dir, report_dir = (
        Path(path).resolve() for path in (snapshot_dir, output_dir, report_dir)
    )
    source_report = json.loads((snapshot_dir / "report.json").read_text(encoding="utf-8"))
    if source_report.get("status") != "completed":
        raise ValueError("只能使用提取完成的成功快照。")
    if output_dir == snapshot_dir or snapshot_dir in output_dir.parents:
        raise ValueError("分析表不能写入原提取快照。")
    output = output_dir / "comments.csv"
    output_dir.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    count, empty = 0, 0
    with (snapshot_dir / "comments.jsonl").open(encoding="utf-8") as source, temporary.open(
            "w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=COMMENT_FIELDS)
        writer.writeheader()
        for line in source:
            row = json.loads(line)
            comment = " ".join(row["text"].split())
            writer.writerow({"post_id": row["post_id"], "comment": comment})
            count += 1
            empty += not comment
    if count != source_report["totals"]["comments"]:
        raise ValueError("评论数量与成功快照报告不一致。")
    reused = output.exists() and filecmp.cmp(temporary, output, shallow=False)
    if reused:
        temporary.unlink()
    elif output.exists() and not overwrite:
        temporary.unlink()
        raise FileExistsError(f"已有分析表与本次结果不同；如需更新请加 --overwrite：{output}")
    else:
        temporary.replace(output)
    result = dict(status="completed", source_snapshot=str(snapshot_dir), output_file=str(output),
                  columns=list(COMMENT_FIELDS), comments=count, empty_comments=empty, reused=reused,
                  finished_at=utc_now())
    report_dir.mkdir(parents=True, exist_ok=True)
    save_json(result, report_dir / "comments_preparation.json")
    return result
