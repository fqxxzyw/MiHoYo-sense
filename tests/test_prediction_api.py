"""验证模型发现、切换、缓存、输入校验与实际 HTTP 接口；不训练模型。"""

from http.client import HTTPConnection
import json
from pathlib import Path
import sys
from threading import Thread

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from api.prediction_service import PredictionService
from api.server import create_server


def create_model(root, algorithm, version):
    run = root / algorithm / version
    run.mkdir(parents=True)
    (run / "pipeline.joblib").write_bytes(b"test model")
    (run / "dev_metrics.json").write_text(json.dumps({"dev_macro_f1": 0.8, "train_rows": 12}), encoding="utf-8")
    return run


@pytest.fixture
def service(tmp_path):
    create_model(tmp_path, "tfidf_logistic", "20261022_old")
    create_model(tmp_path, "tfidf_logistic", "20261023_new")
    create_model(tmp_path, "tfidf_rf", "20261024_rf")
    loaded = []

    def loader(path):
        loaded.append(path)
        return object()

    def adapter(path, texts, *, model):
        assert model is not None
        return [{"comment": text, "label": 1, "sentiment": "正面"} for text in texts]

    instance = PredictionService(tmp_path, loader=loader, adapters={"tfidf_logistic": adapter})
    instance.loaded_paths = loaded
    return instance


def test_catalog_only_enables_registered_saved_models(service):
    catalog = service.catalog()
    assert catalog["default_model_id"] == "tfidf_logistic/20261023_new"
    assert len(catalog["models"]) == 2
    assert catalog["models"][0]["dev_macro_f1"] == 0.8
    assert not next(item for item in catalog["algorithms"] if item["id"] == "tfidf_rf")["available"]
    assert not service.loaded_paths  # 扫描列表不加载模型权重。


def test_switch_model_versions_and_cache(service):
    first = service.predict("tfidf_logistic/20261023_new", ["好玩"])
    service.predict("tfidf_logistic/20261023_new", ["不错"])
    second = service.predict("tfidf_logistic/20261022_old", ["喜欢"])
    assert first["model"]["id"] != second["model"]["id"]
    assert len(service.loaded_paths) == 2
    assert first["counts"] == {"positive": 1, "negative": 0, "neutral": 0}
    changed = service.models_root / "tfidf_logistic/20261023_new/pipeline.joblib"
    changed.write_bytes(b"updated test model")
    service.predict("tfidf_logistic/20261023_new", ["再次预测"])
    assert len(service.loaded_paths) == 3


def test_switch_algorithms_with_registered_predictors(tmp_path):
    create_model(tmp_path, "tfidf_logistic", "lr")
    create_model(tmp_path, "tfidf_rf", "rf")
    loaded = []

    class FakePipeline:
        def __init__(self, label):
            self.label = label

        def predict(self, texts):
            return [self.label] * len(texts)

    def loader(path):
        loaded.append(path)
        return FakePipeline(0 if path.parent.parent.name == "tfidf_rf" else 1)

    # 使用实际注册的预测函数，确认切换算法后加载正确模型并复用缓存。
    instance = PredictionService(tmp_path, loader=loader)
    available = {item["id"] for item in instance.catalog()["models"]}
    assert available == {"tfidf_logistic/lr", "tfidf_rf/rf"}
    rf = instance.predict("tfidf_rf/rf", ["测试评论"])
    lr = instance.predict("tfidf_logistic/lr", ["测试评论"])
    instance.predict("tfidf_rf/rf", ["再次预测"])
    assert rf["model"]["algorithm"] == "tfidf_rf"
    assert rf["predictions"][0] == {
        "comment": "测试评论", "label": 0, "sentiment": "负面",
    }
    assert lr["predictions"][0]["label"] == 1
    assert len(loaded) == 2


@pytest.mark.parametrize("model,texts", [
    ("../../elsewhere", ["内容"]), ("tfidf_rf/20261024_rf", ["内容"]),
    ("tfidf_logistic/20261023_new", []), ("tfidf_logistic/20261023_new", [" "]),
    ("tfidf_logistic/20261023_new", [3]), ("tfidf_logistic/20261023_new", "not a list"),
    ("tfidf_logistic/20261023_new", ["a"] * 101),
    ("tfidf_logistic/20261023_new", ["a" * 5001]),
])
def test_reject_invalid_requests_without_loading(service, model, texts):
    with pytest.raises(ValueError):
        service.predict(model, texts)
    assert not service.loaded_paths


def test_http_interface_and_static_file_boundaries(service):
    server = create_server(0, service=service)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(method, path, data=None, content_type="application/json"):
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        body = json.dumps(data).encode() if data is not None else None
        try:
            connection.request(method, path, body, {"Content-Type": content_type})
            response = connection.getresponse()
            return response.status, response.read(), response.getheader("Content-Type")
        finally:
            connection.close()

    try:
        status, body, kind = request("GET", "/")
        assert status == 200 and "text/html" in kind
        assert "评论情感预测" in body.decode("utf-8")
        assert request("GET", "/app.css")[0] == 200
        assert request("GET", "/app.js")[0] == 200
        assert request("GET", "/api/../prediction_service.py")[0] == 404
        status, body, _ = request("GET", "/api/models")
        assert status == 200 and len(json.loads(body)["models"]) == 2
        status, body, _ = request("POST", "/api/predict", {
            "model_id": "tfidf_logistic/20261023_new", "texts": ["测试评论"],
        })
        assert status == 200 and json.loads(body)["predictions"][0]["label"] == 1
        assert request("POST", "/api/predict", {"texts": []})[0] == 400
        assert request("POST", "/api/predict", {}, "text/plain")[0] == 415
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
