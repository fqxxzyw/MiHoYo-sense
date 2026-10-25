"""标注合并的行对齐、过滤边界、备份与重复运行验证。"""

from pathlib import Path
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from hoyo_sense.analysis.merge_annotations import merge_annotations, removal_reason, SPLITS


@pytest.mark.parametrize("text,reason", [
    ("666", "numeric_only"), (" １２３\n", "numeric_only"), ("②③", "numeric_only"),
    ("？！？", "symbols_only"), ("……", "symbols_only"), ("√+$", "symbols_only"),
    ("一", ""), ("六六六", ""), ("666好玩", ""), ("+1", ""), ("123!!!", ""),
    ("_(派蒙_开心)_(7)", ""), ("_(!)", ""), ("👍😂", ""), ("❤️", ""),
])
def test_removal_boundaries(text, reason):
    assert removal_reason(text) == reason


def test_optional_pictograph_filter():
    assert removal_reason("👍😂", drop_emoji=True) == "symbols_only"
    assert removal_reason("❤️", drop_emoji=True) == "symbols_only"
    assert removal_reason("_(派蒙_开心)", drop_emoji=True) == ""


def inputs(tmp_path):
    annotated = tmp_path / "annotated"
    annotated.mkdir()
    for split in SPLITS:
        original = pd.DataFrame([
            ["12345678901234567890", "_(派蒙_开心)", -1],
            ["12345678901234567890", "666", 1],
            ["12345678901234567890", "？？？", -1],
            ["2", "保留", 0], ["2", "保留", 1], ["2", "一", -1],
        ], columns=["post_id", "comment", "label"])
        original.to_csv(annotated / f"{split}_labeled.csv", index=False, encoding="utf-8-sig")
        original.loc[0, "label"] = 2
        original.to_csv(annotated / f"{split}_labeled_v2.csv", index=False, encoding="utf-8-sig")
    return annotated, tmp_path / "review"


def test_merge_backup_audit_and_idempotency(tmp_path):
    annotated, review = inputs(tmp_path)
    before = {split: (annotated / f"{split}_labeled.csv").read_bytes() for split in SPLITS}
    report = merge_annotations(annotated, review)
    for split in SPLITS:
        path = annotated / f"{split}_labeled.csv"
        assert (Path(report["backup_dir"]) / path.name).read_bytes() == before[split]
        frame = pd.read_csv(path, dtype={"post_id": "string"}, keep_default_na=False)
        assert frame["post_id"].tolist() == ["12345678901234567890", "2", "2", "2"]
        assert frame["label"].tolist() == [2, 0, 1, -1]
        removed = pd.read_csv(Path(report["run_dir"]) / f"{split}_removed.csv")
        assert removed["source_row"].tolist() == [2, 3]
        assert report["splits"][split]["merged_emoticons"] == 1
    hashes = report["output_hashes"]
    assert merge_annotations(annotated, review)["output_hashes"] == hashes
    assert len(list((annotated / "backups").iterdir())) == 1


@pytest.mark.parametrize("problem", ["order", "label"])
def test_bad_last_split_does_not_modify_any_original(tmp_path, problem):
    annotated, review = inputs(tmp_path)
    path = annotated / "test_labeled_v2.csv"
    frame = pd.read_csv(path, dtype={"post_id": "string"})
    if problem == "order":
        frame = frame.iloc[::-1]
    else:
        frame.loc[3, "label"] = 1
    frame.to_csv(path, index=False)
    before = {split: (annotated / f"{split}_labeled.csv").read_bytes() for split in SPLITS}
    with pytest.raises(ValueError):
        merge_annotations(annotated, review)
    assert not (annotated / "backups").exists()
    for split in SPLITS:
        assert (annotated / f"{split}_labeled.csv").read_bytes() == before[split]


def test_write_failure_restores_committed_split(tmp_path, monkeypatch):
    from hoyo_sense.analysis import merge_annotations as module

    annotated, review = inputs(tmp_path)
    before = {split: (annotated / f"{split}_labeled.csv").read_bytes() for split in SPLITS}
    replace = module.replace_csv

    def fail_second(frame, path):
        if path.name == "dev_labeled.csv":
            raise PermissionError("模拟文件被占用")
        replace(frame, path)

    monkeypatch.setattr(module, "replace_csv", fail_second)
    with pytest.raises(PermissionError):
        merge_annotations(annotated, review)
    assert not (review / "annotation_merge_latest.json").exists()
    for split in SPLITS:
        assert (annotated / f"{split}_labeled.csv").read_bytes() == before[split]
