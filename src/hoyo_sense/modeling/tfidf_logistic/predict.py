"""加载已保存的逻辑回归模型，对输入的新评论预测情感。"""

import argparse

from hoyo_sense.modeling.common.evaluation import LABEL_NAMES
from hoyo_sense.modeling.common.model_io import load_pipeline


def predict_texts(model_path, texts, *, model=None):
    """只预测，不读取 train/dev/test，也不训练模型。"""
    texts = list(texts)
    if not texts or any(not isinstance(text, str) or not text.strip() for text in texts):
        raise ValueError("请输入至少一条非空评论")
    if model is None:
        model = load_pipeline(model_path)
    predictions = model.predict(texts)
    return [
        {"comment": text, "label": int(label), "sentiment": LABEL_NAMES[int(label)]}
        for text, label in zip(texts, predictions)
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="模型文件或运行目录")
    parser.add_argument("--text", nargs="+", required=True, help="一条或多条评论，含空格时加引号")
    args = parser.parse_args()
    for item in predict_texts(args.model, args.text):
        print(f"{item['comment']} -> {item['sentiment']}（{item['label']}）")


if __name__ == "__main__":
    main()
