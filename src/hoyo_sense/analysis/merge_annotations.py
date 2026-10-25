"""合回表情标注结果，过滤纯数字和纯符号评论，保留原件备份与逐行记录。"""

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import tempfile
import unicodedata

import pandas as pd

from hoyo_sense.config import load_config
from hoyo_sense.storage import digest, read_json, save_json
from .relabel_emoticons import is_emoticon_only


SPLITS = ("train", "dev", "test")


def removal_reason(text, *, drop_emoji=False):
    """忽略空白判断；保留中文数字词、数字与符号混合的内容及米游社表情。

    默认保守保留 Unicode 的其他符号类 So（包含 emoji 和图形符号）。
    --drop-emoji 才会把这些符号也纳入纯符号过滤。
    """
    compact = "".join(text.split())
    if not compact or is_emoticon_only(text):
        return ""
    categories = [unicodedata.category(char) for char in compact]
    if all(category.startswith("N") for category in categories):
        return "numeric_only"
    # 变体选择符、组合附加符和连接符本身不提供文字内容。
    symbols = [category for category in categories if category[0] != "M" and category != "Cf"]
    if symbols and all(category[0] in "PS" for category in symbols):
        if not drop_emoji and "So" in symbols:
            return ""
        return "symbols_only"
    return ""


def read_annotations(path):
    frame = pd.read_csv(path, encoding="utf-8-sig", keep_default_na=False,
                        dtype={"post_id": "string", "comment": "string"})
    if list(frame.columns) != ["post_id", "comment", "label"]:
        raise ValueError(f"标注表必须是 post_id,comment,label 三列：{path}")
    labels = pd.to_numeric(frame["label"], errors="raise")
    if not labels.isin([-1, 0, 1, 2]).all():
        raise ValueError(f"标注表含非法标签：{path}")
    frame["label"] = labels.astype("int64")
    return frame


def prepare_split(original, revised, *, drop_emoji=False):
    """先按完整行顺序验证，避免同帖多评论、重复评论造成错误合并。"""
    if not original[["post_id", "comment"]].equals(revised[["post_id", "comment"]]):
        raise ValueError("原表与 v2 的帖子、评论或行顺序不一致，拒绝合并")
    changed_mask = original["label"].ne(revised["label"])
    allowed = (original["label"].eq(-1) & revised["label"].eq(2)
               & original["comment"].map(is_emoticon_only))
    if (changed_mask & ~allowed).any():
        raise ValueError("v2 包含纯表情 -1→2 以外的修改，拒绝覆盖现有标签")

    changes = original.loc[changed_mask].copy()
    changes.insert(0, "source_row", changes.index + 1)
    changes.rename(columns={"label": "old_label"}, inplace=True)
    changes["new_label"] = revised.loc[changed_mask, "label"]
    reasons = revised["comment"].map(lambda text: removal_reason(text, drop_emoji=drop_emoji))
    removed = revised.loc[reasons.ne("")].copy()
    removed.insert(0, "source_row", removed.index + 1)
    removed["reason"] = reasons.loc[removed.index]
    result = revised.loc[reasons.eq("")].reset_index(drop=True).copy()
    summary = {
        "before": len(original), "merged_emoticons": int(changed_mask.sum()),
        "removed_numeric": int(reasons.eq("numeric_only").sum()),
        "removed_symbols": int(reasons.eq("symbols_only").sum()),
        "after": len(result), "pending": int(result["label"].eq(-1).sum()),
    }
    return result, changes, removed, summary


def replace_csv(frame, path):
    """先写同目录临时文件，写入失败时不截断原表。"""
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".csv.tmp", delete=False) as file:
        temporary = Path(file.name)
    try:
        frame.to_csv(temporary, index=False, encoding="utf-8-sig")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def merge_annotations(annotated_dir, review_dir, *, drop_emoji=False):
    annotated_dir, review_dir = Path(annotated_dir), Path(review_dir)
    originals = {split: annotated_dir / f"{split}_labeled.csv" for split in SPLITS}
    revisions = {split: annotated_dir / f"{split}_labeled_v2.csv" for split in SPLITS}
    original_hashes = {split: digest(path) for split, path in originals.items()}
    revised_hashes = {split: digest(path) for split, path in revisions.items()}
    latest_path = review_dir / "annotation_merge_latest.json"
    rules = {"version": 1, "drop_emoji": drop_emoji}
    if latest_path.exists():
        latest = read_json(latest_path)
        if (latest["rules"] == rules and latest["output_hashes"] == original_hashes
                and latest["v2_hashes"] == revised_hashes):
            return latest

    # 所有集合校验完毕以后才创建备份和写回，坏输入不会留下半套新数据。
    prepared = {split: prepare_split(read_annotations(originals[split]),
                                    read_annotations(revisions[split]), drop_emoji=drop_emoji)
                for split in SPLITS}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f_UTC")
    backup_dir = annotated_dir / "backups" / stamp
    run_dir = review_dir / f"merge_{stamp}"
    backup_dir.mkdir(parents=True)
    run_dir.mkdir(parents=True)
    for split, path in originals.items():
        shutil.copy2(path, backup_dir / path.name)
        if digest(backup_dir / path.name) != original_hashes[split]:
            raise ValueError("备份校验失败，未写回标注表")
    committed = []
    try:
        for split, (result, changes, removed, _) in prepared.items():
            changes.to_csv(run_dir / f"{split}_label_changes.csv", index=False, encoding="utf-8-sig")
            removed.to_csv(run_dir / f"{split}_removed.csv", index=False, encoding="utf-8-sig")
            # 防止用户在读取和写回之间另存了新标注。
            if digest(originals[split]) != original_hashes[split]:
                raise ValueError(f"{split} 原标注表在运行期间发生变化，停止写回")
            replace_csv(result, originals[split])
            committed.append(split)
    except Exception:
        for split in committed:
            shutil.copy2(backup_dir / originals[split].name, originals[split])
        raise
    report = {
        "rules": rules, "backup_dir": str(backup_dir), "run_dir": str(run_dir),
        "source_hashes": original_hashes, "v2_hashes": revised_hashes,
        "output_hashes": {split: digest(path) for split, path in originals.items()},
        "splits": {split: prepared[split][3] for split in SPLITS},
    }
    save_json(report, run_dir / "summary.json")
    save_json(report, latest_path)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--drop-emoji", action="store_true", help="纯 Unicode 表情和图形符号也删除")
    args = parser.parse_args()
    config = load_config()
    report = merge_annotations(config.paths["annotated_dir"],
                               config.paths["outputs_dir"] / "annotation_review", drop_emoji=args.drop_emoji)
    for split, counts in report["splits"].items():
        print(f"{split}: {counts}")
    print(f"原标注备份：{report['backup_dir']}")
    print(f"逐行修改、删除记录：{report['run_dir']}")
    # 更新当前待审核表，source_row 对应过滤后的当前原标注表。
    from .annotation_review import main as export_pending
    export_pending()


if __name__ == "__main__":
    main()
