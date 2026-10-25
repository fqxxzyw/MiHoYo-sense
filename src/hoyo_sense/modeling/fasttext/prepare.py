"""将已标注的 train/dev 转换为 fastText 输入格式。"""

from hoyo_sense.config import load_config
from hoyo_sense.modeling.common.dataset import load_labeled_data


def preprocess_text(text):
    """按字符加空格，去掉原文中的换行及其他空白。"""
    return " ".join(char for char in text if not char.isspace())


def export_split(frame, output):
    """每条评论写成：__label__类别 文本。"""
    with output.open("w", encoding="utf-8", newline="\n") as file:
        for row in frame.itertuples(index=False):
            text = preprocess_text(row.comment)

            if not text:
                raise ValueError(f"转换后评论为空，帖子：{row.post_id}")

            file.write(f"__label__{int(row.label)} {text}\n")

    print(f"已导出 {len(frame)} 条：{output}")


def main():
    train = load_labeled_data("train")
    dev = load_labeled_data("dev")
    test = load_labeled_data("test")

    # 检查同一个帖子是否出现在不同集合中。
    train_ids = set(train["post_id"])
    dev_ids = set(dev["post_id"])
    test_ids = set(test["post_id"])

    if train_ids & dev_ids or train_ids & test_ids or dev_ids & test_ids:
        raise ValueError("train/dev/test 存在重复帖子")

    print("帖子划分检查通过。")

    output_dir = load_config().paths["annotated_dir"] / "fasttext"
    output_dir.mkdir(parents=True, exist_ok=True)

    export_split(train, output_dir / "train.txt")
    export_split(dev, output_dir / "dev.txt")
    export_split(test, output_dir / "test.txt")


if __name__ == "__main__":
    main()