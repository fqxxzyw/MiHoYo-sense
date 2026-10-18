# 原神官方资讯列表发现

本阶段增加一个独立的发现来源，先展示候选，不采集正文和评论。
接口由浏览器“官方 → 资讯”页面的请求及真实前两页响应验证。

## 输入与输出

接口：`https://bbs-api-static.miyoushe.com/painter/wapi/getNewsList`。
第一页参数：`client_type=4`、`gids=2`、`last_id=`、`page_size=20`、`type=3`。
帖子列表位于 `data.list`，读取 `item.post.post_id`、`subject`、`game_id`。
后续请求原样传递上一响应的 `data.last_id`，不自行加 20，不套用首页的 page 参数。

保存目录：`data/raw/miyoushe/genshin/_discovery/news_type_3/`。

| 文件 | 含义 |
| --- | --- |
| `page_000001.json` 等 | 每页完整原始 JSON，包含图片链接，但不下载图片 |
| `discovery_manifest.json` | 请求参数、文件哈希、分页链、数量、下一游标、状态与历史执行记录 |
| `discovered_posts.json` | 按帖子 ID 去重、按首次出现顺序排列的候选任务清单 |

每条候选记录来源 `getNewsList/list`、发现时间、请求参数、原始文件和页内位置。
候选清单的去重只避免重复调度；原始响应中的重复项仍保留。
旧的 `home_pages` 发现断点、批次清单及单帖断点均不由此模式更新。

## 数据流与代码

入口 `scripts/01_collect_miyoushe.py --discover-news` 使用共用的配置和请求函数，
调用 `src/hoyo_sense/miyoushe_news_discovery.py`。

数据流：请求前保存待请求游标 → 原始页原子落盘 → 登记页信息和下一游标 → 重建候选清单 → 展示各页候选。
如果中断发生在原始页落盘后、断点登记前，恢复时直接登记已保存页，不重新请求。
同一上限再次执行时仍展示已保存页的帖子 ID 和标题，便于核对发现范围。

这份列表中的 `post.game_id` 实测为 `0`，必须保留实际值。
另外记录 `game=genshin` 和 `game_assignment=request_context`，表示请求上下文，
并标记 `requires_detail_validation=true`，后续取得详情后再核验归属。
不能把所有 `game_id=0` 当作跨游戏帖子过滤，也不能直接修改为 2。
列表中的空正文、uid=0、user/stat=null 不能当作完整帖子详情或真实作者标识。

## 运行方式

在项目根目录的 PowerShell 中执行：

```powershell
# 默认只发现两页，展示每页帖子，不启动评论采集。
& 'D:\Anaconda3\envs\hoyosense\python.exe' -u scripts/01_collect_miyoushe.py --discover-news --game genshin

# 扩大至累计 5 页，从已保存两页后的游标继续；这是额外的发现操作。
& 'D:\Anaconda3\envs\hoyosense\python.exe' -u scripts/01_collect_miyoushe.py --discover-news --game genshin --max-discovery-pages 5
```

上限接受正整数或 `None`。本阶段新模式默认 2 页；原有首页发现模式的配置默认值不变。
本阶段只验证原神 `type=3`，其他游戏在该模式下会报错，不把接口兼容性当作已验证事实。
`--discover-news` 与 `--batch`、`--discover-more` 等模式互斥。
原有 `--batch` 继续使用首页来源；`--batch-news` 已接入资讯清单，并继承旧原神批次候选。

## 如何判断结束

- `limited`：达到累计发现页数上限，尚未结束。
- `completed`：该资讯来源返回 `is_last=True`，仅代表该列表遍历结束。
- `interrupted`：请求失败、游标重复或缺失、空列表却未结束、整页无新 ID、文件核验失败等。

HTTP、业务或 JSON 失败不会无限重试。已保存异常页会保留；恢复也不会跳过它或重请求它。
完成状态始终只描述这个列表，不表示正文、评论完成，也不保证原神全站覆盖。

