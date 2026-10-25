"""训练字符TF-IDF + 随机森林 ，并在 dev 上评估"""

import argparse
from time import perf_counter

from hoyo_sense.modeling import tfidf_rf
from hoyo_sense.modeling.common.dataset import load_labeled_data
from hoyo_sense.modeling.common.evaluation import print_evaluation
from hoyo_sense.modeling.common.model_io import (
    create_run_dir,
    save_pipeline,
)
from hoyo_sense.modeling.tfidf_rf.model import build_rf_baseline
from hoyo_sense.storage import save_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()

    # 1. 读取数据，公共函数会检查数据并过滤 label=-1，
    train_df = load_labeled_data("train")
    dev_df = load_labeled_data("dev")

    # 2. 检查帖子是否跨集合重复
    overlapping_posts = set(train_df['post_id']) & set(dev_df['post_id'])
    if overlapping_posts:
        raise ValueError("train 和 dev 存在重复帖子")

    # 3. 创建 RF 模型
    model = build_rf_baseline()

    # 4. 只在 train 上拟合 TF-IDF 和随机森林
    print("\n开始训练：字符 TF-IDF + RandomForest",flush=True)
    started = perf_counter()

    model.fit(
        train_df['comment'],
        train_df['label']
    )

    elapsed = perf_counter() - started
    print(f"训练完成，耗时{elapsed:.2f}s",flush=True)

    # 5. 预测 dev ，使用公共函数计算，打印评估指标
    predictions = model.predict(dev_df['comment'])
    metrics = print_evaluation(
        dev_df['label'],
        predictions,
        split='dev',
    )

    # 6. 创建 RF 自己的运行目录，保存完整的Pipeline
    output_dif= create_run_dir("tfidf_rf")
    save_pipeline(model,output_dif)

    # 7. 保存本次训练和验证结果
    results = {
        "model":"tfidf_rf",
        "train_rows":len(train_df),
        "dev_rows":len(dev_df),
        "training_seconds":elapsed,
        "dev_macro_f1":metrics["macro_f1"],
        "dev_accuracy":metrics["accuracy"],
        "classification_report":metrics["classification_report"],
        "confusion_matrix":metrics["confusion_matrix"],
    }
    save_json(results, output_dif / 'dev_metrics.json')
    print(f"\n模型和结果已保存到：{output_dif}")

if __name__ == '__main__':
    main()









