"""离线验证模型副本、数据边界、模型格式分发与可选依赖下的微型真实模型。"""

import importlib
import json
from pathlib import Path
import sys

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from hoyo_sense.modeling.common import experiments as exp


def make_run(root, algorithm="fasttext", variant="original", directory=False):
    root.mkdir(parents=True)
    if directory:
        (root / "model").mkdir()
        (root / "model/config.json").write_text("{}")
        (root / "model/model.safetensors").write_bytes(b"original weights")
        artifact, format_name = "model", "bert"
    else:
        (root / "model.bin").write_bytes(b"original weights")
        artifact, format_name = "model.bin", "fasttext"
    exp.write_manifest(root, algorithm, variant, artifact, format=format_name)
    return root


@pytest.mark.parametrize("directory", [False, True])
def test_separate_copies_preserve_sources_and_do_not_nest_old_copies(tmp_path, directory):
    source = make_run(tmp_path / "original", "bert" if directory else "fasttext", directory=directory)
    original_hashes = exp.payload_hashes(source)
    first = exp.snapshot_source(source, tmp_path / "quantized/source_copy")
    second = exp.snapshot_source(source, tmp_path / "distilled/source_copy")
    assert first != second and exp.payload_hashes(second) == original_hashes
    exp.assert_source_unchanged(source, first)
    (source / "source_copy").mkdir()
    (source / "source_copy/old.txt").write_text("do not recursively copy")
    third = exp.snapshot_source(source, tmp_path / "third/source_copy")
    assert not (third / "source_copy").exists()
    artifact = exp.artifact_path(first)
    target = next(artifact.glob("*.safetensors")) if directory else artifact
    target.write_bytes(b"changed copy")
    assert exp.payload_hashes(source) == original_hashes
    with pytest.raises(RuntimeError):
        exp.assert_source_unchanged(source, first)
    with pytest.raises(FileExistsError):
        exp.snapshot_source(source, second)


