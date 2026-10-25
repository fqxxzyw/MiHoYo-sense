"""统一学习实验的模型路径、源模型副本、哈希和评估记录。"""

import hashlib
import json
from pathlib import Path
import shutil

from hoyo_sense.config import load_config, project_path
from hoyo_sense.modeling.common.evaluation import LABEL_NAMES
from hoyo_sense.storage import save_json, utc_now


def file_hash(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def read_manifest(run):
    path = project_path(run) / "artifact.json"
    with path.open(encoding="utf-8") as source:
        return json.load(source)


def artifact_path(run):
    """manifest 中只允许当前运行目录内部的相对路径。"""
    run = project_path(run)
    manifest = read_manifest(run)
    relative = Path(manifest["artifact"])
    target = (run / relative).resolve()
    if relative.is_absolute() or not target.is_relative_to(run) or target == run:
        raise ValueError("模型文件路径必须位于运行目录内部")
    if not target.exists():
        raise FileNotFoundError(target)
    if target.is_dir() and any(not p.resolve().is_relative_to(run) for p in target.rglob("*")):
        raise ValueError("模型目录不能链接到外部文件")
    return target


def resolve_run(model_path, algorithm, *, variant=None):
    """指定目录优先；不指定时选择最新完成的匹配版本，并由调用方打印。"""
    if model_path is not None:
        run = project_path(model_path)
        manifest = read_manifest(run)
        if manifest["algorithm"] != algorithm:
            raise ValueError(f"需要 {algorithm} 模型，实际为 {manifest['algorithm']}")
        if variant is not None and manifest["variant"] != variant:
            raise ValueError(f"此操作需要 {variant} 模型")
        artifact_path(run)
        return run
    root = load_config().paths["outputs_dir"] / "models" / algorithm
    candidates = sorted(root.glob("*/artifact.json"), reverse=True)
    for candidate in candidates:
        try:
            return resolve_run(candidate.parent, algorithm, variant=variant)
        except (OSError, ValueError, KeyError):
            continue
    raise FileNotFoundError(f"没有可用的 {algorithm} 模型，请先训练或通过 --model 指定运行目录")


def write_manifest(run, algorithm, variant, artifact, **details):
    data = dict(algorithm=algorithm, variant=variant, artifact=artifact,
                labels={str(i): name for i, name in enumerate(LABEL_NAMES)},
                created_at=utc_now(), **details)
    save_json(data, Path(run) / "artifact.json")
    artifact_path(run)
    return data


def payload_hashes(run):
    target = artifact_path(run)
    paths = sorted(target.rglob("*")) if target.is_dir() else [target]
    return {str(p.relative_to(run)).replace("\\", "/"): file_hash(p)
            for p in paths if p.is_file()}


def snapshot_source(source, destination):
    """每次量化/蒸馏复制一份源模型；不递归复制旧实验的 source_copy。"""
    source, destination = project_path(source), project_path(destination)
    if destination == source or destination.is_relative_to(source):
        raise ValueError("源模型副本必须放在独立实验目录中")
    before = payload_hashes(source)
    destination.mkdir(parents=True, exist_ok=False)
    artifact = artifact_path(source)
    target = destination / artifact.relative_to(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    if artifact.is_dir():
        shutil.copytree(artifact, target)
    else:
        shutil.copy2(artifact, target)
    for name in ("artifact.json", "dev_metrics.json", "training_config.json"):
        if (source / name).is_file():
            shutil.copy2(source / name, destination / name)
    if payload_hashes(destination) != before or payload_hashes(source) != before:
        raise RuntimeError("源模型复制校验失败")
    save_json({"source": str(source), "sha256": before, "copied_at": utc_now()},
              destination / "source_hashes.json")
    return destination


def assert_source_unchanged(source, copied):
    with (Path(copied) / "source_hashes.json").open(encoding="utf-8") as file:
        expected = json.load(file)["sha256"]
    if payload_hashes(source) != expected or payload_hashes(copied) != expected:
        raise RuntimeError("原始模型或保留的副本发生变化")


def model_bytes(run):
    target = artifact_path(run)
    return sum(p.stat().st_size for p in target.rglob("*") if p.is_file()) if target.is_dir() else target.stat().st_size


def check_disjoint(train, dev):
    if train.empty or dev.empty:
        raise ValueError("train/dev 不能为空")
    if set(train["post_id"]) & set(dev["post_id"]):
        raise ValueError("train/dev 存在重复帖子")


def prediction_rows(texts, labels):
    return [{"comment": text, "label": int(label), "sentiment": LABEL_NAMES[int(label)]}
            for text, label in zip(texts, labels)]


def validate_texts(texts):
    if isinstance(texts, str):
        raise ValueError("请传入评论列表，而不是单个字符串")
    texts = list(texts)
    if not texts or any(not isinstance(text, str) or not text.strip() for text in texts):
        raise ValueError("请输入至少一条非空评论")
    return texts


def save_dev_report(run, algorithm, metrics, *, train_rows, dev_rows, seconds, **extra):
    report = dict(model=algorithm, train_rows=train_rows, dev_rows=dev_rows,
                  training_seconds=seconds, dev_macro_f1=metrics["macro_f1"],
                  dev_accuracy=metrics["accuracy"],
                  classification_report=metrics["classification_report"],
                  confusion_matrix=metrics["confusion_matrix"], **extra)
    save_json(report, Path(run) / "dev_metrics.json")
    return report


def save_comparison(run, source, before, after):
    old_size, new_size = model_bytes(source), model_bytes(run)
    save_json({"split": "dev", "source": str(source),
               "source_metrics": before, "result_metrics": after,
               "macro_f1_change": after["macro_f1"] - before["macro_f1"],
               "source_model_bytes": old_size, "result_model_bytes": new_size,
               "compression_ratio": old_size / new_size if new_size else None,
               "size_scope": "仅推理模型文件，不包含保留的源模型副本"},
              Path(run) / "comparison.json")
