# 米游社三游戏采集与断点恢复

本阶段支持原神、崩坏：星穹铁道、绝区零的原始 JSON，不下载图片，不清洗文本。
遍历结束仅限于当前接口、顶层热门排序及已发现楼层，不保证全部历史、删除或折叠评论。
按用户确认的顺序，先积累完整接口 JSON，再做 EDA 与数据去重。
任务清单按 ID 去重仅避免重复调度；分页 ID 集合仅统计重复，不过滤或改写 raw 中任何记录。
已有页的复用只避免重复请求，不把同一响应重新保存为“新增数据”。

## 输入与输出

脚本：`scripts/01_collect_miyoushe.py`。不指定模式时仍为原神测试帖 78918177 单帖模式。
单帖断点目录：`data/raw/miyoushe/<game>/<post_id>/full_collection/`。
再次运行相同目录会自动恢复；指定不同的空 `--run-dir` 可以建立独立采样。

| 游戏参数 | 游戏 | gids |
| --- | --- | --- |
| genshin | 原神 | 2 |
| starrail | 崩坏：星穹铁道 | 6 |
| zzz | 绝区零 | 8 |
| all | 三游戏依次执行，仅用于发现/批次模式 | 分别使用 2、6、8 |

| 参数 | 含义 |
| --- | --- |
| `--config` | 指定 JSON 配置，默认 `configs/miyoushe.json` |
| `--raw-root` | 临时覆盖米游社原始数据根目录，其下直接保存游戏子目录 |
| `--discover-posts` | 每游戏只请求首页第 1 页，保存并展示去重清单，不采评论 |
| `--batch` | 按发现清单运行有限批次，自动恢复 |
| `--game` | 默认 genshin，可选 starrail、zzz、all |
| `--post-id` | 单帖 ID；星穹铁道、绝区零单帖模式必须显式指定 |
| `--max-posts` | 每游戏累计处理的前 N 帖，默认 3，仅批次模式使用 |
| `--max-pages` | 顶层累计页数上限 |
| `--max-sub-pages` | 每个楼层累计页数上限 |
| `--max-floors` | 按首次发现顺序处理的楼层数上限 |
| `--batch-dir` | 批次进度目录，默认 `data/raw/miyoushe/_batches/<游戏名拼接>/` |
| `--run-dir` | 指定单帖断点目录，不能与发现/批次模式混用 |
| `--seed-run` | 新目录首次运行时复制导入旧格式采样 |
| `--verify-only` | 只读核验原始文件与断点，不发请求 |

命令行的顶层页数、楼中楼页数、楼层数默认分别为 **5、2、3**，避免直接运行便启动无限采集。
四种上限均接受非负整数或 `None`；`None` 只取消对应上限，不会启用首页翻页。
上限包括已保存页面；重复运行相同上限不会逐轮扩大范围，降低上限也不删数据。
内部 `run_collection()` 保留历史调用默认值以兼容旧代码；直接调用函数时应显式传入上限。

## 路径与配置的约定

`configs/miyoushe.json` 保存可调整值；`src/hoyo_sense/config.py` 保存读取、校验和路径解析逻辑。
后续清洗脚本可复用 `load_config().paths["processed_dir"]`，不用各写一份 `../data`。
本阶段没有把现有采集器整体拆成复杂框架，`scripts/01_collect_miyoushe.py` 仍是运行入口。

项目根目录从配置模块的 `__file__` 推导，不使用进程的当前目录。
配置中的路径与 CLI 的 `--config`、`--raw-root`、`--run-dir`、`--batch-dir`、`--seed-run`
相对路径都基于项目根目录；绝对路径直接定位，`~` 展开为当前用户目录。
Python 调用 `run_collection()`、`run_batch()`、`discover_posts()` 的相对目录也采用同一约定。
在 Windows 上可使用 `D:/...`，跨机器共享的配置建议保持项目相对路径。
Linux 上错误使用 Windows 绝对路径会明确报错，不会悄悄建立名为 `D:` 的相对目录。

CLI 显式上限覆盖配置，包括 `0` 和 `None`；JSON 中要用 `null`，不能写字符串 `"None"`。
缺省配置项使用默认值；拼错的键、非法上限和重叠的数据目录会在请求前报错。
读取配置只解析路径，不创建目录，不移动数据；改路径意味着后续读取新位置，已有文件仍在原处。
新原始目录若不在默认 `data/raw/` 下面，需要相应更新 `.gitignore`。

