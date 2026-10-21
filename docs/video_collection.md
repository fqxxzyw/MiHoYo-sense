# B站和抖音官方账号采集

2026-10-21 新增两个入口，采样对象是配置中三个游戏的**指定官方账号作品及评论**，不是整个游戏社区。
默认原神、一个作品、作品列表一页、一级评论一页、一个父评论分支的一页回复；不下载头像、图片、音频或视频。
米游社脚本及配置保持原样。

## 新增文件的作用

| 文件 | 作用 |
| --- | --- |
| `scripts/02_collect_bilibili.py` | B站入口，加载共用 CLI |
| `scripts/03_collect_douyin.py` | 抖音入口，加载共用 CLI |
| `configs/bilibili.json`、`configs/douyin.json` | 账号、公共业务参数、路径、请求设置和有限默认上限 |
| `src/hoyo_sense/video_cli.py` | 游戏、作品、上限、私密上下文等参数解析 |
| `src/hoyo_sense/video_config.py` | 配置校验，复用 `config.py` 的项目根路径定位 |
| `src/hoyo_sense/video_http.py` | 串行请求、有限重试、Cookie 环境变量、动态 WBI、参数脱敏 |
| `src/hoyo_sense/video_platforms.py` | 初始状态 JSON 和两平台响应解析、作品/评论归属校验 |
| `src/hoyo_sense/video_collection.py` | 任务队列、累计上限、独立分支断点、中断恢复与防循环 |
| `src/hoyo_sense/video_storage.py` | 不可覆盖的字节原文与完整 JSON 归档，ID 字符串化 |
| `tests/test_video_collectors.py` | 禁止真实网络的解析、分页、失败、恢复和上限测试 |
| `docs/video_collection_validation_20261021.json` | 本次真实小样本结果；不包含凭据或评论正文 |

共用的接口地址仍在 `endpoints.py`；其地址表现在包括“浏览器样本提供但本地尚未成功”的接口，不能把地址存在理解为验证通过。
原子断点写入、时间、哈希和目录锁复用已有 `storage.py`，没有更改米游社断点格式。

## 配置与执行

在已激活 `hoyosense` 的项目根目录运行。也可以把 `python` 换成
`D:\Anaconda3\envs\hoyosense\python.exe`。脚本、配置和 CLI 的相对输出路径都按项目根目录解析。

```powershell
# 默认只发现并采集原神官方账号的小样本；账号列表可能被平台拒绝。
python scripts/02_collect_bilibili.py
python scripts/03_collect_douyin.py

# 已知B站作品：HTML详情、一级评论一页、自动发现的一个父评论的一页回复。
python scripts/02_collect_bilibili.py --game genshin --bvid BV1zi7Y6BEdS

# 已知抖音作品：没有经过验证的独立详情接口，先采评论。
python scripts/03_collect_douyin.py --game genshin --aweme-id 7684640572371488015

# 单独测试交接中的父评论；顶层上限0不会启动新的一级评论请求。
python scripts/02_collect_bilibili.py --bvid BV1zi7Y6BEdS --parent-comment-id 303897942961 --max-pages 0 --max-sub-pages 3
python scripts/03_collect_douyin.py --aweme-id 7684640572371488015 --parent-comment-id 7684907540819952421 --max-pages 0

# 只发现账号作品；此命令不采评论。
python scripts/02_collect_bilibili.py --discover-only --game genshin --max-discovery-pages 2
python scripts/03_collect_douyin.py --discover-only --game genshin --max-discovery-pages 2

# 明确选择其他游戏，仍使用有限默认上限。
python scripts/02_collect_bilibili.py --game starrail --bvid BV1Rmh96ZEXh
python scripts/02_collect_bilibili.py --game zzz --bvid BV1SknF6KE5g
python scripts/03_collect_douyin.py --game starrail --discover-only
python scripts/03_collect_douyin.py --game zzz --discover-only
```

账号完整标识见两个配置文件，尤其不要截断抖音 `sec_user_id`。B站 `mid` 依次为
`401742377`、`1340190821`、`1636034895`；不要拿米游社 `gids` 代替它们。
CLI 可显式选择 `--game all`，默认不会自动启动三个游戏。

| 参数 | 含义 |
| --- | --- |
| `--config` | 公共配置文件；显式 CLI 上限覆盖配置上限 |
| `--raw-root` | 平台数据根目录，其下直接是 `genshin/starrail/zzz` |
| `--max-posts` | 普通模式处理累计队列前 N 个作品；指定作品模式只选择该作品 |
| `--max-discovery-pages` | 累计作品列表页数，包含已经保存的页 |
| `--max-pages` | 每作品累计一级评论页数 |
| `--max-sub-pages` | 每个父评论分支累计回复页数 |
| `--max-floors` | 普通模式处理按发现顺序的累计前 N 个父评论分支 |
| `--parent-comment-id` | 只测试指定的回复分支，可重复传入，仍受分支上限约束 |
| `--metadata-file` | 指定抖音作品时导入该作品的完整本地 JSON，必须含匹配的 `aweme_id`、`author`、`desc` |
| `--resume` / `--no-resume` | 默认自动恢复；关闭恢复会拒绝已有断点，不删除数据 |

