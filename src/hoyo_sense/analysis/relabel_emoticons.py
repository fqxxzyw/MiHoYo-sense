"""
用途：将待审核评论中完全由米游社表情标记组成的内容改为中性。
规则：只修改 label=-1，且全文由一个或多个 _(...) 组成的评论。
      表情之间允许空白；含其他文字或标点的评论不修改。

输入：
    data/annotated/train_labeled.csv
    data/annotated/dev_labeled.csv
    data/annotated/test_labeled.csv

输出：
    data/annotated/train_labeled_v2.csv
    data/annotated/dev_labeled_v2.csv
    data/annotated/test_labeled_v2.csv
    outputs/annotation_review/ 下的修改记录、剩余待审核表和统计表。

不覆盖原标注文件，不改变评论文本、行顺序或数据集划分。
"""

import re

import pandas as pd

from hoyo_sense.config import load_config


# 一个表情：_(非空的表情名称)
# 多个表情可以连续出现，也可以用空格或换行分隔。
EMOTICON_ONLY = re.compile(r"\s*(?:_\([^()\r\n]+\)\s*)+")


def is_emoticon_only(text):
    """判断整条评论是否完全由一个或多个表情标记组成。"""
    return EMOTICON_ONLY.fullmatch(text) is not None


def main():
    config = load_config()
    annotated_dir = config.paths["annotated_dir"]
    review_dir = config.paths["outputs_dir"] / "annotation_review"
    review_dir.mkdir(parents=True, exist_ok=True)

    summaries = []

    for split in ["train", "dev", "test"]:
        input_path = annotated_dir / f"{split}_labeled.csv"

        df = pd.read_csv(
            input_path,
            encoding="utf-8-sig",
            dtype={"post_id": "string", "comment": "string"},
            keep_default_na=False,
        )

        required = {"post_id", "comment", "label"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"{input_path.name} 缺少列：{sorted(missing)}")

        labels = pd.to_numeric(df["label"], errors="raise")
        if not labels.isin([-1, 0, 1, 2]).all():
            raise ValueError(f"{input_path.name} 存在非法标签")

        df["label"] = labels.astype("int64")

        # 条件一：原标签为 -1。
        pending_mask = df["label"].eq(-1)

        # 条件二：整条评论只有表情。
        emoticon_mask = df["comment"].map(is_emoticon_only)

        # 两个条件同时满足，才修改。
        change_mask = pending_mask & emoticon_mask

        # 先保留修改记录，方便核对。
        changed = df.loc[change_mask].copy()
        changed.insert(0, "source_row", changed.index + 1)
        changed = changed.rename(columns={"label": "old_label"})
        changed["new_label"] = 2
        changed["reason"] = "待审核评论全文仅含 _(...) 表情"

        # 在副本中修改标签，评论正文保持原样。
        updated = df.copy()
        updated.loc[change_mask, "label"] = 2

        # 导出改完以后仍然为 -1 的评论。
        remaining = updated.loc[updated["label"].eq(-1)].copy()
        remaining.insert(0, "source_row", remaining.index + 1)

        before_count = int(pending_mask.sum())
        changed_count = int(change_mask.sum())
        remaining_count = len(remaining)

        # 确认数量守恒。
        assert before_count == changed_count + remaining_count
        assert len(updated) == len(df)

        updated.to_csv(
            annotated_dir / f"{split}_labeled_v2.csv",
            index=False,
            encoding="utf-8-sig",
        )
        changed.to_csv(
            review_dir / f"{split}_emoticons_changed.csv",
            index=False,
            encoding="utf-8-sig",
        )
        remaining.to_csv(
            review_dir / f"{split}_pending_remaining.csv",
            index=False,
            encoding="utf-8-sig",
        )

        summaries.append({
            "split": split,
            "pending_before": before_count,
            "changed_to_neutral": changed_count,
            "pending_remaining": remaining_count,
        })

        print(
            f"{split}：原待审核 {before_count} 条，"
            f"改为中性 {changed_count} 条，"
            f"剩余待审核 {remaining_count} 条"
        )

        print("剩余待审核评论前 10 条：")
        for text in remaining["comment"].head(10):
            print(repr(text))
        print()

    summary = pd.DataFrame(summaries)
    summary.to_csv(
        review_dir / "emoticon_relabel_summary.csv",
        index=False,
        encoding="utf-8-sig",
    )

    print("三个集合合计：")
    print("原待审核：", int(summary["pending_before"].sum()))
    print("改为中性：", int(summary["changed_to_neutral"].sum()))
    print("剩余待审核：", int(summary["pending_remaining"].sum()))


if __name__ == "__main__":
    main()