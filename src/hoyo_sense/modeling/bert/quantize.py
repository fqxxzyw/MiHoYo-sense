"""复制 BERT 后做 CPU 动态 INT8 量化，重新加载并比较 dev 结果。"""

import argparse
from time import perf_counter

from hoyo_sense.modeling.bert.model import load_model, quantize_model, save_bundle
from hoyo_sense.modeling.bert.predict import predict_labels
from hoyo_sense.modeling.common.dataset import load_labeled_data
from hoyo_sense.modeling.common.evaluation import print_evaluation
from hoyo_sense.modeling.common.experiments import (
    assert_source_unchanged, read_manifest, resolve_run, save_comparison,
    save_dev_report, snapshot_source, write_manifest,
)
from hoyo_sense.modeling.common.model_io import create_run_dir


def quantize(model_path=None, *, batch_size=32):
    source = resolve_run(model_path, "bert") if model_path else resolve_run(None, "bert", variant="original")
    if read_manifest(source)["format"] != "bert":
        raise ValueError("请选择未量化的原始或蒸馏 BERT")
    run = create_run_dir("bert")
    copied = snapshot_source(source, run / "source_copy")
    bundle = load_model(copied, device="cpu")
    dev = load_labeled_data("dev")
    before = print_evaluation(dev["label"], predict_labels(bundle, dev["comment"], batch_size=batch_size))
    started = perf_counter()
    bundle.model, engine = quantize_model(bundle.model)
    save_bundle(bundle, run / "model", quantized=True)
    seconds = perf_counter() - started
    write_manifest(run, "bert", "quantized", "model", format="bert_dynamic_int8",
                   source=str(source), quantization_engine=engine)
    # 保存后重载，确保磁盘产物确实可用于独立评估和预测。
    reloaded = load_model(run)
    after = print_evaluation(dev["label"], predict_labels(reloaded, dev["comment"], batch_size=batch_size))
    save_dev_report(run, "bert", after, train_rows=0, dev_rows=len(dev), seconds=0,
                    quantization_seconds=seconds, variant="quantized")
    save_comparison(run, copied, before, after)
    assert_source_unchanged(source, copied)
    print(f"量化模型、对比结果和源模型副本：{run}")
    return run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", help="默认最新 original；也可传入 distilled 版本")
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()
    quantize(args.model, batch_size=args.batch_size)


if __name__ == "__main__":
    main()