上限 `0` 表示不发起该类新任务；JSON `null` 或 CLI `None` 仅取消人工上限，不承诺全部历史覆盖。
已落盘但尚未提交断点的响应会优先恢复，即使此次调小了页数上限。
相同目录重复运行同上限不会换成“下一批”；增加上限才会继续之前未完成的任务。
未完的旧队列先于新作品发现处理，每发现一页就先处理作品，不会无限发现后才开始评论采集。
指定作品模式仅处理所选作品；所选作品与 `--game` 的映射由用户提供，B站详情会核验 owner.mid。

配置中 `request` 默认是间隔 2 秒、超时 60 秒、额外重试 1 次。
仅超时、连接错误、HTTP 502/503/504 适合有限重试，每次尝试都分别记录。
403、429、重定向、业务失败、空正文和验证页面停止，不自动绕过验证。
Requests 超时是连接/读取超时，并非整次命令的严格总时限。

## 会话和动态参数

Cookie 只从本地环境变量或本地上下文读取：

```powershell
# 值只在本机设置，不要粘贴到源码、README或提交中。
$env:HOYOSENSE_BILIBILI_COOKIE = '<本地Cookie>'
$env:HOYOSENSE_DOUYIN_COOKIE = '<本地Cookie>'
python scripts/03_collect_douyin.py --request-context configs/douyin.local.json --aweme-id 7684640572371488015
```

`configs/*.local.json` 已被 Git 忽略。本地上下文结构示例：

```json
{
  "headers": {"User-Agent": "与本地会话一致的浏览器标识"},
  "params": {
    "common": {"msToken": "本地当前会话值", "verifyFp": "本地当前环境值"},
    "comments": {},
    "replies": {}
  }
}
```

环境变量 Cookie 优先于本地 headers 中的 Cookie。脚本不会自动读取浏览器登录信息。
本地参数可以保留观察到的浏览器/设备字段；调度器产生的作品、父评论、分页位置优先，
因此上下文中的旧 `cursor/max_cursor/pn` 不会把请求永久固定在旧页。公共配置禁止放入签名/会话字段。
日志只输出接口用途、HTTP状态、条数和状态；断点中的请求参数按业务白名单脱敏，未知字段值默认隐藏。
完整原始响应可能包含平台返回的用户标识或会话信息，保持本地、不提交原始数据。

