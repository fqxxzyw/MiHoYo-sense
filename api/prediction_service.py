"""公共预测服务：发现可用模型、缓存已加载模型并调用算法预测代码。"""

from collections import OrderedDict
import json
import math
from pathlib import Path
from threading import RLock
from time import perf_counter

from hoyo_sense.config import load_config
from hoyo_sense.modeling.common.model_io import load_pipeline
from hoyo_sense.modeling.common.experiments import artifact_path, read_manifest
from hoyo_sense.modeling.tfidf_logistic.predict import predict_texts as predict_logistic
from hoyo_sense.modeling.tfidf_rf.predict import predict_texts as predict_rf
from hoyo_sense.modeling.fasttext.predict import predict_texts as predict_fasttext, load_model as load_fasttext
from hoyo_sense.modeling.bert.predict import predict_texts as predict_bert
from hoyo_sense.modeling.bert.model import load_model as load_bert


ALGORITHMS = {
    "tfidf_logistic": "TF-IDF + LogisticRegression",
    "tfidf_rf": "TF-IDF + RandomForest",
    "fasttext": "fastText",
    "bert": "BERT",
    "llm": "LLM",
}
# 新算法实现后，在这里注册同样接收 model_path、texts、model 的预测适配器。
ADAPTERS = {
    "tfidf_logistic": predict_logistic,
    "tfidf_rf": predict_rf,
    "fasttext": predict_fasttext,
    "bert": predict_bert,
}
LOADERS = {"tfidf_logistic": load_pipeline, "tfidf_rf": load_pipeline,
           "fasttext": load_fasttext, "bert": load_bert}
MAX_TEXTS = 100
MAX_TEXT_LENGTH = 5000
MAX_TOTAL_LENGTH = 50000


class PredictionService:
    def __init__(self, models_root=None, *, adapters=None, loader=None):
        self.models_root = Path(models_root or load_config().paths["outputs_dir"] / "models").resolve()
        self.adapters = ADAPTERS if adapters is None else adapters
        self.loader = loader
        self._cache = OrderedDict()
        self._lock = RLock()

    def catalog(self):
        models = []
        algorithms = []
        for algorithm, name in ALGORITHMS.items():
            folder = self.models_root / algorithm
            available = []
            if algorithm in self.adapters and folder.is_dir():
                for run in sorted(folder.iterdir(), key=lambda path: path.name, reverse=True):
                    if not run.is_dir() or not run.resolve().is_relative_to(self.models_root):
                        continue
                    try:
                        model_path, variant = self._artifact(run, algorithm)
                    except (OSError, ValueError, KeyError, TypeError):
                        continue
                    metadata = self._metadata(run / "dev_metrics.json")
                    record = {
                        "id": f"{algorithm}/{run.name}", "algorithm": algorithm,
                        "name": name, "version": run.name,
                        "dev_macro_f1": metadata.get("dev_macro_f1"),
                        "dev_accuracy": metadata.get("dev_accuracy"),
                        "train_rows": metadata.get("train_rows"),
                        "variant": variant,
                    }
                    available.append(record)
                    models.append(record)
            algorithms.append({
                "id": algorithm, "name": name, "available": bool(available),
                "model_count": len(available),
                "status": "已就绪" if available else "尚无已保存模型" if algorithm in self.adapters else "待接入",
            })
        return {"algorithms": algorithms, "models": models,
                "default_model_id": models[0]["id"] if models else None,
                "limits": {"max_texts": MAX_TEXTS, "max_text_length": MAX_TEXT_LENGTH}}

    @staticmethod
    def _metadata(path):
        try:
            if path.stat().st_size > 100000:
                return {}
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict):
                return {}
            result = {}
            for key in ("dev_macro_f1", "dev_accuracy"):
                value = data.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 1:
                    result[key] = value
            rows = data.get("train_rows")
            if isinstance(rows, int) and not isinstance(rows, bool) and rows >= 0:
                result["train_rows"] = rows
            return result
        except (OSError, ValueError):
            return {}

    def _artifact(self, run, algorithm):
        if algorithm in {"tfidf_logistic", "tfidf_rf"}:
            path, variant = run / "pipeline.joblib", "original"
            if not path.is_file():
                raise FileNotFoundError(path)
        else:
            manifest = read_manifest(run)
            if manifest["algorithm"] != algorithm:
                raise ValueError("模型目录和算法不匹配")
            allowed_formats = {"fasttext": {"fasttext"}, "bert": {"bert", "bert_dynamic_int8"}}
            if manifest["format"] not in allowed_formats.get(algorithm, set()):
                raise ValueError("未知模型格式")
            path, variant = artifact_path(run), manifest["variant"]
        if not path.resolve().is_relative_to(self.models_root):
            raise ValueError("模型不能位于模型根目录外部")
        return path.resolve(), variant

    def _load_cached(self, path, algorithm, run):
        files = sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else [path]
        if (run / "artifact.json").is_file():
            files.append(run / "artifact.json")
        signature = tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in files)
        with self._lock:
            cached = self._cache.get(path)
            if cached and cached[0] == signature:
                self._cache.move_to_end(path)
                return cached[1]
            model = self.loader(path) if self.loader else LOADERS[algorithm](run)
            self._cache[path] = (signature, model)
            self._cache.move_to_end(path)
            while len(self._cache) > 3:
                self._cache.popitem(last=False)
            return model

    def predict(self, model_id, texts):
        if not isinstance(model_id, str):
            raise ValueError("请选择可用模型")
        if not isinstance(texts, list) or not 1 <= len(texts) <= MAX_TEXTS:
            raise ValueError(f"每次请输入 1～{MAX_TEXTS} 条评论")
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("评论必须是非空文本")
        if any(len(text) > MAX_TEXT_LENGTH for text in texts) or sum(map(len, texts)) > MAX_TOTAL_LENGTH:
            raise ValueError("评论过长：单条最多 5000 字，合计最多 50000 字")
        model_info = next((item for item in self.catalog()["models"] if item["id"] == model_id), None)
        if model_info is None:
            raise ValueError("该模型不可用，请刷新模型列表后重新选择")
        run = (self.models_root / model_id).resolve()
        path, _ = self._artifact(run, model_info["algorithm"])
        if not path.is_relative_to(self.models_root):
            raise ValueError("无效模型路径")
        started = perf_counter()
        model = self._load_cached(path, model_info["algorithm"], run)
        predictions = self.adapters[model_info["algorithm"]](path, texts, model=model)
        if len(predictions) != len(texts) or any(item["label"] not in (0, 1, 2) for item in predictions):
            raise RuntimeError("算法返回的预测结果格式不正确")
        return {
            "model": model_info, "predictions": predictions,
            "counts": {name: sum(item["label"] == label for item in predictions)
                       for label, name in ((0, "negative"), (1, "positive"), (2, "neutral"))},
            "elapsed_ms": round((perf_counter() - started) * 1000, 2),
        }
