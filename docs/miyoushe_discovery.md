# 更多帖子发现：在原采集器上增加分页和队列追加

本步目标是突破原来只读取首页第 1 页的限制。使用同一 `webHome` 接口和 `recommended_posts` 字段；
每页完整响应保存为 raw，任务按 ID 建索引，重复记录不会从 raw 删除。
来源范围仍是首页推荐列表，不等于全站或所有板块，更不能承诺固定条数。

## 修改与新增的代码

| 文件 | 作用 |
| --- | --- |
| `src/hoyo_sense/miyoushe_discovery.py` | 新增：列表页循环、页数上限、保存发现断点、恢复和停止判断 |
| `src/hoyo_sense/storage.py` | 从旧入口提取原有 JSON、哈希与文件锁工具，供两个采集模块共用 |
| `scripts/01_collect_miyoushe.py` | 修改：保留原单帖采集，接通分页发现，向旧批次追加新候选 |
| `src/hoyo_sense/config.py` | 修改：读取独立的 discovery 配置，兼容旧配置文件 |

接口 URL 仍从 `endpoints.py` 读取，游戏仍通过 `games.py` 的 ID 选择。
本步不需要复制采集器，不删除已有断点，也不迁移既有 raw。

## 输入、数据流和输出

1. 输入游戏名、原始目录、累计发现页数上限。
2. 优先引用已有成功的第一页快照，保留它原本的发现时间与请求参数；不会复制或重写它。
3. 依次请求 `page=2`、`page=3` 等已验证页码，原始响应先落盘，再登记页数、哈希和下一页。
4. 从所有已保存页重建候选索引，按帖子 ID 保留首次发现顺序；原始 JSON 中的重复、图片信息等不变。
5. 批次保留旧候选、来源、顺序和断点，新帖子只追加到末尾。重复出现的帖子不会再建一份采集任务。
6. 原单帖采集器继续复用详情、顶层和楼中楼已保存页，从未完成处继续。

以绝区零为例，新增发现文件为：

| 路径（相对 `data/raw/miyoushe/zzz/`） | 内容 |
| --- | --- |
| `_discovery/home_pages/discovery_manifest.json` | 每页来源、请求参数、保存时间、SHA-256、下一页和停止原因 |
| `_discovery/home_pages/page_000002.json` | 第 2 页完整接口响应 |
| `_discovery/home_pages/page_000003.json` | 第 3 页完整接口响应 |
| `_discovery/home_pages/discovered_posts.json` | 可从 raw 重新生成的候选索引 |

若已有第一页，它留在原时间目录，发现断点以相对路径引用它；没有旧快照时先创建第一页快照。
原批次仍位于 `_batches/zzz/batch_manifest.json`，原帖子仍位于 `zzz/<post_id>/full_collection/`。

## 运行

直接在 PyCharm 无参数运行入口时，默认 `--batch --game all`，依次处理三个游戏。
传入任何参数时完全按参数执行，不自动补批次选项。自动批次中原神使用官方资讯，星铁和绝区零使用首页；`--batch-home` 保留旧首页批次。

保留现有配置中的四个 `collection` 值和 `request` 值，只需新增与它们平级的配置块：

```json
"discovery": {
  "max_pages": null,
  "batch_pages": 20
}
```

仓库配置已新增此块。缺少该块的旧配置仍默认累计总上限 10 页、每批 20 页。这里包含已有第一页和空的停止页，按累计页数计，不是每次新增 10 页。
可用正整数或 `null`；评论采集的 `max_pages` 仍只控制顶层评论，两者互不替代。

```powershell
# 第一步：先只发现，不采正文或评论。
python scripts/01_collect_miyoushe.py --discover-more --game zzz --max-discovery-pages 10

# 第二步：在原绝区零批次中追加候选并采集，自动复用旧成果。
python scripts/01_collect_miyoushe.py --batch --game zzz --max-discovery-pages 10

# 也可一步执行：每发现至多 20 页就先处理评论，再继续下一轮。
python scripts/01_collect_miyoushe.py --batch --game starrail

# 取消推荐列表发现页数上限。仍保留空页、重复页及异常停止机制。
python scripts/01_collect_miyoushe.py --batch --game zzz --max-discovery-pages None
```

若想采集所有已发现候选，保持 `collection.max_posts=null`，其他评论上限按用户配置。
当 `max_posts=3` 时，即使发现 200 帖也只处理累计前 3 帖，其余任务显示 deferred。
使用 `--game all` 会依次运行三个游戏；分游戏运行则使用各自批次，避免同时运行 all 和相同游戏的任务。

## 停止与恢复

| 发现状态 | 含义 |
| --- | --- |
| limited | 达到累计列表页数上限，提高上限后从下一页继续 |
| exhausted | 当前推荐列表返回空页；仅说明此接口和条件下停止，不是全站完整 |
| stalled | 非空页没有新增帖子 ID；保留该页 raw 并停止，避免反复抓同一推荐页 |
| interrupted | 请求、结构或文件核验出错，保留现场与断点 |

HTTP/业务错误不当作空页，也不自动跳过；若接口异常页已经落盘，恢复会读它并报错，不盲目重复请求。
若 raw 已保存而断点尚未登记，中断恢复会补登记该页，从下一页继续。
重复运行相同上限会核验并复用页面，不会从第一页重新下载。

发现 `limited/stalled` 时，即使已入队帖子全部采完，分页批次总体仍显示 limited。
批次另存 `collection_completed` 和 `discovery_finished`，便于区分“评论任务采完”和“推荐列表返回空页”。
旧的单页 Python 调用保持兼容；`--max-discovery-pages 1` 仅使用原第一页范围，不声称推荐池全部遍历结束。

当前发现断点对应这次分页过程，不是定时刷新或持续监控；列表随时间可能变化。
本步没有实现板块列表遍历、自动重新刷新已结束列表、全站全部帖子覆盖或固定十万条数据保证。

## 首页分页初版的历史验证结果

`python -m pytest -q`：68 项通过。新增覆盖旧 completed 批次追加、累计上限恢复、
旧 raw 不变、网络中断、raw/断点之间崩溃、重复页停滞、坏页不重复请求、跨游戏排除及 CLI 只发现模式。

真实接口只检查最多 3 页，没有批量采正文和评论：

| 游戏 | 每页返回记录数 | 不同候选 ID | 状态 |
| --- | --- | --- | --- |
| 原神 | 15、0 | 15 | 第 2 页空，当前推荐列表停止 |
| 星穹铁道 | 20、18、20 | 58 | 达到 3 页验证上限 |
| 绝区零 | 20、20、20 | 60 | 达到 3 页验证上限 |

随后禁止网络并重跑相同发现上限，请求数为 0，原始文件 SHA-256 保持不变。
用户提供的“加载更多”响应也通过现有推荐列表解析，20 条候选；未把未知的具体请求页码/时间补写成真实来源。
摘要见 `miyoushe_discovery_validation_20261018.json`；本轮测试环境 Python 3.12，代码保持 Python 3.10 语法兼容。

当前首页批次也按 `batch_pages` 分轮调度，已有分页断点时先采旧候选。到达每批页数后会采评论并继续；只有到达总上限、接口停止条件或候选未完成时才结束运行。
