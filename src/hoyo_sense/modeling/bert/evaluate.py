"""独立评估保存的原始、蒸馏或 INT8 BERT；test 需显式指定。"""

import argparse

from hoyo_sense.modeling.bert.model import load_model
from hoyo_sense.modeling.bert.predict import predict_labels
from hoyo_sense.modeling.common.dataset import load_labeled_data
from hoyo_sense.modeling.common.evaluation import print_evaluation
from hoyo_sense.modeling.common.experiments import resolve_run
from hoyo_sense.storage import save_json


def evaluate(model_path=None, split="dev", *, device="cpu", batch_size=32, save=False):
    if split not in {"dev", "test"}:
        raise ValueError("评估集合必须是 dev 或 test")
    run = resolve_run(model_path, "bert")
    print(f"评估模型：{run}")
    frame = load_labeled_data(split)
    bundle = load_model(run, device=device)
    labels = predict_labels(bundle, frame["comment"], batch_size=batch_size)
    metrics = print_evaluation(frame["label"], labels, split=split)
    report = dict(model="bert", model_path=str(run), split=split, rows=len(frame), **metrics)
    if save:
        save_json(report, run / f"{split}_evaluation.json")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model")
    parser.add_argument("--split", choices=["dev", "test"], default="dev")
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="cpu")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args()
    evaluate(args.model, args.split, device=args.device, batch_size=args.batch_size, save=args.save)


if __name__ == "__main__":
    main()
