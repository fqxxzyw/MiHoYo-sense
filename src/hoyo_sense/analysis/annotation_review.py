"""
用途：分别导出 train、dev、test 中标签为 -1 的待审核评论。
输入：data/annotated/ 下的三个 *_labeled.csv 文件。
输出：outputs/annotation_review/ 下的三个 *_pending.csv 文件。
保留原始标注文件，不自动修改标签。

导出时增加 source_row 列，表示该记录在原数据中的位置：
第一条数据记为 1，不包含表头。
"""
import pandas as pd

from hoyo_sense.config import load_config

def main():
    config = load_config()
    input_dir = config.paths["annotated_dir"]
    output_dir = config.paths["outputs_dir"] / "annotation_review"
    output_dir.mkdir(parents=True, exist_ok=True)

    total = 0

    for split in ["train", "dev", "test"]:
        input_path = input_dir / f"{split}_labeled.csv"

        df = pd.read_csv(
            input_path,
            encoding="utf-8-sig",
            dtype={"post_id": "string"},
            keep_default_na=False,
        )

        # 标签转成数字后，筛选出 -1。
        labels = pd.to_numeric(df["label"], errors="raise")
        pending = df.loc[labels == -1].copy()

        # 保留原数据行位置，方便以后准确对应回原文件。
        pending.insert(0, "source_row", pending.index + 1)

        output_path = output_dir / f"{split}_pending.csv"
        pending.to_csv(
            output_path,
            index=False,
            encoding="utf-8-sig",
        )

        total += len(pending)
        print(f"{split}：导出 {len(pending)} 条")
        print(f"保存位置：{output_path}")

        print("前 10 条待审核评论：")
        for text in pending["comment"].head(10):
            print(repr(text))

        print()

    print(f"总计导出：{total} 条待审核评论")


if __name__ == "__main__":
    main()