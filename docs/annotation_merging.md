# 表情标注合并与纯数字、纯符号过滤

入口为 `src/hoyo_sense/analysis/merge_annotations.py`。它读取三个集合各自的
`data/annotated/<split>_labeled.csv` 和 `<split>_labeled_v2.csv`，逐行验证帖子 ID、
评论和顺序完全一致，只允许合回米游社纯表情的 `-1 → 2` 修改。
不按帖子 ID 单独关联，避免同帖多条评论被错误合并。

2026-10-22 已将 11,868 条表情修改合回原来的三份 `*_labeled.csv`。
随后删除纯数字 1,336 条、纯标点或普通符号 1,261 条，共 2,597 条。

| 集合 | 原行数 | 合回表情数 | 删除纯数字 | 删除纯符号 | 当前行数 | 当前 -1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| train | 157568 | 9072 | 1037 | 944 | 155587 | 2098 |
| dev | 28226 | 1408 | 156 | 165 | 27905 | 466 |
| test | 38456 | 1388 | 143 | 152 | 38161 | 425 |

过滤规则在三个集合中一致，不依赖标签值：

- 判断时忽略空白，保留的正文不修改。
- 纯数字按 Unicode 数字类别判断，例如 `666`、`１２３`、`②③`；中文的“一”“六六六”保留。
- 纯标点、数学或货币等普通符号删除，例如 `？？？`、`……`、`√+$`。
- 默认保守保留 Unicode 的其他符号类 So，包含 emoji 和图形符号。
- 米游社 `_(...)` 表情保留；数字和符号混合的 `+1`、`123!!!` 也保留。

直接运行模块的方式（项目根目录，PowerShell）：

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
& "D:\Anaconda3\envs\hoyosense\python.exe" -m hoyo_sense.analysis.merge_annotations
```

`--drop-emoji` 可在首次处理时将纯 Unicode 表情、图形符号也纳入删除。
当前数据已按默认规则处理；改变规则或人工修改原表后，不要用旧 v2 强行重新合并。
只有当前输出、v2 输入和规则都未变化时，重复运行才自动复用上次合并结果。

原件逐字节备份在 `data/annotated/backups/20261022_071956_391394_UTC/`。
逐行标签修改、删除明细及统计在
`outputs/annotation_review/merge_20261022_071956_391394_UTC/`。
最新记录索引为 `outputs/annotation_review/annotation_merge_latest.json`。

`train_pending.csv`、`dev_pending.csv`、`test_pending.csv` 已刷新，
它们的 `source_row` 对应**当前合并过滤后**原标注表的数据行号（首条数据为 1）。
本次运行目录中的修改与删除记录，`source_row` 对应**备份原件**的行号。
先前的 `*_emoticons_changed.csv`、`*_pending_remaining.csv` 是过滤前的历史记录，不能按旧行号直接回填当前表。

`data/splits/` 保留原始划分；当前标注表是原划分的子集，保持原行顺序和所属集合。
三集合仍为 594 / 74 / 74 篇帖子，帖子交叉为 0。
后续读取原来的 `*_labeled.csv` 即可，公共读取代码在 `modeling/common/dataset.py`；
`modeling/dataset.py` 仍保留兼容入口，输入目录不变。
表情的中性标签来自既有规则处理，并不代表逐条人工判定。
