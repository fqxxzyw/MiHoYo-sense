"""两列评论表清洗：去空、同帖文本去重、异常审计及清洗前后 EDA。"""

from collections import Counter
import csv
import filecmp
import html
import json
import math
from pathlib import Path
import re
import tempfile
import unicodedata

from .miyoushe import normalize_text
from ..storage import digest, run_lock, save_json, utc_now


FIELDS = ("post_id", "comment")
HTML_TAG = re.compile(r"</?(?:p|div|span|br|img|a|script|style|ul|ol|li)\b[^>]*>", re.I)
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
INVISIBLE = re.compile("[\u200b\ufeff\u2060]")  # 保留 emoji 中的 ZWJ / 变体选择符。
URL = re.compile(r"https?://[^\s]+", re.I)
PRIZE = re.compile(r"求中奖|求中|接好运|希望中奖|参与抽奖|抽我|求欧|蹲中奖")
AD = re.compile(r"加[微薇][信]?|[vV][xX][：:\s]|兼职日结|低价代充|出售账号|买号联系")
REPEATED = re.compile(r"(.)\1{7,}")


def clean_text(raw, *, short_length=2, long_length=2000):
    flags = []
    if CONTROL.search(raw):
        flags.append("control_characters")
    if INVISIBLE.search(raw):
        flags.append("invisible_characters")
    value = INVISIBLE.sub("", CONTROL.sub("", raw))
    decoded = html.unescape(value)
    if decoded != value:
        flags.append("html_entities")
    is_html = bool(HTML_TAG.search(decoded))
    if is_html:
        flags.append("html_tags")
    has_image = is_html and bool(re.search(r"<img\b", decoded, re.I))
    if has_image:
        flags.append("embedded_image")
    text = " ".join(normalize_text(decoded, html=is_html).split())
    if has_image and not text:
        flags.append("image_only_no_text")
    if text and len(text) < short_length:
        flags.append("short_text")
    if len(text) > long_length:
        flags.append("long_text")
    if "\ufffd" in text or "锟斤拷" in text:
        flags.append("suspected_encoding_damage")
    if URL.search(text) and not URL.sub("", text).strip(" .,，。;；!！?？"):
        flags.append("url_only")
    if AD.search(text):
        flags.append("suspected_advertisement")
    if PRIZE.search(text):
        flags.append("prize_interaction")
    if REPEATED.search(text):
        flags.append("repeated_characters")
    if text and all(unicodedata.category(char)[0] in ("P", "Z") for char in text):
        flags.append("punctuation_only")
    return text, flags


def length_summary(histogram):
    total = sum(histogram.values())
    if not total:
        return dict(count=0, min=None, median=None, p95=None, max=None, mean=None)

    def percentile(fraction):
        target, current = max(1, math.ceil(total * fraction)), 0
        for length, count in sorted(histogram.items()):
            current += count
            if current >= target:
                return length

    return dict(count=total, min=min(histogram), median=percentile(0.5), p95=percentile(0.95),
                max=max(histogram), mean=sum(n * count for n, count in histogram.items()) / total)


