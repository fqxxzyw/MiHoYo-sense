import csv
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hoyo_sense.analysis.prepare import prepare_analysis


def test_only_two_columns_preserves_all_comments_and_source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    comments = [dict(post_id="800", comment_id=str(i), text=text, level="sub" if i else "top")
                for i, text in enumerate(("好\n好", "好 好", ""))]
    (source / "comments.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in comments), encoding="utf-8")
    (source / "report.json").write_text(json.dumps(dict(status="completed", totals=dict(comments=3))), encoding="utf-8")
    before = {p: p.read_bytes() for p in source.iterdir()}
    output, reports = tmp_path / "processed", tmp_path / "outputs"
    report = prepare_analysis(source, output, reports)
    assert report["comments"] == 3 and report["empty_comments"] == 1
    assert {p.name for p in output.iterdir()} == {"comments.csv"}
    assert len((output / "comments.csv").read_bytes().splitlines()) == 4
    with (output / "comments.csv").open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        assert reader.fieldnames == ["post_id", "comment"]
        rows = list(reader)
    assert rows == [{"post_id": "800", "comment": "好 好"},
                    {"post_id": "800", "comment": "好 好"},
                    {"post_id": "800", "comment": ""}]
    assert all(p.read_bytes() == data for p, data in before.items())
    assert (reports / "comments_preparation.json").is_file()
    existing = output / "comments.csv"
    old_stat = existing.stat()
    result = prepare_analysis(source, output, reports)
    assert result["reused"]
    assert existing.stat().st_mtime_ns == old_stat.st_mtime_ns
    assert not (output / "comments.csv.tmp").exists()

    # 不同结果必须显式更新；未指定时完整保留已有表。
    old_content = existing.read_bytes()
    comments[0]["text"] = "新评论"
    (source / "comments.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in comments), encoding="utf-8")
    with pytest.raises(FileExistsError, match="--overwrite"):
        prepare_analysis(source, output, reports)
    assert existing.read_bytes() == old_content
    result = prepare_analysis(source, output, reports, overwrite=True)
    assert not result["reused"]
    with existing.open(encoding="utf-8", newline="") as file:
        assert next(csv.DictReader(file))["comment"] == "新评论"


def test_refuses_failed_snapshot(tmp_path):
    (tmp_path / "report.json").write_text('{"status":"interrupted"}', encoding="utf-8")
    with pytest.raises(ValueError, match="成功快照"):
        prepare_analysis(tmp_path, tmp_path / "analysis", tmp_path / "outputs")
