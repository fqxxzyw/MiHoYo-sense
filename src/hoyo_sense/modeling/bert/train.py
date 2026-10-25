"""微调本地中文 BERT，以 dev Macro-F1 选择最佳 epoch，保存独立运行目录。"""

import argparse
from time import perf_counter

from hoyo_sense.modeling.bert.model import BertBundle, build_classifier, choose_device
from hoyo_sense.modeling.bert.training import fit, set_seed
from hoyo_sense.modeling.common.dataset import load_labeled_data
from hoyo_sense.modeling.common.experiments import check_disjoint, save_dev_report, write_manifest
from hoyo_sense.modeling.common.model_io import create_run_dir
from hoyo_sense.storage import save_json


def train(pretrained=None, *, epochs=3, batch_size=8, accumulation_steps=2,
          lr=2e-5, max_length=128, device="auto", seed=42):
    train_frame, dev = load_labeled_data("train"), load_labeled_data("dev")
    check_disjoint(train_frame, dev)
    set_seed(seed)
    model, tokenizer = build_classifier(pretrained)
    bundle = BertBundle(model, tokenizer, max_length, choose_device(device))
    run = create_run_dir("bert")
    parameters = dict(pretrained=str(pretrained) if pretrained else "default_local",
                      epochs=epochs, batch_size=batch_size, accumulation_steps=accumulation_steps,
                      lr=lr, max_length=max_length, device=bundle.device, seed=seed)
    save_json(parameters, run / "training_config.json")
    print(f"BERT 训练设备：{bundle.device}；保存目录：{run}")
    started = perf_counter()
    metrics, best_epoch = fit(bundle, train_frame, dev, run, epochs=epochs, batch_size=batch_size,
                              accumulation_steps=accumulation_steps, lr=lr, seed=seed)
    seconds = perf_counter() - started
    save_dev_report(run, "bert", metrics, train_rows=len(train_frame), dev_rows=len(dev),
                    seconds=seconds, best_epoch=best_epoch, timing_scope="训练及逐轮 dev 评估")
    write_manifest(run, "bert", "original", "model", format="bert", best_epoch=best_epoch)
    print(f"最佳模型和 dev 结果已保存：{run}")
    return run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pretrained", help="本地预训练目录，不自动下载")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--accumulation-steps", type=int, default=2)
    parser.add_argument("--lr", type=float, default=2e-5)
    parser.add_argument("--max-length", type=int, default=128)
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    train(**vars(args))


if __name__ == "__main__":
    main()
