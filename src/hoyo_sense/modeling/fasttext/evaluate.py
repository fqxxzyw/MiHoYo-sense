"""加载 fastText 已保存模型，独立评估 dev 或显式指定的 test。"""

import argparse

from hoyo_sense.modeling.common.dataset import load_labeled_data
from hoyo_sense.modeling.common.evaluation import print_evaluation
from hoyo_sense.modeling.common.experiments import resolve_run
from hoyo_sense.modeling.fasttext.predict import load_model, predict_labels
from hoyo_sense.storage import save_json


def evaluate(model_path=None, split="dev", *, save=False):
    if split not in {"dev", "test"}:
        raise ValueError("评估集合必须是 dev 或 test")
    run = resolve_run(model_path, "fasttext")
    print(f"评估模型：{run}")
    frame = load_labeled_data(split)
    metrics = print_evaluation(frame["label"], predict_labels(load_model(run), frame["comment"]), split=split)
    report = dict(model="fasttext", model_path=str(run), split=split, rows=len(frame), **metrics)
    if save:
        save_json(report, run / f"{split}_evaluation.json")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model")
    parser.add_argument("--split", choices=["dev", "test"], default="dev")
    parser.add_argument("--save", action="store_true", help="另存评估报告，不覆盖训练指标")
    args = parser.parse_args()
    evaluate(args.model, args.split, save=args.save)


if __name__ == "__main__":
    main()