def test_reject_model_path_escape_and_wrong_algorithm(tmp_path):
    run = make_run(tmp_path / "run")
    with pytest.raises(ValueError):
        exp.resolve_run(run, "bert")
    with pytest.raises(ValueError):
        exp.snapshot_source(run, run / "copy")
    (tmp_path / "outside.bin").write_bytes(b"x")
    manifest = exp.read_manifest(run)
    manifest["artifact"] = "../outside.bin"
    (run / "artifact.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        exp.artifact_path(run)


def test_prepared_txt_must_match_current_training_csv(tmp_path):
    from hoyo_sense.modeling.fasttext.prepare import export_split
    from hoyo_sense.modeling.fasttext.train import verify_training_file

    frame = pd.DataFrame({"post_id": ["1", "2"], "comment": ["很好\n玩", "一般"], "label": [1, 2]})
    path = tmp_path / "train.txt"
    export_split(frame, path)
    verify_training_file(path, frame)
    frame.loc[0, "label"] = 0
    with pytest.raises(ValueError, match="第 1 行"):
        verify_training_file(path, frame)


def test_fasttext_predict_decodes_labels_and_reuses_preprocessing():
    from hoyo_sense.modeling.fasttext.predict import predict_texts

    class FakeModel:
        def predict(self, texts, k):
            assert texts == ["好 玩", "太 差 了"] and k == 1
            return [["__label__1"], ["__label__0"]], [[0.8], [0.9]]

    items = predict_texts(None, ["好玩", "太差了"], model=FakeModel())
    assert [item["label"] for item in items] == [1, 0]
    assert items[0]["comment"] == "好玩"


@pytest.mark.parametrize("algorithm", ["fasttext", "bert"])
def test_evaluation_only_reads_explicit_split(tmp_path, monkeypatch, algorithm):
    module = importlib.import_module(f"hoyo_sense.modeling.{algorithm}.evaluate")
    run = make_run(tmp_path / algorithm, algorithm, directory=algorithm == "bert")
    calls = []
    def read(split):
        calls.append(split)
        return pd.DataFrame({"comment": ["坏", "好", "一般"], "label": [0, 1, 2]})
    monkeypatch.setattr(module, "load_labeled_data", read)
    monkeypatch.setattr(module, "load_model", lambda *a, **k: object())
    monkeypatch.setattr(module, "predict_labels", lambda *a, **k: [0, 1, 2])
    assert module.evaluate(run)["macro_f1"] == 1
    assert calls == ["dev"]
    with pytest.raises(ValueError):
        module.evaluate(run, "train")
    assert calls == ["dev"]


def test_api_discovers_native_and_bert_variants_and_invalidates_directory_cache(tmp_path):
    from api.prediction_service import PredictionService

    rf = tmp_path / "tfidf_rf/001"
    rf.mkdir(parents=True)
    (rf / "pipeline.joblib").write_bytes(b"rf")
    make_run(tmp_path / "fasttext/001")
    bert = make_run(tmp_path / "bert/001", "bert", variant="distilled", directory=True)
    loaded = []
    def loader(path):
        loaded.append(path)
        return object()
    def adapter(path, texts, *, model):
        assert model is not None
        return exp.prediction_rows(texts, [2] * len(texts))
    service = PredictionService(tmp_path, loader=loader,
                                adapters={name: adapter for name in ("tfidf_rf", "fasttext", "bert")})
    catalog = service.catalog()
    assert len(catalog["models"]) == 3 and not loaded
    assert next(m for m in catalog["models"] if m["algorithm"] == "bert")["variant"] == "distilled"
    service.predict("bert/001", ["测试"])
    service.predict("bert/001", ["测试"])
    assert len(loaded) == 1
    (bert / "model/config.json").write_text('{"changed": true}')
    service.predict("bert/001", ["测试"])
    assert len(loaded) == 2
    service.predict("fasttext/001", ["测试"])
    service.predict("tfidf_rf/001", ["测试"])
    assert len(loaded) == 4


def test_fasttext_training_reads_no_test_and_keeps_native_artifact(tmp_path, monkeypatch):
    from hoyo_sense.modeling.fasttext import train as module
    from hoyo_sense.modeling.fasttext.prepare import export_split
    from types import SimpleNamespace

    train = pd.DataFrame({"post_id": ["1", "2", "3"], "comment": ["坏", "好", "一般"], "label": [0, 1, 2]})
    dev = train.assign(post_id=["4", "5", "6"])
    (tmp_path / "fasttext").mkdir()
    export_split(train, tmp_path / "fasttext/train.txt")
    read = []
    monkeypatch.setattr(module, "load_labeled_data", lambda split: read.append(split) or {"train": train, "dev": dev}[split])
    monkeypatch.setattr(module, "load_config", lambda: SimpleNamespace(paths={"annotated_dir": tmp_path}))
    run = tmp_path / "run"
    run.mkdir()
    monkeypatch.setattr(module, "create_run_dir", lambda name: run)
    class Fake:
        def save_model(self, path):
            Path(path).write_bytes(b"native weights")
    monkeypatch.setattr(module, "train_fasttext", lambda path, **kw: Fake())
    monkeypatch.setattr(module, "predict_labels", lambda model, texts: [0, 1, 2])
    assert module.train() == run
    assert read == ["train", "dev"]
    assert exp.read_manifest(run)["artifact"] == "model.bin"


def test_hard_distillation_only_relabels_train_copy(tmp_path, monkeypatch):
    from hoyo_sense.modeling.fasttext import distill as module

    source = make_run(tmp_path / "original")
    source_hashes = exp.payload_hashes(source)
    train = pd.DataFrame({"post_id": ["1", "2", "3"], "comment": ["坏", "好", "一般"], "label": [0, 1, 2]})
    dev = train.assign(post_id=["4", "5", "6"])
    read = []
    monkeypatch.setattr(module, "load_labeled_data", lambda split: read.append(split) or {"train": train, "dev": dev}[split])
    run = tmp_path / "student"
    run.mkdir()
    monkeypatch.setattr(module, "create_run_dir", lambda name: run)
    monkeypatch.setattr(module, "load_model", lambda run: object())
    monkeypatch.setattr(module, "predict_labels", lambda model, texts: [2] * len(texts))
    class Student:
        def save_model(self, path):
            Path(path).write_bytes(b"small weights")
    def training(path, **kwargs):
        assert all(line.startswith("__label__2 ") for line in path.read_text(encoding="utf-8").splitlines())
        return Student()
    monkeypatch.setattr(module, "train_fasttext", training)
    module.distill(source, teacher_algorithm="fasttext")
    assert read == ["train", "dev"]
    assert train["label"].tolist() == [0, 1, 2]
    assert exp.payload_hashes(source) == source_hashes
    assert exp.read_manifest(run)["distillation"] == "hard_labels"


def test_real_fasttext_quantization_roundtrip_when_installed(tmp_path, monkeypatch):
    pytest.importorskip("fasttext", reason="尚未安装 fasttext-wheel，真实量化验证需要该依赖")
    from hoyo_sense.modeling.fasttext.model import train_fasttext
    from hoyo_sense.modeling.fasttext import quantize as module
    from hoyo_sense.modeling.fasttext.predict import load_model, predict_labels

    training = tmp_path / "tiny.txt"
    training.write_text("".join(f"__label__{i % 3} 好 {chr(0x4E00 + i)} 活 动\n" for i in range(300)), encoding="utf-8")
    native = train_fasttext(training, epoch=2, dim=8, bucket=512, minCount=1, thread=1)
    original = tmp_path / "original"
    original.mkdir()
    native.save_model(str(original / "model.bin"))
    exp.write_manifest(original, "fasttext", "original", "model.bin", format="fasttext")
    before = exp.payload_hashes(original)
    run = tmp_path / "quantized"
    run.mkdir()
    monkeypatch.setattr(module, "create_run_dir", lambda name: run)
    monkeypatch.setattr(module, "load_labeled_data", lambda split: pd.DataFrame({"comment": ["好活动", "一般"], "label": [1, 2]}))
    module.quantize(original)
    model = load_model(run)
    assert model.is_quantized()
    assert len(predict_labels(model, ["好活动"])) == 1
    assert exp.payload_hashes(original) == before


def test_real_tiny_bert_train_distill_quantize_reload_when_installed(tmp_path):
    torch = pytest.importorskip("torch", reason="尚未安装 PyTorch，真实 BERT 验证需要该依赖")
    transformers = pytest.importorskip("transformers")
    from hoyo_sense.modeling.bert.model import BertBundle, label_config, load_model, quantize_model, save_bundle
    from hoyo_sense.modeling.bert.predict import predict_labels
    from hoyo_sense.modeling.bert.training import distillation_loss, fit, make_student

    previous_threads = torch.get_num_threads()
    torch.set_num_threads(1)
    try:
        vocab = tmp_path / "vocab.txt"
        vocab.write_text("[PAD]\n[UNK]\n[CLS]\n[SEP]\n[MASK]\nbad\ngood\nneutral\n", encoding="utf-8")
        tokenizer = transformers.BertTokenizerFast(vocab_file=str(vocab))
        config = transformers.BertConfig(vocab_size=8, hidden_size=16, num_hidden_layers=2,
                                        num_attention_heads=2, intermediate_size=32, max_position_embeddings=32,
                                        **label_config())
        config._attn_implementation = "eager"
        teacher = transformers.BertForSequenceClassification(config).eval()
        bundle = BertBundle(teacher, tokenizer, 16)
        frame = pd.DataFrame({"post_id": ["1", "2", "3"], "comment": ["bad", "good", "neutral"], "label": [0, 1, 2]})
        original = tmp_path / "bert_original"
        original.mkdir()
        metrics, epoch = fit(bundle, frame, frame.assign(post_id=["4", "5", "6"]), original,
                             epochs=1, batch_size=2, accumulation_steps=2)
        exp.write_manifest(original, "bert", "original", "model", format="bert")
        assert epoch == 1 and 0 <= metrics["macro_f1"] <= 1
        bundle = load_model(original)
        before = exp.payload_hashes(original)
        student, selected = make_student(bundle.model, 1)
        assert selected == [1] and student.config.num_hidden_layers == 1
        logits = torch.randn(3, 3, requires_grad=True)
        teacher_logits = torch.randn(3, 3, requires_grad=True)
        loss = distillation_loss(logits, teacher_logits, torch.tensor([0, 1, 2]))
        loss.backward()
        assert logits.grad is not None and teacher_logits.grad is None
        distilled = tmp_path / "student"
        distilled.mkdir()
        student_bundle = BertBundle(student, tokenizer, 16)
        fit(student_bundle, frame, frame, distilled, epochs=1, batch_size=2, teacher=bundle)
        assert all(parameter.grad is None for parameter in bundle.model.parameters())
        copied = exp.snapshot_source(original, tmp_path / "int8/source_copy")
        quantized = load_model(copied)
        quantized.model, engine = quantize_model(quantized.model)
        save_bundle(quantized, tmp_path / "int8/model", quantized=True)
        exp.write_manifest(tmp_path / "int8", "bert", "quantized", "model", format="bert_dynamic_int8", quantization_engine=engine)
        reloaded = load_model(tmp_path / "int8")
        assert predict_labels(reloaded, frame["comment"]) == predict_labels(quantized, frame["comment"])
        assert exp.payload_hashes(original) == before
    finally:
        torch.set_num_threads(previous_threads)