def clean_comments(input_path, output_path, report_dir, *, dedup="post", short_length=2, long_length=2000):
    """清洗输入不变；成品只有两列，所有删除与标记可通过输入记录序号追溯。"""
    if dedup not in ("post", "none"):
        raise ValueError("dedup 只支持 post 或 none。")
    if (isinstance(short_length, bool) or not isinstance(short_length, int) or short_length < 1
            or isinstance(long_length, bool) or not isinstance(long_length, int) or long_length < short_length):
        raise ValueError("长度阈值必须是正整数，且 long_length 不小于 short_length。")
    input_path, output_path, report_dir = (Path(p).resolve() for p in (input_path, output_path, report_dir))
    if input_path == output_path:
        raise ValueError("清洗输出不能覆盖输入文件。")
    if report_dir == output_path.parent or report_dir in input_path.parents or report_dir in output_path.parents:
        raise ValueError("报告目录必须与数据文件分开。")
    source_sha = digest(input_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    counts = Counter(input_rows=0, output_rows=0, malformed_rows_removed=0, invalid_id_removed=0,
                     empty_removed=0, originally_empty_removed=0, normalized_empty_removed=0,
                     duplicate_rows_detected=0, duplicate_rows_removed=0,
                     normalized_rows=0, anomaly_rows=0, flagged_rows_kept=0)
    flag_counts, lengths_before, lengths_after = Counter(), Counter(), Counter()
    posts_before, posts_after, texts_before, texts_after = Counter(), Counter(), Counter(), Counter()
    groups = {}
    old_csv_limit = csv.field_size_limit()
    temporary_output = None
    try:
        csv.field_size_limit(10_000_000)
        with run_lock(report_dir), tempfile.TemporaryDirectory(prefix=".cleaning-", dir=report_dir) as staging_name:
            staging = Path(staging_name)
            with input_path.open(encoding="utf-8-sig", newline="") as source, tempfile.NamedTemporaryFile(
                    "w", encoding="utf-8", newline="", dir=output_path.parent,
                    prefix=".comments_clean-", suffix=".tmp", delete=False) as target, (
                    staging / "anomalies.jsonl").open("w", encoding="utf-8") as anomalies:
                temporary_output = Path(target.name)
                reader = csv.DictReader(source, strict=True)
                if reader.fieldnames != list(FIELDS):
                    raise ValueError("输入列必须依次为 post_id、comment。")
                writer = csv.DictWriter(target, fieldnames=FIELDS)
                writer.writeheader()

                def audit(number, row, text, flags, action, first_record=None):
                    if not flags:
                        return
                    counts["anomaly_rows"] += 1
                    flag_counts.update(flags)
                    anomalies.write(json.dumps(dict(record_number=number, physical_line_end=reader.line_num,
                        original=row, cleaned_comment=text, reasons=flags, action=action,
                        first_record_number=first_record), ensure_ascii=False) + "\n")

                for number, row in enumerate(reader, 1):
                    counts["input_rows"] += 1
                    if None in row or any(row.get(key) is None for key in FIELDS):
                        counts["malformed_rows_removed"] += 1
                        audit(number, row, None, ["malformed_row"], "removed")
                        continue
                    raw_id, raw = row["post_id"], row["comment"]
                    post_id = raw_id.strip()
                    if not post_id.isascii() or not post_id.isdecimal() or not post_id.strip("0"):
                        counts["invalid_id_removed"] += 1
                        audit(number, row, None, ["invalid_post_id"], "removed")
                        continue
                    posts_before[post_id] += 1
                    text, flags = clean_text(raw, short_length=short_length, long_length=long_length)
                    if post_id != raw_id:
                        flags.append("post_id_whitespace")
                    if text != raw or post_id != raw_id:
                        counts["normalized_rows"] += 1
                    if not text:
                        counts["empty_removed"] += 1
                        counts["originally_empty_removed" if not raw.strip() else "normalized_empty_removed"] += 1
                        audit(number, row, text, flags + ["empty_text"], "removed")
                        continue
                    lengths_before[len(text)] += 1
                    texts_before[text] += 1
                    key = post_id, text
                    first = groups.get(key)
                    if first is None:
                        groups[key] = [1, number]
                    else:
                        first[0] += 1
                        counts["duplicate_rows_detected"] += 1
                        flags.append("same_post_duplicate")
                        if dedup == "post":
                            counts["duplicate_rows_removed"] += 1
                            audit(number, row, text, flags, "duplicate_removed", first[1])
                            continue
                    writer.writerow(dict(post_id=post_id, comment=text))
                    counts["output_rows"] += 1
                    counts["flagged_rows_kept"] += bool(flags)
                    lengths_after[len(text)] += 1
                    texts_after[text] += 1
                    posts_after[post_id] += 1
                    audit(number, row, text, flags, "kept", first[1] if first else None)
            if digest(input_path) != source_sha:
                raise ValueError("清洗期间输入文件发生变化，未发布本次结果。")
            assert counts["input_rows"] == sum(counts[name] for name in (
                "output_rows", "malformed_rows_removed", "invalid_id_removed", "empty_removed", "duplicate_rows_removed"))
            with (staging / "duplicate_groups.csv").open("w", encoding="utf-8", newline="") as file:
                writer = csv.writer(file)
                writer.writerow(["post_id", "comment", "occurrences", "first_record_number"])
                for (post_id, text), (count, first_record) in groups.items():
                    if count > 1:
                        writer.writerow([post_id, text, count, first_record])
            report = dict(schema_version=1, status="completed", finished_at=utc_now(), input_file=str(input_path),
                input_sha256=source_sha, output_file=str(output_path), output_sha256=digest(temporary_output),
                columns=list(FIELDS), dedup=dedup, short_length=short_length, long_length=long_length,
                counts=dict(counts), anomaly_counts=dict(flag_counts),
                before=dict(posts=len(posts_before), nonempty_text_rows=sum(lengths_before.values()),
                            distinct_nonempty_texts=len(texts_before), text_length=length_summary(lengths_before)),
                after=dict(posts=len(posts_after), distinct_nonempty_texts=len(texts_after),
                           text_length=length_summary(lengths_after)),
                policy=["删除格式损坏行、无效帖子ID和整理后空文本。",
                        "post模式仅合并同帖相同文本；不同帖子相同文本保留。none模式保留文本重复。",
                        "广告/抽奖/乱码嫌疑、长短文本、重复字符与纯标点仅标记，不据此删除。",
                        "保留表情、单字评价、否定词、标点、数字、URL；清理HTML、实体、控制字符和空白。",
                        "纯HTML图片/表情图片没有alt文字时不进入纯文本成品，原文及URL保留在异常审计，需看原图复核。",
                        "去重结果用于文本建模；统计原始评论情感比例时用none模式或按duplicate_groups.csv恢复权重。"],
                files=["cleaning_report.json", "anomalies.jsonl", "duplicate_groups.csv", "EDA.md"])
            lines = ["# 评论清洗与前后 EDA", "", f"输入：`{input_path}`", f"输出：`{output_path}`", "",
                "| 项目 | 条数 |", "| --- | ---: |",
                f"| 输入评论 | {counts['input_rows']} |",
                f"| 格式异常删除 | {counts['malformed_rows_removed']} |",
                f"| 无效帖子 ID 删除 | {counts['invalid_id_removed']} |",
                f"| 空文本删除 | {counts['empty_removed']} |",
                f"| 其中原来为空 | {counts['originally_empty_removed']} |",
                f"| 其中整理标签/实体等后为空 | {counts['normalized_empty_removed']} |",
                f"| 同帖重复删除 | {counts['duplicate_rows_removed']} |",
                f"| 清洗后评论 | {counts['output_rows']} |", "",
                f"清洗前后有评论的帖子数：{len(posts_before)} → {len(posts_after)}。",
                f"非空文本长度中位数：{report['before']['text_length']['median']} → {report['after']['text_length']['median']}；"
                f"P95：{report['before']['text_length']['p95']} → {report['after']['text_length']['p95']}。", "",
                "## 异常标记（可以重叠）", "", "| 标记 | 条数 |", "| --- | ---: |"]
            lines += [f"| {flag} | {count} |" for flag, count in flag_counts.most_common()]
            lines += ["", "## 清洗规则", ""] + ["- " + policy for policy in report["policy"]]
            lines += ["", "anomalies.jsonl 按输入记录序号记录原因和动作；duplicate_groups.csv 保留重复次数。",
                      "仅做规则清洗与统计，尚未生成情感标签。", ""]
            (staging / "EDA.md").write_text("\n".join(lines), encoding="utf-8")
            reused = output_path.exists() and filecmp.cmp(temporary_output, output_path, shallow=False)
            if not reused:
                temporary_output.replace(output_path)
            report["reused"] = reused
            # 报告最后发布，报告中的哈希可用于检查成品与本轮规则是否一致。
            for name in ("anomalies.jsonl", "duplicate_groups.csv", "EDA.md"):
                (staging / name).replace(report_dir / name)
            save_json(report, report_dir / "cleaning_report.json")
            return report
    finally:
        csv.field_size_limit(old_csv_limit)
        if temporary_output is not None:
            temporary_output.unlink(missing_ok=True)
