"""训练 fastText，使用 dev 评估，并单独保存本次模型和参数。"""

import argparse
from itertools import zip_longest
from time import perf_counter

from hoyo_sense.config import load_config
from hoyo_sense.modeling.common.dataset import load_labeled_data
from hoyo_sense.modeling.common.evaluation import print_evaluation
from hoyo_sense.modeling.common.experiments import check_disjoint, save_dev_report, write_manifest
from hoyo_sense.modeling.common.model_io import create_run_dir
from hoyo_sense.modeling.fasttext.model import DEFAULT_PARAMETERS, train_fasttext
from hoyo_sense.modeling.fasttext.predict import predict_labels
from hoyo_sense.modeling.fasttext.prepare import preprocess_text
from hoyo_sense.storage import save_json


def verify_training_file(path, frame):
    """防止 CSV 改过后，误用旧 TXT 训练；只验证 train，不读取 test。"""
    expected = (f"__label__{int(row.label)} {preprocess_text(row.comment)}\n"
                for row in frame.itertuples(index=False))
    with path.open(encoding="utf-8") as source:
        for number, (actual, wanted) in enumerate(zip_longest(source, expected), 1):
            if actual != wanted:
                raise ValueError(f"train.txt 第 {number} 行与标注数据不一致，请重新运行 fasttext/prepare.py")


def train(**overrides):
    train_frame, dev = load_labeled_data("train"), load_labeled_data("dev")
    check_disjoint(train_frame, dev)
    path = load_config().paths["annotated_dir"] / "fasttext" / "train.txt"
    verify_training_file(path, train_frame)
    parameters = {**DEFAULT_PARAMETERS, **overrides}
    print("开始训练 fastText")
    started = perf_counter()
    model = train_fasttext(path, **overrides)
    seconds = perf_counter() - started
    run = create_run_dir("fasttext")
    model.save_model(str(run / "model.bin"))
    save_json(parameters, run / "training_config.json")
    metrics = print_evaluation(dev["label"], predict_labels(model, dev["comment"]))
    save_dev_report(run, "fasttext", metrics, train_rows=len(train_frame), dev_rows=len(dev), seconds=seconds)
    write_manifest(run, "fasttext", "original", "model.bin", format="fasttext", preprocessing="char_space_v1")
    print(f"模型和 dev 结果已保存：{run}")
    return run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--lr", type=float, default=0.5)
    parser.add_argument("--dim", type=int, default=100)
    parser.add_argument("--thread", type=int, default=4)
    args = parser.parse_args()
    train(epoch=args.epochs, lr=args.lr, dim=args.dim, thread=args.thread)


if __name__ == "__main__":
    main()
