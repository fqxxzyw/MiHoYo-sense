"""清洗语义、去重边界、审计、幂等运行及坏输入下的成品保护。"""

import csv
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hoyo_sense.preprocessing.cleaning import clean_comments, clean_text


def write_input(tmp_path, rows):
    source = tmp_path / "input.csv"
    with source.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["post_id", "comment"])
        writer.writerows(rows)
    return source


def read_csv(path):
    with path.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        assert reader.fieldnames == ["post_id", "comment"]
        return list(reader)


def test_removes_empty_and_same_post_duplicates_preserves_cross_post_and_sentiment(tmp_path):
    source = write_input(tmp_path, [("800", "  "), ("800", "<p><br></p>"),
        ("800", "好\n耶"), ("800", "好 耶"), ("801", "好 耶"), ("800", "差"),
        ("800", "👩‍💻🥰"), ("800", "不喜欢！！！"), ("800", "!!!"), ("800", "求中奖"),
        ("800", "https://example.test"), ("800", "加微信低价代充"), ("800", "坏\ufffd字")])
    before = source.read_bytes()
    output, reports = tmp_path / "processed/comments_clean.csv", tmp_path / "outputs"
    report = clean_comments(source, output, reports)
    result = read_csv(output)
    assert report["counts"]["input_rows"] == 13
    assert report["counts"]["empty_removed"] == 2
    assert report["counts"]["duplicate_rows_removed"] == 1
    assert len(result) == 10 and report["counts"]["output_rows"] == 10
    assert {row["comment"] for row in result} >= {"差", "👩‍💻🥰", "不喜欢！！！", "!!!", "求中奖", "坏\ufffd字"}
    assert sum(row["comment"] == "好 耶" for row in result) == 2
    assert len(output.read_bytes().splitlines()) == 11
    assert source.read_bytes() == before
    audit = [json.loads(line) for line in (reports / "anomalies.jsonl").read_text(encoding="utf-8").splitlines()]
    duplicate = next(row for row in audit if row["action"] == "duplicate_removed")
    assert duplicate["record_number"] == 4 and duplicate["first_record_number"] == 3
    with (reports / "duplicate_groups.csv").open(encoding="utf-8", newline="") as file:
        groups = list(csv.DictReader(file))
    assert groups == [dict(post_id="800", comment="好 耶", occurrences="2", first_record_number="3")]
    assert report["anomaly_counts"]["suspected_advertisement"] == 1
    assert report["anomaly_counts"]["prize_interaction"] == 1
    assert report["anomaly_counts"]["suspected_encoding_damage"] == 1
    assert (reports / "EDA.md").exists()
    old_mtime = output.stat().st_mtime_ns
    rerun = clean_comments(source, output, reports)
    assert rerun["reused"] and output.stat().st_mtime_ns == old_mtime


def test_none_mode_preserves_duplicate_frequencies(tmp_path):
    source = write_input(tmp_path, [("800", "期待"), ("800", "期待"), ("801", "期待")])
    output = tmp_path / "processed/comments_clean.csv"
    report = clean_comments(source, output, tmp_path / "reports", dedup="none")
    assert len(read_csv(output)) == 3
    assert report["counts"]["duplicate_rows_detected"] == 1
    assert report["counts"]["duplicate_rows_removed"] == 0


def test_invalid_ids_and_malformed_rows_are_quarantined(tmp_path):
    source = write_input(tmp_path, [("", "空ID"), ("8.0", "浮点ID"), ("8e3", "科学记数"),
                                    ("0", "零ID"), ("800",), ("800", "多列", "额外列"),
                                    (" 2110605758023241729 ", "保留长ID")])
    output = tmp_path / "processed/comments_clean.csv"
    report = clean_comments(source, output, tmp_path / "reports")
    assert report["counts"]["invalid_id_removed"] == 4
    assert report["counts"]["malformed_rows_removed"] == 2
    assert read_csv(output) == [dict(post_id="2110605758023241729", comment="保留长ID")]


def test_markup_entities_controls_and_length_flags_preserve_meaning():
    text, flags = clean_text('<p>不喜欢<b>这个</b>！</p><p>&amp; 👩‍💻\x00\u200b</p>')
    assert text == "不喜欢这个！ & 👩‍💻"
    assert set(flags) >= {"html_tags", "html_entities", "control_characters", "invisible_characters"}
    assert clean_text("角色A <3")[0] == "角色A <3"
    assert "short_text" in clean_text("好")[1]
    assert "long_text" in clean_text("很" * 20, long_length=10)[1]
    assert "repeated_characters" in clean_text("哈" * 10)[1]
    assert clean_text("e\u0301")[0] == "é"
    assert clean_text('<img src="test.png" alt="🥰">')[0] == "🥰"
    text, flags = clean_text('<img class="custom-emoticon-image" src="test.gif">')
    assert text == "" and "image_only_no_text" in flags


@pytest.mark.parametrize("content", ['bad,header\n800,test\n', 'post_id,comment\n800,"未闭合'])
def test_unreadable_csv_does_not_publish_or_replace_existing_output(tmp_path, content):
    source = tmp_path / "input.csv"
    source.write_text(content, encoding="utf-8")
    output = tmp_path / "processed/comments_clean.csv"
    output.parent.mkdir()
    output.write_text("old result", encoding="utf-8")
    with pytest.raises((ValueError, csv.Error)):
        clean_comments(source, output, tmp_path / "reports")
    assert output.read_text(encoding="utf-8") == "old result"
    assert not (tmp_path / "reports/cleaning_report.json").exists()
    assert not list(output.parent.glob("*.tmp"))


def test_empty_result_is_a_valid_two_column_csv(tmp_path):
    source = write_input(tmp_path, [("800", "\u200b"), ("800", "&nbsp;")])
    output = tmp_path / "processed/comments_clean.csv"
    report = clean_comments(source, output, tmp_path / "reports")
    assert report["counts"]["output_rows"] == 0 and read_csv(output) == []
    assert report["after"]["text_length"]["median"] is None


def test_cannot_overwrite_input_or_use_invalid_thresholds(tmp_path):
    source = write_input(tmp_path, [("800", "内容")])
    with pytest.raises(ValueError, match="覆盖输入"):
        clean_comments(source, source, tmp_path / "reports")
    with pytest.raises(ValueError, match="长度阈值"):
        clean_comments(source, tmp_path / "clean.csv", tmp_path / "reports", long_length=0)


def test_changed_input_updates_only_derived_result(tmp_path):
    source = write_input(tmp_path, [("800", "旧评论")])
    output = tmp_path / "processed/comments_clean.csv"
    clean_comments(source, output, tmp_path / "reports")
    source = write_input(tmp_path, [("800", "新评论")])
    report = clean_comments(source, output, tmp_path / "reports")
    assert not report["reused"] and read_csv(output) == [dict(post_id="800", comment="新评论")]
