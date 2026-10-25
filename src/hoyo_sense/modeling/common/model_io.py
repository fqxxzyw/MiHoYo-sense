"""统一模型输出目录，并读写 scikit-learn 的完整 Pipeline。"""

from datetime import datetime, timezone

import joblib

from hoyo_sense.config import load_config, project_path


def create_run_dir(algorithm):
    """每次训练单独建目录，保留以前的模型。"""
    if not algorithm.isidentifier():
        raise ValueError("算法目录名必须是有效的 Python 标识符")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f_UTC")
    parent = load_config().paths["outputs_dir"] / "models" / algorithm
    # Windows 时钟可能连续返回相同时间；冲突时换目录，绝不覆盖已有模型。
    for sequence in range(1000):
        output = parent / (stamp if sequence == 0 else f"{stamp}_{sequence}")
        try:
            output.mkdir(parents=True, exist_ok=False)
            return output
        except FileExistsError:
            continue
    raise FileExistsError("无法创建独立的模型运行目录")


def save_pipeline(model, run_dir):
    """同时保存 TF-IDF 特征和分类器，路径格式与已有模型一致。"""
    path = project_path(run_dir) / "pipeline.joblib"
    joblib.dump(model, path)
    return path


def load_pipeline(model_path):
    """接受本地模型文件或含 pipeline.joblib 的运行目录。"""
    path = project_path(model_path)
    if path.is_dir():
        path = path / "pipeline.joblib"
    return joblib.load(path)
