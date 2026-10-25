"""验证公共指标、模型读写，以及训练、评估、预测入口的职责边界。"""

import json
from datetime import datetime
from pathlib import Path
import sys
from types import SimpleNamespace

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hoyo_sense.modeling.common.evaluation import classification_metrics
from hoyo_sense.modeling.common import model_io
from hoyo_sense.modeling.tfidf_logistic import evaluate, predict, train


class SavedPipeline:
    """评估与预测若误调用 fit，测试必须失败。"""

    def fit(self, *args, **kwargs):
        raise AssertionError("加载后的模型禁止重新训练")

    def predict(self, texts):
        mapping = {"negative": 0, "positive": 1, "neutral": 2}
        return [mapping[text] for text in texts]


def test_metrics_keep_all_three_labels():
    metrics = classification_metrics([0, 1, 2], [0, 2, 2])
    assert metrics["macro_f1"] == pytest.approx(5 / 9)
    assert metrics["accuracy"] == pytest.approx(2 / 3)
    assert metrics["confusion_matrix"] == [[1, 0, 0], [0, 0, 1], [0, 0, 1]]
    assert classification_metrics([2], [2])["macro_f1"] == pytest.approx(1 / 3)


def test_model_io_uses_models_and_retains_previous_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(model_io, "load_config", lambda: SimpleNamespace(paths={"outputs_dir": tmp_path}))
    monkeypatch.setattr(model_io, "datetime", SimpleNamespace(now=lambda tz: datetime(2026, 10, 22, tzinfo=tz)))
    first = model_io.create_run_dir("tfidf_logistic")
    second = model_io.create_run_dir("tfidf_logistic")
    assert first != second
    assert first.parent == tmp_path / "models/tfidf_logistic"
    assert first.is_dir() and second.is_dir()
    path = model_io.save_pipeline(SavedPipeline(), first)
    assert model_io.load_pipeline(first).predict(["negative", "positive"]) == [0, 1]
    assert model_io.load_pipeline(path).predict(["neutral"]) == [2]
    with pytest.raises(ValueError):
        model_io.create_run_dir("../elsewhere")


def test_evaluate_loads_only_requested_split_without_fit(tmp_path, monkeypatch):
    model_path = model_io.save_pipeline(SavedPipeline(), tmp_path)
    calls = []

    def data(split):
        calls.append(split)
        return pd.DataFrame({"comment": ["negative", "positive", "neutral"], "label": [0, 1, 2]})

    monkeypatch.setattr(evaluate, "load_labeled_data", data)
    report = evaluate.evaluate(model_path)
    assert calls == ["dev"]
    assert report["rows"] == 3
    assert report["macro_f1"] == 1
    with pytest.raises(ValueError):
        evaluate.evaluate(model_path, "train")


def test_predict_keeps_input_order_without_reading_datasets(tmp_path):
    model_path = model_io.save_pipeline(SavedPipeline(), tmp_path)
    items = predict.predict_texts(model_path, ["neutral", "negative", "positive"])
    assert [item["label"] for item in items] == [2, 0, 1]
    assert [item["sentiment"] for item in items] == ["中性", "负面", "正面"]
    with pytest.raises(ValueError):
        predict.predict_texts(model_path, [" "])


def test_train_fits_only_train_and_saves_legacy_report_schema(tmp_path, monkeypatch):
    calls = []
    frame = pd.DataFrame({"post_id": ["1", "2", "3"],
                          "comment": ["negative", "positive", "neutral"], "label": [0, 1, 2]})
    dev = frame.copy()
    dev["post_id"] = ["4", "5", "6"]

    class TrainingSpy(SavedPipeline):
        def fit(self, texts, labels):
            calls.append((list(texts), list(labels)))

    loaded = []
    monkeypatch.setattr(train, "load_labeled_data", lambda split: loaded.append(split) or {"train": frame, "dev": dev}[split])
    monkeypatch.setattr(train, "build_logistic_baseline", TrainingSpy)
    monkeypatch.setattr(train, "create_run_dir", lambda algorithm: tmp_path)
    saved = []
    monkeypatch.setattr(train, "save_pipeline", lambda model, path: saved.append((model, path)))
    monkeypatch.setattr(sys, "argv", ["train"])
    train.main()
    assert loaded == ["train", "dev"]
    assert calls == [(["negative", "positive", "neutral"], [0, 1, 2])]
    assert len(saved) == 1 and saved[0][1] == tmp_path
    report = json.loads((tmp_path / "dev_metrics.json").read_text(encoding="utf-8"))
    assert set(report) == {"model", "train_rows", "dev_rows", "training_seconds", "dev_macro_f1", "dev_accuracy", "classification_report", "confusion_matrix"}
    assert report["train_rows"] == report["dev_rows"] == 3
    assert report["dev_macro_f1"] == report["dev_accuracy"] == 1
