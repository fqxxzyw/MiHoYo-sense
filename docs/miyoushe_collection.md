# 单帖采集与断点恢复

本阶段只采集帖子 78918177 的原始 JSON，不下载图片，不清洗文本。
遍历结束仅限于当前接口、顶层热门排序及已发现楼层，不保证全部历史、删除或折叠评论。

## 输入与输出

脚本：`scripts/01_collect_miyoushe.py`。默认断点目录：
`data/raw/miyoushe/genshin/78918177/full_collection/`。
再次运行相同目录会自动恢复；指定不同的空 `--run-dir` 可以建立独立采样。

| 参数 | 含义 |
| --- | --- |
| `--max-pages` | 顶层累计页数上限 |
| `--max-sub-pages` | 每个楼层累计页数上限 |
| `--max-floors` | 按首次发现顺序处理的楼层数上限 |
| `--seed-run` | 新目录首次运行时复制导入旧格式采样 |
| `--verify-only` | 只读核验原始文件与断点，不发请求 |

三个上限均接受非负整数或 `None`，默认均为 `None`。
上限包括已保存页面；重复运行相同上限不会逐轮扩大范围，降低上限也不删数据。

## 数据流与代码

1. `initialize()` 加载断点，或从旧采样复制原始文件并记录 `seed_run` 来源。
2. `collect_post()` 核验已保存的帖子文件，已有文件不重新请求。
3. `collect_pages()` 分页获取顶层评论，使用上一页返回的游标。
4. `discover_floors()` 从已保存顶层记录发现有回复的楼层，用字典键按 `floor_id` 去重，保存 `parent_reply_id` 关联顶层评论。
5. 每个楼层复用 `collect_pages()`；只有顶层及所有楼层均结束，总体才结束。

`pages` 是按顺序保存页面记录的列表，包括请求与响应游标、文件路径、采集时间、数量和 SHA-256。
`seen_ids` 集合只统计重复，不删除 raw 中的重复评论、空文本或图片元信息。
顶层附带的楼中楼与独立采集的楼中楼均保留，后续分析表再按评论 ID 合并。

每页持久化顺序：保存 `pending_request` → 请求 → 原始 JSON 原子落盘 → 更新页列表与下一游标。
`reconcile()` 恢复时重读已保存页面，核验游标链、数量和哈希。
若原始页已落盘而断点尚未更新，用 `pending_request` 定位该页并登记，不重复请求。
文件缺失或修改会停止检查；系统文件锁防止两个采集进程同时写同一目录。

## 运行与验证

在项目根目录的 PowerShell 中执行，解释器使用已配置的 hoyosense 环境。

```powershell
# 小规模运行：本地旧采样导入后，先登记的楼层是 35 楼。
& 'D:\Anaconda3\envs\hoyosense\python.exe' -u scripts/01_collect_miyoushe.py --seed-run data/raw/miyoushe/genshin/78918177/20261018_062634_980587_UTC --max-pages 6 --max-sub-pages 3 --max-floors 1

# 无上限恢复；直接运行脚本也使用以下默认值。
& 'D:\Anaconda3\envs\hoyosense\python.exe' -u scripts/01_collect_miyoushe.py --max-pages None --max-sub-pages None --max-floors None

# 只读核验。
& 'D:\Anaconda3\envs\hoyosense\python.exe' scripts/01_collect_miyoushe.py --verify-only
```

旧采样只在本地存在，新建无旧数据的任务时省略 `--seed-run`。
旧页保留旧采集时间，新页记录新时间；这不是同一时刻的静态快照。
超时、连接异常、Ctrl+C 后可重新运行相同目录续采。没有自动重试。
若遇到异常分页或文件核验失败，先检查已保存的异常页，恢复不会自动跳过或重新请求该页。

## 状态与进度

| 状态 | 含义 | completed |
| --- | --- | --- |
| pending | 尚未开始或等待下一页 | false |
| running | 正在执行，强制退出后可从落盘断点重建 | false |
| completed | 正常遍历结束，分页任务末页明确 is_last=True | true |
| limited | 达到页数或楼层数上限 | false |
| interrupted | 请求、业务、分页、文件异常或手动中断 | false |

帖子详情任务的完成仅表示该详情已保存并核验。
`manifest.json` 中每个分页任务保存 `next_last_id`、`pages`、`pending_request` 和 `stop_reason`。
每次执行的上限、时间、状态及错误记录在 `attempts`。总体不再使用含义模糊的 finished。

已验证：13 项离线测试通过；原样本顶层 99 个不同 ID、35 楼 40 个不同 ID；真实小规模续采后分别为 119 和 60。
相同上限再次运行时禁止网络，请求数为 0，原始文件哈希不变。
小规模记录：`outputs/reports/miyoushe_resume_validation.json`。
完整遍历日志：`outputs/reports/miyoushe_full_collection.log`；最终状态以断点及后续核验报告为准。
热门排序和评论随时间变化，无重复也不证明没有遗漏。

2026-10-18 无上限遍历和核验结果：

| 对象 | 页数 | 不同评论 ID | 状态 |
| --- | --- | --- | --- |
| 顶层热门评论 | 100 | 1,950 | 末页 is_last=True |
| 193 个楼层的楼中楼 | 413 | 1,183 | 所有楼层末页 is_last=True |
| 合计 | 513 | 3,133 | 当前接口与条件下遍历结束 |

重复记录为 0，顶层与楼中楼 ID 无交集。
原始响应共 514 份 JSON（含帖子详情，不含 manifest）。
已重新核验全部文件哈希、分页衔接、计数、楼层归属及顶层父评论关联；旧采样导入文件与来源逐字节一致。
全部结束后禁止网络并再次恢复：请求数为 0，原始文件哈希不变。
最终报告：`outputs/reports/miyoushe_full_validation.json`。
