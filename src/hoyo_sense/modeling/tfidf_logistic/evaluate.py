"""加载已保存的逻辑回归模型，独立评估 dev 或明确指定的 test。"""

import argparse

from hoyo_sense.config import project_path
from hoyo_sense.modeling.common.dataset import load_labeled_data
from hoyo_sense.modeling.common.evaluation import print_evaluation
from hoyo_sense.modeling.common.model_io import load_pipeline
from hoyo_sense.storage import save_json


def evaluate(model_path, split="dev"):
    if split not in {"dev", "test"}:
        raise ValueError("评估集合必须是 dev 或 test")
    model = load_pipeline(model_path)
    frame = load_labeled_data(split)
    predictions = model.predict(frame["comment"])
    metrics = print_evaluation(frame["label"], predictions, split=split)
    return {"model": "tfidf_logistic", "split": split, "rows": len(frame), **metrics}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="模型文件或运行目录")
    parser.add_argument("--split", choices=["dev", "test"], default="dev")
    parser.add_argument("--output", help="可选，另存评估 JSON 的路径")
    args = parser.parse_args()
    report = evaluate(args.model, args.split)
    if args.output:
        output = project_path(args.output)
        model_file = project_path(args.model)
        if model_file.is_dir():
            model_file = model_file / "pipeline.joblib"
        if output == model_file:
            raise ValueError("评估报告不能覆盖模型文件")
        save_json(report, output)
        print(f"评估报告已保存到：{output}")


if __name__ == "__main__":
    main()
