"""按帖子分组划分未标注评论，固定随机种子并审计跨集合重复文本。"""

from collections import Counter
from contextlib import ExitStack
import csv
import filecmp
import math
from pathlib import Path
import random
import tempfile

from ..storage import digest, run_lock, save_json, utc_now


NAMES = ("train", "dev", "test")
FIELDS = ("post_id", "comment")


def read_comments(path):
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file, strict=True)
        if reader.fieldnames != list(FIELDS):
            raise ValueError("输入必须只有 post_id、comment 两列。")
        for number, row in enumerate(reader, 1):
            if None in row or any(row.get(key) is None for key in FIELDS):
                raise ValueError(f"第 {number} 条评论字段数量不正确。")
            post_id, comment = row["post_id"], row["comment"]
            if not post_id.isascii() or not post_id.isdecimal() or not post_id.strip("0"):
                raise ValueError(f"第 {number} 条帖子 ID 无效；请先运行清洗。")
            if not comment.strip() or "\n" in comment or "\r" in comment:
                raise ValueError(f"第 {number} 条评论为空或未整理换行；请先运行清洗。")
            yield row


def allocate_posts(post_ids, ratios=(8, 1, 1), seed=42):
    if (len(ratios) != 3 or any(isinstance(x, bool) or not isinstance(x, (int, float))
                              or not math.isfinite(x) or x <= 0 for x in ratios)
            or not math.isfinite(sum(ratios))):
        raise ValueError("划分比例必须是三个正的有限数，例如 8 1 1。")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("随机种子必须是整数。")
    ids = sorted(set(post_ids))
    if len(ids) < 3:
        raise ValueError("至少需要三个不同帖子才能划分 train/dev/test。")
    fractions = [value / sum(ratios) for value in ratios]
    quotas = [len(ids) * fraction for fraction in fractions]
    sizes = [math.floor(quota) for quota in quotas]
    remainder = len(ids) - sum(sizes)
    for index in sorted(range(3), key=lambda i: (-(quotas[i] - sizes[i]), i))[:remainder]:
        sizes[index] += 1
    # 小样本也保证三个集合都有帖子。
    for index in range(3):
        if sizes[index] == 0:
            donor = max((i for i in range(3) if sizes[i] > 1), key=lambda i: sizes[i] - quotas[i])
            sizes[donor] -= 1
            sizes[index] += 1
    random.Random(seed).shuffle(ids)
    mapping, start = {}, 0
    for name, size in zip(NAMES, sizes):
        for post_id in ids[start:start + size]:
            mapping[post_id] = name
        start += size
    return mapping, fractions


def split_comments(input_path, output_dir, report_dir, *, ratios=(8, 1, 1), seed=42, overwrite=False):
    input_path, output_dir, report_dir = (Path(p).resolve() for p in (input_path, output_dir, report_dir))
    filenames = [name + ".csv" for name in NAMES] + ["split_manifest.csv"]
    if any(input_path == (output_dir / name).resolve() for name in filenames):
        raise ValueError("划分输出不能覆盖输入文件。")
    if report_dir == output_dir or report_dir in input_path.parents or report_dir in output_dir.parents:
        raise ValueError("报告目录必须与划分数据分开。")
    source_sha = digest(input_path)
    post_counts = Counter(row["post_id"] for row in read_comments(input_path))
    mapping, fractions = allocate_posts(post_counts, ratios, seed)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    counts = Counter({name: 0 for name in NAMES})
    text_counts = {}
    with run_lock(report_dir), tempfile.TemporaryDirectory(prefix=".split-", dir=output_dir) as staging_name:
        staging = Path(staging_name)
        with ExitStack() as stack:
            writers = {}
            for name in NAMES:
                file = stack.enter_context((staging / (name + ".csv")).open("w", encoding="utf-8", newline=""))
                writers[name] = csv.DictWriter(file, fieldnames=FIELDS)
                writers[name].writeheader()
            for row in read_comments(input_path):
                name = mapping.get(row["post_id"])
                if name is None:
                    raise ValueError("两次读取间输入帖子发生变化，本次不发布。")
                writers[name].writerow(row)
                counts[name] += 1
                occurrences = text_counts.setdefault(row["comment"], [0, 0, 0])
                occurrences[NAMES.index(name)] += 1
        if digest(input_path) != source_sha or sum(counts.values()) != sum(post_counts.values()):
            raise ValueError("划分期间输入文件发生变化，本次不发布。")
        with (staging / "split_manifest.csv").open("w", encoding="utf-8", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(["post_id", "split"])
            writer.writerows((post_id, mapping[post_id]) for post_id in sorted(mapping))
        changed = [name for name in filenames if (output_dir / name).exists()
                   and not filecmp.cmp(staging / name, output_dir / name, shallow=False)]
        if changed and not overwrite:
            raise FileExistsError("已有划分与本次结果不同，已保留原文件。请使用新的 --output-dir、--report-dir，"
                                  "或确认尚未开始标注后加 --overwrite：" + ", ".join(changed))
        leakage_groups, affected = 0, Counter({name: 0 for name in NAMES})
        # 重复文字单独报告，不破坏已确定的帖子分组或静默删除 dev/test 样本。
        with (staging / "cross_split_duplicates.csv").open("w", encoding="utf-8", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(["comment", "train_rows", "dev_rows", "test_rows"])
            for text, numbers in text_counts.items():
                if sum(number > 0 for number in numbers) > 1:
                    leakage_groups += 1
                    affected.update(dict(zip(NAMES, numbers)))
                    writer.writerow([text, *numbers])
        groups = Counter(mapping.values())
        total_rows = sum(counts.values())
        report = dict(schema_version=1, status="completed", finished_at=utc_now(), input_file=str(input_path),
                      input_sha256=source_sha, seed=seed, columns=list(FIELDS), group_key="post_id",
                      requested_post_ratios=dict(zip(NAMES, fractions)), posts=len(mapping), rows=total_rows,
                      post_overlap=0, cross_split_duplicate_text_groups=leakage_groups,
                      rows_with_cross_split_duplicate_text=dict(affected),
                      splits={name: dict(posts=groups[name], rows=counts[name],
                              post_fraction=groups[name] / len(mapping), row_fraction=counts[name] / total_rows,
                              file=str(output_dir / (name + ".csv")), sha256=digest(staging / (name + ".csv")))
                              for name in NAMES},
                      notes=["按帖子分组，比例针对帖子数；评论行数比例受帖子大小影响。",
                             "未使用情感标签分层；数据尚未标注。",
                             "所有行均保留，未根据跨集合重复文本自动删除样本；标注前检查重复清单。",
                             "从train中试标并确定标准；dev用于调参，test用于最终评估。",
                             "开始标注后固定本次split_manifest.csv和seed，不反复更换测试集。"])
        reused = all((output_dir / name).exists() and filecmp.cmp(staging / name, output_dir / name, shallow=False)
                     for name in filenames)
        for name in filenames:
            if not (output_dir / name).exists() or not filecmp.cmp(staging / name, output_dir / name, shallow=False):
                (staging / name).replace(output_dir / name)
        (staging / "cross_split_duplicates.csv").replace(report_dir / "cross_split_duplicates.csv")
        report["reused"] = reused
        save_json(report, report_dir / "split_report.json")
        return report
