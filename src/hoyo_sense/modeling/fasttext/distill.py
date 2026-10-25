"""用教师在 train 上产生硬标签，训练更小的 fastText 学生模型。"""

import argparse
from time import perf_counter

from hoyo_sense.modeling.common.dataset import load_labeled_data
from hoyo_sense.modeling.common.evaluation import print_evaluation
from hoyo_sense.modeling.common.experiments import (
    assert_source_unchanged, check_disjoint, resolve_run, save_comparison,
    save_dev_report, snapshot_source, write_manifest,
)
from hoyo_sense.modeling.common.model_io import create_run_dir
from hoyo_sense.modeling.fasttext.model import DEFAULT_PARAMETERS, train_fasttext
from hoyo_sense.modeling.fasttext.predict import load_model, predict_labels
from hoyo_sense.modeling.fasttext.prepare import export_split
from hoyo_sense.storage import save_json


def distill(teacher=None, *, teacher_algorithm="bert", dim=30, bucket=50000,
            epochs=20, device="auto", batch_size=8):
    if teacher_algorithm not in {"bert", "fasttext"}:
        raise ValueError("教师必须是 bert 或 fasttext")
    source = resolve_run(teacher, teacher_algorithm, variant="original")
    train_frame, dev = load_labeled_data("train"), load_labeled_data("dev")
    check_disjoint(train_frame, dev)
    run = create_run_dir("fasttext")
    copied = snapshot_source(source, run / "source_copy")
    if teacher_algorithm == "bert":
        from hoyo_sense.modeling.bert.model import load_model as load_teacher
        from hoyo_sense.modeling.bert.predict import predict_labels as teacher_predict

        teacher_model = load_teacher(copied, device=device)
        def infer(texts):
            return teacher_predict(teacher_model, texts, batch_size=batch_size)
    else:
        teacher_model = load_model(copied)
        def infer(texts):
            return predict_labels(teacher_model, texts)
    started = perf_counter()
    # 只替换内存副本和本实验 TXT 的标签；人工标注 CSV 永不改写。
    pseudo_train = train_frame.copy()
    pseudo_train["label"] = infer(train_frame["comment"])
    pseudo_path = run / "distillation_train.txt"
    export_split(pseudo_train, pseudo_path)
    before = print_evaluation(dev["label"], infer(dev["comment"]))
    del teacher_model
    parameters = {**DEFAULT_PARAMETERS, "dim": dim, "bucket": bucket, "epoch": epochs, "wordNgrams": 2}
    save_json({**parameters, "teacher_algorithm": teacher_algorithm, "teacher": str(source),
               "method": "hard_label_distillation", "pseudo_train_rows": len(pseudo_train)},
              run / "training_config.json")
    student = train_fasttext(pseudo_path, dim=dim, bucket=bucket, epoch=epochs, wordNgrams=2)
    student.save_model(str(run / "model.bin"))
    seconds = perf_counter() - started
    after = print_evaluation(dev["label"], predict_labels(student, dev["comment"]))
    save_dev_report(run, "fasttext", after, train_rows=len(train_frame), dev_rows=len(dev),
                    seconds=seconds, variant="distilled", distillation="hard_labels")
    write_manifest(run, "fasttext", "distilled", "model.bin", format="fasttext",
                   preprocessing="char_space_v1", source=str(source),
                   teacher_algorithm=teacher_algorithm, distillation="hard_labels")
    save_comparison(run, copied, before, after)
    assert_source_unchanged(source, copied)
    print(f"fastText 学生、教师副本和对比结果：{run}")
    return run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--teacher")
    parser.add_argument("--teacher-algorithm", choices=["bert", "fasttext"], default="bert")
    parser.add_argument("--dim", type=int, default=30)
    parser.add_argument("--bucket", type=int, default=50000)
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    parser.add_argument("--batch-size", type=int, default=8)
    distill(**vars(parser.parse_args()))


if __name__ == "__main__":
    main()
