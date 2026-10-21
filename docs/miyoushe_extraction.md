# 从现有米游社 JSON 开始处理

本阶段先使用现有米游社原神、星铁、绝区零数据。B站和抖音保留已有代码与样本，暂缓新增采集。
不要求等整批采集结束才开始分析，也不能把本地已有样本称为全站完整数据。

## 目的与输入输出

接口 JSON 是嵌套结构，帖子正文在 `data.post.post`，评论在 `data.list[].reply`。
直接把所有 JSON 混在一起读取，会把发现列表、断点、旧采样及重复预览混入分析。

`scripts/04_extract_miyoushe.py` 仅查找 `full_collection/manifest.json`，按照每个断点登记的文件读取、
核验已记录的 SHA256、校验帖子和楼层归属。详情可用时输出帖子；分页和内嵌回复预览输出评论。
旧日期采样、发现列表、错误响应和其他平台数据均不参与本次提取。

输出目录默认为配置中的 `processed_dir/miyoushe/<UTC时间>/`，每次运行建立一个新目录。
不会覆盖旧快照或改动 raw，也不会更新采集断点、请求网络或导入采集入口。

| 文件 | 用途 |
| --- | --- |
| `posts.jsonl` | 每帖一行，正文、元信息和采集状态 |
| `comments.jsonl` | 每个游戏+帖子+评论 ID 一行，原文、轻量整理文本及来源 |
| `posts.csv`、`comments.csv` | 便于浏览的主要平面字段，使用整理后的文字；原文、复杂来源和图片元信息请读 JSONL |
| `comments_flat.csv` | 顶层与楼中楼回复各占一个物理行，通过 level 和 root_comment_id 保留层级；内部换行写成字面量 `\n`，反斜杠写成 `\\` |
| `collection_runs.jsonl` | 每个单帖断点的版本哈希、状态、处理页数、排除的待处理页和关联检查 |
| `report.json` | 分游戏计数、采集状态、空文本、重复 ID、预览独有回复等质量统计 |

`report.json.status=completed` 表示提取成功；`interrupted` 目录只是部分结果，不应作为完整分析输入。
没有找到断点或输出目录已存在会报错。输出目录不能与 raw 目录相同或互相包含。

## 主要字段与处理规则

| 字段 | 含义 |
| --- | --- |
| `platform`、`game`、`post_id` | 平台、游戏和帖子关联键 |
| `title` | 帖子标题 |
| `comment_id`、`author_id` | 评论和平台内作者 ID，输出字符串，避免长整数精度丢失 |
| `text_raw`、`text` | 原始文字与轻量整理后的文字 |
| `text_source` | 帖子正文来源：普通 content、HTML content 或结构化正文回退 |
| `root_comment_id` | 楼中楼所属顶层评论 ID；顶层为 null，不等同于直接回复对象 |
| `floor_id` | 采集时记录的楼层 ID |
| `raw_f_reply_id`、`replied_to_user_id` | 原始 f_reply_id、r_uid；不据此补造不存在的回复链 |
| `created_at`、`updated_at` | 接口原始时间戳，不擅自转换时区 |
| `like_count`、`reply_count` | 对应响应中的计数，不代表实际提取条数 |
| `images`、`image_count`、`is_empty_text` | 图片元信息及空文字标志；图片评论仍然保留 |
| `collection_status`、`collection_completed` | 该帖提取时的采集状态，提取成功不等于采集完整 |
| `sources` | 评论出现的原始文件、JSON Pointer、来源类型、采集时间 |
| `embedded_only` | 仅在内嵌预览中出现、尚无独立分页记录 |
| `occurrence_count`、`has_text_variants` | 同 ID 出现次数，以及原文是否在不同响应中发生变化 |

1. 每帖先读完整评论页，再读内嵌楼中楼预览。相同 ID 取第一个完整分页版本；没有完整分页才用预览。
2. 按游戏+帖子+评论 ID 合并重复出现，保留全部来源。不同 ID 的相同文字只计数，不删除。
3. 帖子 HTML 去标签，保留段落、图片 alt 和实体文字；普通评论按纯文本处理，保留 `<3` 等文字。
4. 单独生成 `text`：Unicode NFC、控制字符和空白整理。保留原始 `text_raw`、表情、标点、数字、URL 和否定词。
5. 正文为空时尝试读取结构化正文中的文字 insert；图片和视频内容不自动转写，不做 OCR。
6. 不设置停用词、不分词、不生成情感或主题标签；先了解数据分布再决定建模清洗策略。

