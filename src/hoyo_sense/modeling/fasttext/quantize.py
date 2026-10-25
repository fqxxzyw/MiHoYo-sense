"""在原模型副本上做 fastText 乘积量化，保留原件并比较 dev 指标和大小。"""

import argparse
from time import perf_counter

from hoyo_sense.modeling.common.dataset import load_labeled_data
from hoyo_sense.modeling.common.evaluation import print_evaluation
from hoyo_sense.modeling.common.experiments import (
    assert_source_unchanged, read_manifest, resolve_run, save_comparison,
    save_dev_report, snapshot_source, write_manifest,
)
from hoyo_sense.modeling.common.model_io import create_run_dir
from hoyo_sense.modeling.fasttext.predict import load_model, predict_labels


def quantize(model_path=None, *, dsub=2):
    if dsub < 1:
        raise ValueError("dsub 必须为正整数")
    source = resolve_run(model_path, "fasttext") if model_path else resolve_run(None, "fasttext", variant="original")
    if read_manifest(source)["variant"] == "quantized":
        raise ValueError("此模型已经量化，请选择原始或蒸馏模型")
    run = create_run_dir("fasttext")
    copied = snapshot_source(source, run / "source_copy")
    model = load_model(copied)
    dev = load_labeled_data("dev")
    before = print_evaluation(dev["label"], predict_labels(model, dev["comment"]))
    started = perf_counter()
    # retrain=False：不重新训练，也不读取任何训练或测试样本。
    model.quantize(qnorm=True, retrain=False, dsub=dsub)
    model.save_model(str(run / "model.ftz"))
    seconds = perf_counter() - started
    after_labels = predict_labels(model, dev["comment"])
    after = print_evaluation(dev["label"], after_labels)
    save_dev_report(run, "fasttext", after, train_rows=0, dev_rows=len(dev), seconds=0,
                    quantization_seconds=seconds, variant="quantized")
    write_manifest(run, "fasttext", "quantized", "model.ftz", format="fasttext",
                   source=str(source), preprocessing="char_space_v1", dsub=dsub)
    # 独立重载验证 .ftz，而不仅验证仍在内存中的对象。
    restored = predict_labels(load_model(run), dev["comment"])
    if restored != after_labels:
        raise RuntimeError("量化模型保存前后预测不一致")
    save_comparison(run, copied, before, after)
    assert_source_unchanged(source, copied)
    print(f"量化结果及原模型副本：{run}")
    return run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", help="默认最新 original；也可传入 distilled 版本")
    parser.add_argument("--dsub", type=int, default=2)
    args = parser.parse_args()
    quantize(args.model, dsub=args.dsub)


if __name__ == "__main__":
    main()
