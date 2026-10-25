"""冻结已微调 BERT 教师，训练较少层学生：真实标签 CE + 温度软标签 KL。"""

import argparse
from time import perf_counter

from hoyo_sense.modeling.bert.model import BertBundle, choose_device, load_model
from hoyo_sense.modeling.bert.predict import predict_labels
from hoyo_sense.modeling.bert.training import fit, make_student, set_seed
from hoyo_sense.modeling.common.dataset import load_labeled_data
from hoyo_sense.modeling.common.evaluation import print_evaluation
from hoyo_sense.modeling.common.experiments import (
    assert_source_unchanged, check_disjoint, resolve_run, save_comparison,
    save_dev_report, snapshot_source, write_manifest,
)
from hoyo_sense.modeling.common.model_io import create_run_dir
from hoyo_sense.storage import save_json


def distill(teacher=None, *, layers=4, epochs=3, batch_size=4, accumulation_steps=4,
            lr=2e-5, temperature=2.0, alpha=0.5, device="auto", seed=42):
    if temperature <= 0 or not 0 <= alpha <= 1:
        raise ValueError("temperature 必须大于零，alpha 必须在 [0, 1]")
    source = resolve_run(teacher, "bert", variant="original")
    train_frame, dev = load_labeled_data("train"), load_labeled_data("dev")
    check_disjoint(train_frame, dev)
    run = create_run_dir("bert")
    copied = snapshot_source(source, run / "source_copy")
    teacher_bundle = load_model(copied, device=choose_device(device))
    set_seed(seed)
    student, selected = make_student(teacher_bundle.model, layers)
    bundle = BertBundle(student, teacher_bundle.tokenizer, teacher_bundle.max_length, teacher_bundle.device)
    parameters = dict(teacher=str(source), layers=layers, selected_teacher_layers=selected,
                      epochs=epochs, batch_size=batch_size, accumulation_steps=accumulation_steps,
                      lr=lr, temperature=temperature, alpha=alpha, seed=seed, device=bundle.device)
    save_json(parameters, run / "training_config.json")
    before = print_evaluation(dev["label"], predict_labels(teacher_bundle, dev["comment"], batch_size=batch_size))
    started = perf_counter()
    after, best_epoch = fit(bundle, train_frame, dev, run, epochs=epochs, batch_size=batch_size,
                            accumulation_steps=accumulation_steps, lr=lr, seed=seed,
                            teacher=teacher_bundle, temperature=temperature, alpha=alpha)
    seconds = perf_counter() - started
    save_dev_report(run, "bert", after, train_rows=len(train_frame), dev_rows=len(dev),
                    seconds=seconds, best_epoch=best_epoch, variant="distilled")
    write_manifest(run, "bert", "distilled", "model", format="bert", source=str(source),
                   student_layers=layers, distillation="soft_labels_kl_and_ce", best_epoch=best_epoch)
    save_comparison(run, copied, before, after)
    assert_source_unchanged(source, copied)
    print(f"蒸馏模型、对比结果和教师副本：{run}")
    return run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--teacher", help="默认最新已微调 original BERT")
    parser.add_argument("--layers", type=int, default=4)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--accumulation-steps", type=int, default=4)
    parser.add_argument("--lr", type=float, default=2e-5)
    parser.add_argument("--temperature", type=float, default=2.0)
    parser.add_argument("--alpha", type=float, default=0.5)
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    parser.add_argument("--seed", type=int, default=42)
    distill(**vars(parser.parse_args()))


if __name__ == "__main__":
    main()