## 本地验证结果

- 第一页 20 帖：请求游标为空，响应游标 `20`，`is_last=false`。
- 第二页 20 帖：请求游标 `20`，响应游标 `40`，`is_last=false`。
- 共 40 个不同帖子 ID，相比旧首页的 15 个候选，包含 35 个新 ID。
- 停止状态为 `limited`，没有声称遍历完整；评论采集未启动。
- 同上限恢复请求数为 0，仍展示每页候选，原始页面哈希不变。
- 3,979 份旧 JSON（包括原神旧采样、发现断点和原神批次清单）核对未变。
- `tests/test_miyoushe_news_discovery.py` 新增 14 项测试；全量 82 项测试通过。
- 验证报告：`outputs/reports/miyoushe_news_discovery_validation.json`。

上述两页验证阶段没有恢复旧评论采集进程，没有提交或上传 GitHub。

## 抽样通过后的正式批次

2026-10-18 从尚未采集的资讯候选中用随机种子 20261018 抽取 3 帖：

| 帖子 ID | 标题 | 顶层抽样 | 楼中楼抽样 |
| --- | --- | --- | --- |
| 78481174 | 【网页活动上线】周年快乐，旅行者！ | 20 | 1 |
| 78279662 | 克洛琳德生日快乐 | 20 | 15 |
| 78817334 | 《原神》过场动画-「遗赠」 | 20 | 20 |

三个帖子详情都返回 game_id=2，正文文本可获取，评论归属核验通过。
报告：`outputs/reports/genshin_news_random_probe.json`。抽样 raw 和断点直接保存在单帖标准目录，正式批次复用。
抽样后新增批次调度及详情归属测试，全量 89 项测试通过。

正式启动命令：

```powershell
& 'D:\Anaconda3\envs\hoyosense\python.exe' -u scripts/01_collect_miyoushe.py --batch-news --game genshin --max-discovery-pages None --max-posts None --max-pages None --max-sub-pages None --max-floors None
```

任务先从已保存游标无上限遍历资讯列表，直到该列表返回 is_last=True，再逐帖复用原采集器。
顶层和每个有回复楼层也按各自 is_last 判断结束。2 秒间隔、配置超时、分页异常检测和异常停止继续生效。
每帖先校验详情明确返回 game_id=2，才开始评论采集；缺少或不符时停止，不把列表的 0 强改为 2。

批次索引继续使用原目录：`data/raw/miyoushe/_batches/genshin/batch_manifest.json`。
资讯调度状态在该文件的 `news_collection` 子字典中，原首页候选和历史记录保留。
它继承旧原神首页批次的 15 个候选，与资讯候选按帖子 ID 去重，保留所有来源。
单帖目录仍是 `genshin/<post_id>/full_collection/`，所以旧原始页、旧断点和本次抽样页都能续用。
后续查看同一批次文件的 `news_collection` 及各帖断点，不再新建 genshin_news 批次目录。

经用户授权，已停止原先挂起的原神进程以释放文件锁，由新批次接管续采；星铁和绝区零进程未调整。
随后按用户要求已停止原神后台任务，将资讯批次进度并回原批次文件，并删除多余的 genshin_news 批次目录。
4110 份原始 JSON 核对未变，90 项测试通过；合并记录见 `outputs/reports/genshin_batch_directory_merge.json`。
用户在 CMD 手动运行：

```bat
cd /d D:\soft\python\NLP\HoYoSense
"D:\Anaconda3\envs\hoyosense\python.exe" -u scripts\01_collect_miyoushe.py --batch-news --game genshin --max-discovery-pages None --max-posts None --max-pages None --max-sub-pages None --max-floors None
```

旧后台任务记录见 `outputs/reports/genshin_news_current_job.json`，当前标记为已停止、等待用户在 CMD 续采。
达到接口结束会正常退出；HTTP、业务或分页异常会保留断点并停止，不会无限重试。
恢复时重新执行相同命令，勿与已有原神批次进程同时运行。
