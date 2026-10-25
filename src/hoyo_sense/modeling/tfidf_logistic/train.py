"""训练第一个情感基线，并在 dev 上评估。"""
import argparse
from time import perf_counter

from hoyo_sense.modeling.common.dataset import load_labeled_data
from hoyo_sense.modeling.common.evaluation import print_evaluation
from hoyo_sense.modeling.common.model_io import create_run_dir, save_pipeline
from hoyo_sense.storage import save_json
from hoyo_sense.modeling.tfidf_logistic.model import (
    build_logistic_baseline,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    # 1. 读取已经检查、过滤的数据。
    train_df = load_labeled_data("train")
    dev_df = load_labeled_data("dev")

    overlapping_posts = set(train_df["post_id"]) & set(dev_df["post_id"])
    if overlapping_posts:
        raise ValueError("train 和 dev 存在重复帖子")

    # 2. 创建 TF-IDF + 逻辑回归模型。
    model = build_logistic_baseline()

    # 3. 只使用 train 学习文本特征和分类规则。
    print("\n开始训练：字符 TF-IDF + LogisticRegression", flush=True)
    started = perf_counter()

    model.fit(
        train_df["comment"],
        train_df["label"],
    )

    elapsed = perf_counter() - started
    print(f"训练完成，耗时 {elapsed:.2f} 秒", flush=True)

    # 4. 使用已经训练好的模型预测 dev。
    predictions = model.predict(dev_df["comment"])
    y_true = dev_df["label"]

    # 5. 对照真实标签，计算验证集指标。
    metrics = print_evaluation(y_true, predictions, split="dev")
    # 6. 每次运行创建独立目录，保存模型和验证结果。
    output_dir = create_run_dir("tfidf_logistic")

    # 保存整个 Pipeline：包含 TF-IDF 词表、IDF 和分类器。
    save_pipeline(model, output_dir)

    results = {
        "model": "tfidf_logistic",
        "train_rows": len(train_df),
        "dev_rows": len(dev_df),
        "training_seconds": elapsed,
        "dev_macro_f1": metrics["macro_f1"],
        "dev_accuracy": metrics["accuracy"],
        "classification_report": metrics["classification_report"],
        "confusion_matrix": metrics["confusion_matrix"],
    }

    save_json(results, output_dir / "dev_metrics.json")

    print(f"\n模型和结果已保存到：{output_dir}")


if __name__ == "__main__":
    main()