## 先发现，再批量

```powershell
# 输入：三个游戏标识；输出：独立的首页原始响应和候选任务清单。
python scripts/01_collect_miyoushe.py --discover-posts --game all

# 输入：已保存的发现快照；输出：各帖原始 JSON、单帖和批次 manifest。
python scripts/01_collect_miyoushe.py --batch --game all --max-posts 3 --max-pages 5 --max-sub-pages 2 --max-floors 3

# 只采星穹铁道；不需要改脚本里的任何全局 ID。
python scripts/01_collect_miyoushe.py --batch --game starrail --max-posts 1

# 单帖示例：保持游戏与帖子归属匹配。
python scripts/01_collect_miyoushe.py --game zzz --post-id 78937626 --max-pages 2 --max-sub-pages 2 --max-floors 2
```

发现接口为 `https://bbs-api-static.miyoushe.com/apihub/wapi/webHome`，
只使用 `gids=<游戏ID>&page=1&page_size=20` 与 `data.recommended_posts`。
请求 20 条不保证返回 20 条，也不保证是全站最新帖或随机样本。
记录 `webHome/recommended_posts` 来源、发现时间、请求参数和原始出现位置。
原始列表保留重复记录，任务按 `post_id` 去重；跨游戏记录保留在发现清单及批次 `excluded`，不进入错误的游戏目录。

批次首次运行优先复用各游戏最新的成功发现快照，没有才请求首页。
之后候选名单固定，不随首页变化；恢复时重新核对来源和原始响应哈希。
每帖调用原单帖采集器，复用 `full_collection` 中已有原始页，不重新采帖或前几页。
发现功能本身不会修改旧的单帖或批次状态。

若要采用新的首页样本，先再次运行发现命令，再指定一个新的空 `--batch-dir`：

```powershell
python scripts/01_collect_miyoushe.py --discover-posts --game all
python scripts/01_collect_miyoushe.py --batch --game all --batch-dir data/raw/miyoushe/_batches/second_sample
```

新批次仍会复用同 ID 帖子的旧断点；这不是把旧帖重新采成一个新时间快照。
要对某帖建立独立时间快照，使用单帖模式和新的空 `--run-dir`。
不修改已有批次的游戏组合；单游戏和三游戏批次默认使用不同目录。
不同批次若涉及同一帖子，单帖文件锁会防止并发写入。

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
文件缺失或内容修改会停止检查；系统文件锁防止两个采集进程同时写同一目录。
Git 在 Windows/Linux 间可能只转换 CRLF/LF 文件换行；兼容校验只接受当前字节、
统一 LF 或统一 CRLF 的 SHA-256 与旧记录一致，不忽略空格、字段顺序或其他文本变化。
发生这种转换时记录 `original_sha256` 与 `hash_note`，原始文件不被改写。

## 运行与验证

在项目根目录的 PowerShell 中执行，解释器使用已配置的 hoyosense 环境。

```powershell
# 小规模运行：本地旧采样导入后，先登记的楼层是 35 楼。
& 'D:\Anaconda3\envs\hoyosense\python.exe' -u scripts/01_collect_miyoushe.py --seed-run data/raw/miyoushe/genshin/78918177/20261018_062634_980587_UTC --max-pages 6 --max-sub-pages 3 --max-floors 1

# 显式无上限恢复；直接运行脚本现已采用有限默认值。
& 'D:\Anaconda3\envs\hoyosense\python.exe' -u scripts/01_collect_miyoushe.py --max-pages None --max-sub-pages None --max-floors None

# 只读核验。
& 'D:\Anaconda3\envs\hoyosense\python.exe' scripts/01_collect_miyoushe.py --verify-only
```

没有旧数据的任务省略 `--seed-run`；当前仓库实际包含一部分历史原始样本。
旧页保留旧采集时间，新页记录新时间；这不是同一时刻的静态快照。
超时、连接异常、Ctrl+C 后可重新运行相同目录续采。没有自动重试。
HTTP 403、429、502 或业务错误会记录错误并停止当前批次，不跳过失败页后声称成功。
恢复时先读已保存文件，再从失败请求继续。如果持续失败，保留现场并检查网络或浏览器实际请求；
不要把 Cookie、令牌或设备标识写进代码。当前三个游戏测试沿用原有四个普通请求头。
若遇到异常分页或文件核验失败，先检查已保存的异常页，恢复不会自动跳过或重新请求该页。