每帖只在内存中合并本帖评论，随后写出，避免一次性载入全部评论。正文和评论 ID、楼层或哈希校验不通过时停止，
不会静默跳过坏页。哈希校验复用已有 CRLF/LF 兼容逻辑，仍拒绝其他内容变更。

## 运行与检查

在项目根目录使用 `hoyosense` 环境：

```powershell
python scripts/04_extract_miyoushe.py
python scripts/04_extract_miyoushe.py --game genshin
python scripts/04_extract_miyoushe.py --raw-root data/raw/miyoushe --output-dir data/processed/miyoushe/my_snapshot
# 已有成功快照只补生成单行评论表，不重复提取 raw。
python scripts/04_extract_miyoushe.py --flatten-snapshot data/processed/miyoushe/20261021_122801_976711_UTC
python -m pytest -q tests/test_miyoushe_extraction.py
```

相对路径基于项目根目录。配置仍由 `configs/miyoushe.json` 管理，无需修改采集脚本或批次调度模块。
CSV 为 UTF-8；Excel 使用“从文本/CSV”导入，将各 ID 列设置为文本。数据分析优先读取 JSONL。
部分原始评论含 NUL 等隐藏控制字符，会使 Python 3.10 CSV 导出失败，因此 `text_raw` 仅存于 JSONL，CSV 使用 `text`。

包含 completed、limited、interrupted 等帖子现有的已登记成功页，保留状态供后续筛选。
详情不可用的任务仍在覆盖记录中；没有成功详情时不会伪造帖子正文。
`pending_request` 对应的 raw 即使已落盘，只要尚未登记到 pages，就只报告而不导入。
需要先由原采集器恢复、核验登记，再生成新快照。

如果采集仍在进行，本次逐帖读取各自的一份断点快照；提取过程中及之后新增的页可能不属于该快照。
本次不停止任何已有采集进程，也不重新启动采集。后续再运行提取即可重新生成完整本地快照。

## 后续分析顺序

先查看 `report.json` 按游戏的帖子数、顶层/楼中楼数量、空文本、图片评论、重复文字、预览独有回复和采集状态。
再抽样读 `posts.jsonl`、`comments.jsonl`，按研究目标建立文本分析子集，例如单独标记空文字、区分官方资讯与首页来源。
划分训练与测试时优先按帖子或事件分组，防止同帖评论或相同文本跨集合造成泄漏。
当前步骤只完成结构化与基础质量统计，主题探索、人工情感标注、分类模型和推荐评估后续推进。

## 本地首轮结果（2026-10-21）

成功快照为 `data/processed/miyoushe/20261021_122801_976711_UTC/`。
共核验 743 个单帖断点、45,532 个已登记分页，导出 742 篇帖子和 275,570 条不同评论 ID。

| 游戏 | 帖子 | 评论（含楼中楼） |
| --- | ---: | ---: |
| 原神 | 132 | 165,184 |
| 星穹铁道 | 414 | 85,177 |
| 绝区零 | 196 | 25,209 |

采集覆盖中有 739 个 completed、3 个 interrupted、1 个 skipped_unavailable；最后一项没有成功详情，因此少一篇帖子。
正文为空的帖子 37 篇；空文本评论 2,649 条，含图片评论 29,544 条。以上记录均保留。
内嵌预览独有评论 58 条，缺失顶层关联和待登记 raw 排除数均为 0。
分页与预览共合并 31,644 次重复 ID 出现；不同 ID 的相同非空文本有 8,610 组，额外重复记录 85,226 条，只报告、不删除。

10 项提取测试通过。独立回读 JSONL 和 CSV 后，两类表行数与 report 一致，ID 类型及组合键唯一性检查通过。
首次运行曾因原文 NUL 字符导致 CSV 导出失败；修复后重新生成上述成功快照。
首次失败目录 `20261021_122603_870037_UTC/` 因自动审批阻止清理而保留，report 标记为 interrupted，请勿用于完整分析。

后续补充 `comments_flat.csv`：182,968 条顶层评论和 92,602 条楼中楼回复均独立成行，
共 275,570 条数据，加表头为 275,571 个物理行。单行布局与层级保留测试加入后，提取相关 12 项测试通过。
