"""
用途：读取、检查情感标注数据，统计标签分布并排除待审核样本。
输入：data/annotated/train_labeled.csv。
输出：控制台中的检查结果和标签统计，以及内存中可用于训练的数据表。
不修改原始 CSV，不执行模型训练。
"""
import pandas as pd

from hoyo_sense.config import load_config

def load_labeled_data(split):
    """读取指定集合，检查数据并返回排除 -1 后的数据表。"""
    if split not in {"train", "dev", "test"}:
        raise ValueError("split 必须是 train、dev 或 test")
    # 使用项目已有配置，找到标注数据目录
    config = load_config()
    annotated_dir = config.paths["annotated_dir"]

    # 拼接训练集文件路径
    train_path = annotated_dir / f"{split}_labeled.csv"
    # 读取CSV ,明确要求帖子ID按字符串保存

    train_df = pd.read_csv(
        train_path,
        encoding='utf-8-sig',
        dtype={'post_id':'string'},
        keep_default_na=False,

    )
    # 1. 检查训练需要的三列是否存在。
    required_columns = {"post_id", "comment", "label"}
    missing_columns = required_columns - set(train_df.columns)

    if missing_columns:
        raise ValueError(f"缺少必要列：{sorted(missing_columns)}")

    # 2. 检查帖子 ID 和评论是否为空，包括只有空格的情况。
    for column in ["post_id", "comment"]:
        values = train_df[column].astype("string")

        empty_mask = (
            values.isna()
            | values.str.strip().eq("").fillna(False)
        )
        empty_count = int(empty_mask.sum())

        print(f"{column} 空值数量：{empty_count}")

        if empty_count > 0:
            raise ValueError(f"{column} 中存在 {empty_count} 条空值")

    # 3. 将标签转换为数字，不能转换的内容会变成缺失值。
    labels = pd.to_numeric(train_df["label"], errors="coerce")

    # 只有 -1、0、1、2 是允许的标签。
    invalid_mask = ~labels.isin([-1, 0, 1, 2])

    if invalid_mask.any():
        invalid_values = train_df.loc[invalid_mask, "label"].head(5).tolist()
        raise ValueError(f"发现非法标签，示例：{invalid_values}")

    # 已确认标签合法，统一转换为整数。
    train_df["label"] = labels.astype("int64")

    print("数据检查通过。")

    print(f"读取文件：",train_path)
    print("数据形状：",train_df.shape)
    print("列名：",train_df.columns.tolist())

    print(f"\n前5条数据:")
    print(train_df.head())

    print("\n各列的数据类型")
    print(train_df.dtypes)

    # 统计每种标签对应的评论数量，并按标签编号排序。
    print("\n过滤前的标签数量：")
    print(train_df["label"].value_counts().sort_index())

    # True 表示这一条评论可以参加三分类训练。
    usable_mask = train_df["label"] != -1

    # 筛选可用行，并复制成独立的数据表。
    usable_df = train_df.loc[usable_mask].copy()

    # 筛选后重新生成连续行号：0、1、2……
    usable_df = usable_df.reset_index(drop=True)

    print("\n过滤前评论数：", len(train_df))
    print("待审核评论数：", int((~usable_mask).sum()))
    print("过滤后评论数：", len(usable_df))

    print("\n过滤后的标签数量：")
    print(usable_df["label"].value_counts().sort_index())
    return usable_df
def main():
    train_df = load_labeled_data("train")
    dev_df = load_labeled_data("dev")

    print("\n供模型使用的数据：")
    print("train 评论数：", len(train_df))
    print("dev 评论数：", len(dev_df))

    # 同一帖子不能同时出现在训练集和验证集中。
    overlapping_posts = set(train_df["post_id"]) & set(dev_df["post_id"])
    if overlapping_posts:
        raise ValueError("train 和 dev 存在重复帖子")

    print("帖子划分检查通过。")


if __name__ == "__main__":
    main()