B站在需要时请求 `nav` 获取当时的 WBI 密钥，为每次请求重新生成 `wts/w_rid`，不存永久签名。
`nav` 的未登录状态不能视为登录成功。WBI只是请求实现，不保证免于风控；
算法核对参考[公开 Python 实现源码](https://github.com/7Hello80/Bilibili_PrivateMessage_Bot/blob/main/wbi.py)，本项目另有固定输入测试和本地请求证据。

抖音未内置 `a_bogus` 算法。`msToken/a_bogus/fp/webid/uifid/authentication_token` 等值都不能当作永久凭据。
如果已有自己维护的本地动态签名实现，可用 `--signer package:function`：模块应能通过当前 Python 的
模块路径导入，函数接收关键字 `kind, url, params, headers`，返回完整参数字典；不得改变作品、父评论、游标等业务参数。
签名代码和会话值应保存在仓库外。没有签名实现时也可以做有限请求测试，失败就保留证据并停止，
不能把仅配置了 Cookie 或导入旧签名写成“抖音已跑通”。

## 输出和断点

默认数据根目录：

```text
data/raw/bilibili/<game>/
data/raw/douyin/<game>/
  _batch/manifest.json                 # 队列及所有独立任务的进度
  _discovery/<唯一标识>.*               # 账号作品列表
  _session/<唯一标识>.*                 # B站WBI初始化响应（如使用）
  <作品ID>/full_collection/
    detail/<唯一标识>.*                 # B站HTML及初始状态JSON
    comments/<唯一标识>.*               # 一级评论
    replies/<父评论ID>/<唯一标识>.*      # 回复分支
```

一次响应保存字节原文 `.body`（视频详情用 `.html`）、HTTP上下文 `.http.json`；
解析与归属校验成功后另存**全部字段与全部记录**的 `.json`。该 JSON 仅把 ID 转为字符串，
不清洗文本、不去重。字节原文保持服务器的原始数字表示，便于取证；读取它时应使用 Python 整数，
不要经过 JavaScript 浮点转换。分析和后续处理优先使用字符串 ID 的完整 JSON。
抖音从作品列表获得的完整作品对象保存在断点 `works[aweme_id].metadata`，
`metadata_source` 指向完整列表 JSON，不调用未经验证的独立详情接口。
指定抖音作品但没有列表元信息时，评论可以继续，作品状态仍为部分完成，原因是基本信息尚缺。
本地导入的作品 JSON 单独标记为 `explicit_local_metadata`，不能算作本地详情请求成功。

每页断点包含 UTC 时间、平台/游戏、接口来源、脱敏参数、作品及请求父评论ID、输入/输出分页、
本页条数、接口报告总数、业务状态、停止原因及文件哈希。任务ID仅用于去重调度，raw 中重复记录不变。
B站热门排序明确记录 `mode=3`。抖音的 `reply_id/reply_to_reply_id/root_comment_id` 全部原样保留，
额外的 `request_parent_comment_id` 才是本次请求使用的父评论；不把 root_comment_id 强行当一级评论ID。
不依据跨平台同名昵称合并用户；缺少抖音作者 sec_uid 时只保留请求来源上下文，不通过昵称猜测账号身份。

响应完整落盘后才推进任务断点。响应已落盘、断点尚未推进时，可通过待办请求重放解析，避免重复请求。
如果进程恰好在字节原文写入后、HTTP sidecar 落盘前崩溃，会保留孤立原文并另存一次新请求，绝不覆盖旧文件。
恢复会校验已登记的原文、JSON哈希及分页链；同一游戏目录有进程锁，避免并行改写断点。
来源账号或公共业务参数改变时拒绝复用旧目录，可通过新的 `--raw-root` 创建独立采样条件。

状态含义：`completed` 是当前任务/接口条件返回结束，`limited` 是人工上限或基本信息不足，
`stalled` 是游标循环、重复页或空页仍有更多，`interrupted` 是请求/解析/落盘失败或人为中断。
`manifest.run_scope` 记录本次选择的作品、模式和上限；顶层 completed 只针对本次请求范围，
其余队列任务以各自状态为准。停滞任务不会自动无限重试，检查 raw 后再决定是否用新目录验证。
目录存在不代表作品完成；只有对应任务断点能决定是否继续。
预建目录中的空 `.gitkeep` 占位文件会保留并允许首次初始化；非空 `.gitkeep` 或其他未登记文件
仍会阻止创建新断点，错误消息会给出具体游戏目录和缺失的断点路径，无需删除占位目录。

## 本次验证和待办

离线测试使用**依据交接字段构造的模拟响应**。当前目录没有交接中的完整浏览器HTML/JSON文件，
因此不能声称已离线重放过那些原始样本。本轮视频采集49项、项目全量174项测试全部通过。
离线检查涵盖长ID、空文本、重复记录保留、归属错误、
分页结束/循环/空页/重复页、累计上限、分支进度、中断落盘恢复、禁止网络恢复、失败不推进与脱敏。

```powershell
python -m pytest tests/test_video_collectors.py -q
python -m pytest -q
```

| 链路 | 交接证据 | 2026-10-21 本地 Python 结果 |
| --- | --- | --- |
| B站原神作品发现 | 浏览器第1页 | HTTP200 / code=-352，失败；pn=2未实测成功 |
| B站原神详情 | 浏览器HTML解析 | HTTP200，aid/owner/desc解析成功，与交接一致 |
| B站原神一级评论 | 初始浏览器样本，后页未验证 | HTTP200/code=0，仅3条，all_count=70901，is_end=true；下一页未验证 |
| B站父评论303897942961回复 | 浏览器第1、2、3页响应 | 本地实际pn=1、2、3均成功，各10条，共30个不同ID；报告count=3992，仍为limited |
| B站另一父评论307025126864 | 本次自动发现 | 本地第1、2页成功，各10条，仍为limited |
| B站真实文件恢复 | 本轮新增检查 | 禁止网络后恢复0次请求，27份已有响应/JSON/HTTP文件哈希不变 |
| 抖音原神作品发现 | 浏览器中间页 | HTTP403，失败；初始max_cursor=0和连续翻页仍未验证成功 |
| 抖音原神一级评论 | 浏览器第1页10条 | HTTP200但零字节正文，失败；未推进cursor |
| 抖音原神已知父评论回复 | 浏览器单页1条 | HTTP200但零字节正文，失败；多页链路仍待验证 |
| 星铁/绝区零两个平台 | 账号/部分测试视频已知 | 已配置、离线参数检查通过；未做本轮真实接口请求 |
| 抖音弹幕 | 浏览器粘贴样本 | 第一阶段未实现/未启用，完整性和时间单位仍待验证 |

本次真实请求使用临时被忽略的配置：间隔2秒、超时15秒、重试0次，未提供登录Cookie。
真实响应留在 `data/raw/bilibili_validation_20261021/genshin/` 和
`data/raw/douyin_validation_20261021/genshin/`，与默认采集目录分开；它们均被 Git 忽略。
汇总证据见 [验证记录](video_collection_validation_20261021.json)。

一级评论只返回3条且声称结束，不能据此声称覆盖70901条；返回总数也不等于采集量。
B站一级评论 `next_offset` 链路、作品列表第二页，抖音初始列表和所有连续游标链路仍待本地成功验证。
回复三页成功不代表回复已采完。抖音播放量为0的原始字段不解释为真实零播放。
弹幕不混入评论回复关系，也不进行未经核验的时间单位换算。
本轮没有执行大规模采集，没有提交凭据、原始响应或自动推送GitHub。
