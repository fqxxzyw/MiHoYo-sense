# train / dev / test 划分

入口：`scripts/07_split_dataset.py`；实现：`src/hoyo_sense/modeling/splitting.py`。
默认读取 `data/processed/miyoushe/comments_clean.csv`。

```powershell
python scripts/07_split_dataset.py
python scripts/07_split_dataset.py --ratios 8 1 1 --seed 42
```

比例针对帖子数量。先对排序后的唯一 `post_id` 使用独立的 `random.Random(seed)` 打乱，
再按比例分配帖子，把该帖全部评论写入所属集合。不会随机打散同帖评论或改变文字。
评论条数比例会受大帖影响，不保证也为 8:1:1。
使用整数分配和余数调整，使帖子数总和保持不变；至少需要三个帖子，每个集合至少有一个帖子。

## 输出

| 文件 | 内容 |
| --- | --- |
| `data/splits/train.csv` | 训练集，只保留 post_id、comment |
| `data/splits/dev.csv` | 验证集，同上 |
| `data/splits/test.csv` | 最终测试集，同上 |
| `data/splits/split_manifest.csv` | post_id、split 两列，固定每个帖子的归属 |
| `outputs/splitting/miyoushe/split_report.json` | 输入与各集哈希、随机种子、比例、帖子与评论数量、重复文本检查 |
| `outputs/splitting/miyoushe/cross_split_duplicates.csv` | 跨集合相同文本及其在 train/dev/test 的出现次数 |

可用 `--config`、`--input`、`--output-dir`、`--report-dir` 修改配置。相对路径基于项目根目录。
重复运行相同数据和参数会校验并复用一致结果。数据、参数变化导致划分不同时，默认保留原文件并提示；
尚未开始标注时，可显式加 `--overwrite` 更新，或指定新的数据目录和报告目录。

## 标注与评估

先固定划分，再从 train 抽样试标、统一情感标准，然后正式标注。dev 用于调参，test 最后评估。
人工标注主数据保存在 `data/annotated`；当前三个 CSV 尚未有情感标签。
正式标注后保留本次 split_manifest，不应通过反复换 seed 挑选更好的测试结果。

按帖子划分保证同一帖的顶层和楼中楼不会跨集合，但仍可能有跨帖复制文案、抽奖模板和“期待”等短句。
脚本只报告跨集合完全相同文本，不自动丢弃它们，也不声称已经排除所有文本泄漏。
标注前复核重复清单，区分普通短句和复制模板，再确定评估口径。近似重复、同一事件不同帖的相关性尚未自动识别。
当前没有情感标签，也未做情感类别分层。

## 2026-10-21 本地结果

源数据共 742 篇帖子、224,250 条评论，seed=42、帖子比例 8:1:1。

| 集合 | 帖子 | 评论 | 评论比例 |
| --- | ---: | ---: | ---: |
| train | 594 | 157,568 | 70.26% |
| dev | 74 | 28,226 | 12.59% |
| test | 74 | 38,456 | 17.15% |

帖子交叉为 0；跨集合相同文本共 3,497 组，已记录。11 项针对性测试通过，覆盖分组完整性、
全部行保留、可复现性、重复运行、不同划分保护和非法输入。
