"""加载已保存的随机森林模型，预测新评论的情感。"""

import argparse

from hoyo_sense.config import load_config
from hoyo_sense.modeling.common.evaluation import LABEL_NAMES
from hoyo_sense.modeling.common.model_io import load_pipeline


def predict_texts(model_path, texts, *, model=None):
    """输入评论列表，返回每条评论的预测结果。"""
    texts = list(texts)

    if not texts or any(
        not isinstance(text, str) or not text.strip()
        for text in texts
    ):
        raise ValueError("请输入至少一条非空评论")

    if model is None:
        model = load_pipeline(model_path)

    predictions = model.predict(texts)

    return [
        {
            "comment": text,
            "label": int(label),
            "sentiment": LABEL_NAMES[int(label)],
        }
        for text, label in zip(texts, predictions)
    ]


def main():
    default_model = (
        load_config().paths["outputs_dir"]
        / "models"
        / "tfidf_rf"
        / "20261023_085825_426077_UTC"
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=str(default_model))
    parser.add_argument(
        "--text",
        nargs="+",
        default=[
            "这个活动很好玩，下次还想参加",
            "这个版本太让人失望了",
            "今天更新了新版本",
        ],
    )
    args = parser.parse_args()

    print(f"使用模型：{args.model}")

    for item in predict_texts(args.model, args.text):
        print(
            f"{item['comment']} -> "
            f"{item['sentiment']}（{item['label']}）"
        )


if __name__ == "__main__":
    main()