"""验证配置路径不依赖启动目录，避免采集和后续清洗读写到不同位置。"""

import importlib.util
import json
from pathlib import Path
import sys

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))
from hoyo_sense.config import load_config, project_path


def write_config(root, data):
    config = root / "configs" / "miyoushe.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps(data), encoding="utf-8")
    return config


def test_config_paths_ignore_working_directory_and_do_not_create_data(tmp_path, monkeypatch):
    root = tmp_path / "项目 带空格"
    write_config(root, {"paths": {"raw_dir": "samples/raw"}, "collection": {"max_posts": 1}})
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    config = load_config(root=root)
    assert config.miyoushe_root == root / "samples/raw/miyoushe"
    assert config.paths["processed_dir"] == root / "data/processed"
    assert config.limits["max_posts"] == 1
    assert not (root / "samples").exists()
    assert project_path("runs/test", root=root) == root / "runs/test"
    assert project_path(elsewhere, root=root) == elsewhere


@pytest.mark.parametrize("bad", [
    {"collection": {"max_pages": True}},
    {"collection": {"max_pages": -1}},
    {"collection": {"max_pages": 1.5}},
    {"collection": {"max_pages": "None"}},
    {"collection": {"max_page": 1}},
    {"paths": {"raw_dir": ""}},
    {"paths": {"raw_dir": "data/processed"}},
    {"paths": {"raw_dir": "data"}},
    {"paths": {"raw_path": "data/raw"}},
])
def test_invalid_config_fails_before_writing_or_requesting(tmp_path, bad):
    write_config(tmp_path, bad)
    with pytest.raises(ValueError):
        load_config(root=tmp_path)
    assert not (tmp_path / "data").exists()


def test_null_means_unlimited_not_missing_value(tmp_path):
    write_config(tmp_path, {"collection": {"max_posts": None, "max_pages": 0}})
    config = load_config(root=tmp_path)
    assert config.limits["max_posts"] is None
    assert config.limits["max_pages"] == 0


def test_cli_config_and_override_apply_from_other_working_directory(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[1] / "scripts/01_collect_miyoushe.py"
    spec = importlib.util.spec_from_file_location("configured_collector", path)
    c = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(c)
    config = write_config(tmp_path, {
        "paths": {"raw_dir": str(tmp_path / "configured_raw")},
        "collection": {"max_posts": 1, "max_pages": 0, "max_sub_pages": 0, "max_floors": 0},
    })
    calls = []

    def request(url, params):
        calls.append((url, params))
        if url == c.WEB_HOME_URL:
            return {"retcode": 0, "data": {"recommended_posts": [
                {"post": {"post_id": "800", "game_id": 8, "subject": "配置测试"}},
            ]}}
        assert url == c.POST_URL  # 上限为 0，因此不允许请求评论。
        return {"retcode": 0, "data": {"post": {"post": {
            "post_id": "800", "game_id": 8, "subject": "测试", "content": "",
        }}}}

    monkeypatch.setattr(c, "request_json", request)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["collector", "--config", str(config), "--batch", "--game", "zzz"])
    c.main()
    output = tmp_path / "configured_raw/miyoushe/zzz/800/full_collection"
    assert (output / "post_800.json").exists()
    assert c.read_json(output / "manifest.json")["attempts"][-1]["max_pages"] == 0
    assert len(calls) == 2
    calls.clear()
    override = tmp_path / "override_miyoushe"
    monkeypatch.setattr(sys, "argv", ["collector", "--config", str(config), "--batch", "--game", "zzz",
                                     "--raw-root", str(override), "--max-posts", "0"])
    c.main()
    assert len(calls) == 1 and calls[0][0] == c.WEB_HOME_URL
    batch = c.read_json(override / "_batches/zzz/batch_manifest.json")
    assert batch["attempts"][-1]["max_posts_per_game"] == 0
    assert batch["batches"]["zzz"]["tasks"][0]["status"] == "deferred"
    assert not (override / "zzz/800").exists()
    # 显式 None 不能误当作“未提供”，它应覆盖配置中的 max_posts=1。
    monkeypatch.setattr(sys, "argv", ["collector", "--config", str(config), "--batch", "--game", "zzz",
                                     "--raw-root", str(override), "--max-posts", "None"])
    c.main()
    batch = c.read_json(override / "_batches/zzz/batch_manifest.json")
    assert batch["attempts"][-1]["max_posts_per_game"] is None
    assert (override / "zzz/800/full_collection/post_800.json").exists()
