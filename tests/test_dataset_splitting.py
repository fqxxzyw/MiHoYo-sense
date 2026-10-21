import csv
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hoyo_sense.modeling.splitting import allocate_posts, split_comments


def source_data(tmp_path, posts=30):
    path = tmp_path / "input.csv"
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["post_id", "comment"])
        for index in range(posts):
            writer.writerow([str(800 + index), "共同文本"])
            for child in range(index % 3 + 1):
                writer.writerow([str(800 + index), f"楼中楼 {index}-{child}"])
    return path


def read_rows(path):
    with path.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        assert reader.fieldnames == ["post_id", "comment"]
        return list(reader)


def test_posts_do_not_overlap_all_comments_preserved_and_repeatable(tmp_path):
    source = source_data(tmp_path)
    before = source.read_bytes()
    output, reports = tmp_path / "splits", tmp_path / "reports"
    result = split_comments(source, output, reports)
    assert result["rows"] == len(read_rows(source))
    assert [result["splits"][name]["posts"] for name in ("train", "dev", "test")] == [24, 3, 3]
    all_rows, post_sets = [], []
    for name in ("train", "dev", "test"):
        rows = read_rows(output / f"{name}.csv")
        all_rows.extend((row["post_id"], row["comment"]) for row in rows)
        post_sets.append({row["post_id"] for row in rows})
    assert not post_sets[0] & post_sets[1] and not post_sets[0] & post_sets[2] and not post_sets[1] & post_sets[2]
    assert sorted(all_rows) == sorted((row["post_id"], row["comment"]) for row in read_rows(source))
    assert result["cross_split_duplicate_text_groups"] == 1
    assert source.read_bytes() == before
    times = {path: path.stat().st_mtime_ns for path in output.glob("*.csv")}
    rerun = split_comments(source, output, reports)
    assert rerun["reused"] and all(path.stat().st_mtime_ns == time for path, time in times.items())
    # 已开始使用的划分不能被不同种子静默覆盖。
    with pytest.raises(FileExistsError, match="--overwrite"):
        split_comments(source, output, reports, seed=1)
    assert all(path.stat().st_mtime_ns == time for path, time in times.items())
    changed = split_comments(source, output, reports, seed=1, overwrite=True)
    assert not changed["reused"]


def test_allocation_is_independent_of_source_order_and_does_not_touch_global_random():
    import random

    random.seed(100)
    state = random.getstate()
    mapping, _ = allocate_posts([str(i) for i in range(742)])
    assert mapping == allocate_posts([str(i) for i in reversed(range(742))])[0]
    assert random.getstate() == state
    assert [list(mapping.values()).count(name) for name in ("train", "dev", "test")] == [594, 74, 74]


@pytest.mark.parametrize("count", [3, 4, 5, 6])
def test_small_datasets_still_have_three_nonempty_splits(count):
    mapping, _ = allocate_posts([str(i) for i in range(count)])
    assert set(mapping.values()) == {"train", "dev", "test"}


@pytest.mark.parametrize("ratios", [(0, 1, 1), (-1, 1, 1), (8, float("nan"), 1), (8, 1)])
def test_invalid_ratios_fail(ratios):
    with pytest.raises(ValueError):
        allocate_posts(["1", "2", "3"], ratios)


def test_invalid_or_insufficient_input_does_not_publish_splits(tmp_path):
    source = source_data(tmp_path, posts=2)
    with pytest.raises(ValueError, match="三个"):
        split_comments(source, tmp_path / "splits", tmp_path / "reports")
    source.write_text("post_id,comment\n800,\n", encoding="utf-8")
    with pytest.raises(ValueError, match="为空"):
        split_comments(source, tmp_path / "splits", tmp_path / "reports")
    assert not (tmp_path / "splits/train.csv").exists()
