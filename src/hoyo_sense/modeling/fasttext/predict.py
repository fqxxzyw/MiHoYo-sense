"""加载 fastText 原始、蒸馏或量化模型，对新评论分类。"""

import argparse

from hoyo_sense.modeling.common.experiments import (
    artifact_path, prediction_rows, resolve_run, validate_texts,
)
from hoyo_sense.modeling.fasttext.prepare import preprocess_text


def load_model(run):
    import fasttext

    run = resolve_run(run, "fasttext")
    return fasttext.load_model(str(artifact_path(run)))


def predict_labels(model, texts):
    processed = [preprocess_text(text) for text in validate_texts(texts)]
    labels, _ = model.predict(processed, k=1)
    result = []
    for row in labels:
        label = row[0]
        if label not in ("__label__0", "__label__1", "__label__2"):
            raise ValueError(f"模型返回了未知标签：{label}")
        result.append(int(label.removeprefix("__label__")))
    return result


def predict_texts(model_path, texts, *, model=None):
    texts = validate_texts(texts)
    model = load_model(model_path) if model is None else model
    return prediction_rows(texts, predict_labels(model, texts))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", help="运行目录；默认最新已保存版本")
    parser.add_argument("--text", nargs="+", default=["这个活动很好玩", "奖励太少了", "今天更新了"])
    args = parser.parse_args()
    run = resolve_run(args.model, "fasttext")
    print(f"预测模型：{run}")
    for item in predict_texts(run, args.text):
        print(f"{item['comment']} -> {item['sentiment']}（{item['label']}）")


if __name__ == "__main__":
    main()