## 状态与进度

| 状态 | 含义 | completed |
| --- | --- | --- |
| pending | 尚未开始或等待下一页 | false |
| running | 正在执行，强制退出后可从落盘断点重建 | false |
| completed | 正常遍历结束，分页任务末页明确 is_last=True | true |
| limited | 达到页数或楼层数上限 | false |
| interrupted | 请求、业务、分页、文件异常或手动中断 | false |

批次中的单帖还可能为 `deferred`：超出本游戏累计帖子上限，暂未处理。
空发现清单显示 `no_posts`，不宣称全站没有帖子或已遍历完成。
批次 `completed` 仅表示本批固定清单中所有候选都在当前接口条件下结束，仍不是全站全量。

帖子详情任务的完成仅表示该详情已保存并核验。
`manifest.json` 中每个分页任务保存 `next_last_id`、`pages`、`pending_request` 和 `stop_reason`。
每次执行的上限、时间、状态及错误记录在 `attempts`。总体不再使用含义模糊的 finished。

已验证：13 项离线测试通过；原样本顶层 99 个不同 ID、35 楼 40 个不同 ID；真实小规模续采后分别为 119 和 60。
以上为历史记录；本轮新增三游戏及批次测试后的完整测试数量见下方当日验证记录。
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

## 2026-10-18 三游戏扩展验证

测试命令为 `python -m pytest -q`，41 项通过。本轮测试使用 Python 3.12；
代码保留项目要求的 Python 3.10 语法与标准库范围，未声称已经在用户 Windows 环境执行新版本。
依赖继续使用已有 `requirements.txt`，没有新增训练框架或采集依赖。

新增离线覆盖：三游戏 ID 与目录隔离、帖子/游戏归属检查、跨游戏候选排除、
重复发现去重、复用发现快照、累计帖子上限不轮换、提高上限只处理新增任务、
请求中断恢复、原始响应不改写、空列表不误报全量完成、CLI 模式约束、CRLF/LF 校验兼容，
以及配置路径不依赖当前目录、中文/空格路径、CLI 覆盖优先级和配置错误提前阻断。
旧原神样本 514 份原始 JSON 的只读核验通过，文件未修改。

本次真实验收采用独立数据目录，避免改动仓库里已有的采样：每游戏 1 帖，
每帖最多 2 页顶层、2 个楼层、每楼 2 页。原神与绝区零均达到设定上限，
不是接口全量结束；原神保存顶层 39 / 楼中楼 80 个 ID，绝区零保存顶层 40 / 楼中楼 39 个 ID。
对两者禁止网络后恢复，均没有请求，原始文件字节保持不变；绝区零批次恢复也通过。

星铁详情及顶层 2 页、40 个 ID 已保存。`post_id=78446173` 的 24 楼批次请求多次返回 HTTP 502，
响应包含 `Connection refused`。独立探测同帖 24、26 楼曾返回成功，不能据此声称整个批次已通过；
该旧批次保留中断断点，没有将 HTTP 502 写成“空评论”或“已完成”。
三个首页分别返回原神 15 帖、星铁 20 帖、绝区零 20 帖；返回 `game_id` 分别为 2、6、8。
用户另提供的两份首页 JSON 各 20 条，解析和游戏归属核验通过，没有导入为有确切采集时间的新批次。

本轮普通请求头仍为 `User-Agent`、`Referer`、`Origin`、`Accept`。未添加 Cookie、`ds` 或设备标识，
也未更改代理。首页翻页、全站帖子覆盖和三游戏整批成功恢复均不列为已验证完成。

用户补充成功 URL 后，新增验证星铁帖子 `78487092`：详情、顶层第一页和 221 楼第一页均返回 200；
使用断点保存的游标续取第二页，顶层累计 40 个 ID，221 楼累计 31 个 ID。
绝区零帖子 `78960086` 同样续采两页：顶层累计 37 个 ID、35 楼累计 9 个 ID，35 楼末页返回 `is_last=True`。
两条续采命令均从项目目录之外启动，使用 `--raw-root` 指定数据目录，没有重复请求详情或第一页。
随后禁止网络并以相同上限重新运行，两者请求数均为 0，已保存原始文件的 SHA-256 全部不变。
这说明两个游戏共用接口和游标逻辑已跑通；不把新帖成功当成旧星铁 24 楼的故障已解决。
