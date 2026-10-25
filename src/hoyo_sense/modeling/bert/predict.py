"""批量预测原始、蒸馏或量化 BERT；使用保存的 tokenizer 和截断长度。"""

import argparse

from hoyo_sense.modeling.bert.model import load_model
from hoyo_sense.modeling.common.experiments import prediction_rows, resolve_run, validate_texts


def predict_labels(bundle, texts, *, batch_size=32):
    import torch

    texts = validate_texts(texts)
    if batch_size < 1:
        raise ValueError("batch_size 必须为正整数")
    bundle.model.eval()
    labels = []
    with torch.inference_mode():
        for start in range(0, len(texts), batch_size):
            inputs = bundle.tokenizer(texts[start:start + batch_size], padding=True,
                                      truncation=True, max_length=bundle.max_length, return_tensors="pt")
            inputs = {key: value.to(bundle.device) for key, value in inputs.items()}
            labels.extend(bundle.model(**inputs).logits.argmax(dim=-1).cpu().tolist())
    return labels


def predict_texts(model_path, texts, *, model=None):
    texts = validate_texts(texts)
    bundle = load_model(model_path) if model is None else model
    return prediction_rows(texts, predict_labels(bundle, texts))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model")
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="cpu")
    parser.add_argument("--text", nargs="+", default=["这个活动很好玩", "奖励太少了", "今天更新了"])
    args = parser.parse_args()
    run = resolve_run(args.model, "bert")
    print(f"预测模型：{run}")
    bundle = load_model(run, device=args.device)
    for item in predict_texts(run, args.text, model=bundle):
        print(f"{item['comment']} -> {item['sentiment']}（{item['label']}）")


if __name__ == "__main__":
    main()
