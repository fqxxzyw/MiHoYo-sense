"""创建中文 BERT 分类模型，并统一原始、蒸馏和 INT8 模型的保存加载。"""

from dataclasses import dataclass
import json
from pathlib import Path

from hoyo_sense.config import load_config, project_path
from hoyo_sense.modeling.common.evaluation import LABEL_NAMES
from hoyo_sense.modeling.common.experiments import artifact_path, read_manifest, resolve_run
from hoyo_sense.storage import save_json


PRETRAINED_ID = "google-bert/bert-base-chinese"


def pretrained_dir():
    return load_config().paths["outputs_dir"] / "models" / "_pretrained" / "bert-base-chinese"


def choose_device(value="auto"):
    import torch

    if value == "auto":
        value = "cuda" if torch.cuda.is_available() else "cpu"
    if value not in {"cpu", "cuda"}:
        raise ValueError("device 必须为 auto、cpu 或 cuda")
    if value == "cuda" and not torch.cuda.is_available():
        raise ValueError("当前 PyTorch 没有可用 CUDA，请使用 --device cpu 或安装匹配的 CUDA 版本")
    return value


def label_config():
    return dict(num_labels=3, id2label=dict(enumerate(LABEL_NAMES)),
                label2id={name: i for i, name in enumerate(LABEL_NAMES)})


def build_classifier(pretrained=None):
    from transformers import BertForSequenceClassification, BertTokenizerFast

    source = project_path(pretrained) if pretrained else pretrained_dir()
    if not source.is_dir():
        raise FileNotFoundError(f"预训练模型不存在：{source}；先运行 bert/download.py")
    tokenizer = BertTokenizerFast.from_pretrained(str(source), local_files_only=True)
    model = BertForSequenceClassification.from_pretrained(
        str(source), local_files_only=True, attn_implementation="eager", **label_config(),
    )
    return model, tokenizer


@dataclass
class BertBundle:
    model: object
    tokenizer: object
    max_length: int = 128
    device: str = "cpu"


def save_bundle(bundle, folder, *, quantized=False):
    import torch

    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    bundle.tokenizer.save_pretrained(str(folder))
    if quantized:
        bundle.model.config.save_pretrained(str(folder))
        # INT8 包含 packed 参数；重建同样的量化结构后再加载 state_dict。
        torch.save(bundle.model.state_dict(), folder / "quantized_state.pt")
    else:
        bundle.model.save_pretrained(str(folder), safe_serialization=True)
    save_json({"max_length": bundle.max_length, "quantized": quantized}, folder / "inference_config.json")


def quantize_model(model, *, engine=None):
    import torch

    if engine is None:
        supported = torch.backends.quantized.supported_engines
        engine = next((name for name in ("x86", "fbgemm", "qnnpack") if name in supported), None)
    if engine not in torch.backends.quantized.supported_engines or engine == "none":
        raise RuntimeError(f"当前 PyTorch 不支持此 INT8 后端：{engine}")
    torch.backends.quantized.engine = engine
    # 仅将 Linear 权重量化为 INT8；Embedding、LayerNorm 仍为浮点。
    result = torch.ao.quantization.quantize_dynamic(
        model.cpu().eval(), {torch.nn.Linear}, dtype=torch.qint8, inplace=False,
    )
    return result, engine


def load_model(run=None, *, device="cpu"):
    import torch
    from transformers import BertConfig, BertForSequenceClassification, BertTokenizerFast

    run = resolve_run(run, "bert")
    manifest = read_manifest(run)
    folder = artifact_path(run)
    with (folder / "inference_config.json").open(encoding="utf-8") as file:
        settings = json.load(file)
    tokenizer = BertTokenizerFast.from_pretrained(str(folder), local_files_only=True)
    if manifest["format"] == "bert_dynamic_int8":
        if device == "cuda":
            raise ValueError("动态 INT8 模型只支持 CPU 推理")
        config = BertConfig.from_pretrained(str(folder), local_files_only=True)
        config._attn_implementation = "eager"
        model, _ = quantize_model(BertForSequenceClassification(config), engine=manifest["quantization_engine"])
        state = torch.load(folder / "quantized_state.pt", map_location="cpu", weights_only=True)
        model.load_state_dict(state)
        device = "cpu"
    elif manifest["format"] == "bert":
        model = BertForSequenceClassification.from_pretrained(
            str(folder), local_files_only=True, attn_implementation="eager",
        )
        device = choose_device(device)
    else:
        raise ValueError(f"未知 BERT 保存格式：{manifest['format']}")
    if model.config.num_labels != 3 or model.config.id2label != dict(enumerate(LABEL_NAMES)):
        raise ValueError("BERT 情感标签映射不匹配")
    model.to(device).eval()
    return BertBundle(model, tokenizer, settings["max_length"], device)
