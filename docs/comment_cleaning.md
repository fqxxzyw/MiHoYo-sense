# 两列评论清洗

入口：`scripts/06_clean_comments.py`。规则实现：`src/hoyo_sense/preprocessing/cleaning.py`。
输入、成品只使用 `post_id,comment` 两列，顶层和楼中楼按相同规则处理，每条评论独占一行。

## 文件位置

| 文件 | 用途 |
| --- | --- |
| `data/processed/miyoushe/comments.csv` | 第 05 步生成的输入，本步骤只读 |
| `data/processed/miyoushe/comments_clean.csv` | 去空并按同帖相同文字去重后的两列成品 |
| `outputs/cleaning/miyoushe/cleaning_report.json` | 输入/输出哈希、参数、计数、长度分布和清洗规则 |
| `outputs/cleaning/miyoushe/anomalies.jsonl` | 被删除或标记记录的原文、记录序号、原因和实际动作 |
| `outputs/cleaning/miyoushe/duplicate_groups.csv` | 同帖重复文本的原始出现次数，用于复核及恢复统计权重 |
| `outputs/cleaning/miyoushe/EDA.md` | 清洗前后数量、长度、帖子覆盖和异常统计 |

`data` 根目录继续只用 `raw`、`processed`、`annotated`、`splits` 四个目录。
本步骤不改写 raw、原提取快照或输入 CSV，不创建标注数据和训练集，也不生成情感标签。

## 运行

```powershell
# 默认读取当前两列表并清洗。
python scripts/06_clean_comments.py

# 修改长度标记阈值；仍只标记，不根据长度删除。
python scripts/06_clean_comments.py --short-length 2 --long-length 2000

# 情感比例统计用的频次保留版本：去空，但保留相同文字的不同评论。
python scripts/06_clean_comments.py --dedup none --output data/processed/miyoushe/comments_clean_all.csv --report-dir outputs/cleaning/miyoushe_all
```

支持 `--config`、`--input`、`--output`、`--report-dir`。相对路径统一基于项目根目录。
重复运行会重新检查输入；成品一致时复用，不同则更新派生的清洗成品。输出不能与输入为同一文件。
表头不正确、CSV 引号损坏、编码无法读取或输入在处理中发生变化时停止，不发布本轮清洗结果。

## 执行规则

1. 校验输入列必须恰好为 `post_id,comment`。字段数量不对的行移入异常审计，不混入成品。
2. 去除 ID 两端空白，保持字符串。缺失、零、非 ASCII 数字、浮点或科学记数 ID 移入异常审计，不猜测修复。
3. 整理 HTML 标签、HTML 实体、控制字符、零宽空格和普通空白，Unicode 使用 NFC。
   保留否定词、单字评价、表情、emoji 组合中的 ZWJ、标点、数字和 URL。
4. 整理后没有文字的记录移入异常审计，记为去空。不会把“好”“差”“🥰”“!!!”当成空文本。
   HTML 图片有 alt 文字时保留 alt；没有 alt 的纯图片/自定义表情图片标为 `image_only_no_text`，
   不进入纯文本成品，原内容及 URL 保存在异常审计。此类图像可能表达情感，需要查看原图复核，不能自动当成中性。
5. 默认 `--dedup post`：按帖子 ID＋整理后的完整评论去重，保留第一次出现。
   不同帖子下相同文字继续保留，不做模糊去重，也不把不同文字中相似的观点合并。
6. 广告嫌疑、抽奖互动、编码损坏嫌疑、长短文本、重复字符、纯标点和纯 URL 只标记、保留。
   这些规则可能命中正常留言，不能当成可靠的垃圾评论分类器。

`anomalies.jsonl` 的 `record_number` 是从 1 开始的输入数据记录序号，不包括表头；
`physical_line_end` 是 CSV 解析器读到的物理行号。`action` 分为 kept、removed、duplicate_removed。
一条记录可能有多个原因，因此各原因计数之和不一定等于异常记录数。

删除计数按顺序互斥：

```text
输入 = 格式异常删除 + 无效ID删除 + 空文本删除 + 同帖重复删除 + 清洗后保留
```

长度统计只针对非空有效文本，分位数使用 nearest-rank；清洗前的全体输入行数另列。
清洗后某些帖子可能因仅有空评论而不再出现，报告会显示前后帖子数量。

## 去重与情感比例的区别

同一帖子下“期待”等文字可能来自不同用户。两列输入已不包含作者和评论 ID，因此这里的去重
是用于文本建模的文本去重，不能据此认定原始评论是重复采集或机器人留言。

训练时可先使用默认的去重成品。统计评论情感比例时，使用 `--dedup none` 的频次版本，
或将默认成品与 `duplicate_groups.csv` 按 `post_id,comment` 关联，以 occurrences 恢复权重；
未出现在重复清单中的文本权重为 1。使用 none 版本时不要再重复加权。
后续应对保留频次与去重两套口径进行对照，不将清洗后比例直接称为全部玩家态度。

当前规则没有删除广告/抽奖嫌疑行，没有做自动拼写纠正、分词或停用词过滤。
先复核异常记录，再决定是否增加明确的过滤规则。
