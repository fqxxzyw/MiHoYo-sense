# 情感分析训练交接：下一位智能体从这里开始

## 用户希望怎样学习

**2026-10-25 当前请求优先：** 用户已明确要求一次补全 fastText、BERT 及量化/蒸馏学习代码，
压缩操作先复制原模型，所有模型产物统一放 `outputs/models/`。
已增加独立算法入口和 `scripts/10_model_experiment.py`，具体流程见
[模型压缩学习说明](model_compression_learning.md)。后面“每轮只写一步”的段落是此前教学偏好，
不应据此阻止本次已授权的代码补全。代码完成不等于真实模型训练已完成，需分别核对。

用户要另一位智能体带着自己一步一步写训练代码，重点是**在哪里建文件夹、建哪个文件、代码写在哪里、怎样运行和检查**。
当前不要直接替用户生成整套训练工程或启动训练。每轮只做一个可验证的小步骤：

1. 先说明本步目标，以及要打开/新建的具体项目相对路径。
2. 解释这个文件负责什么、输入输出是什么。
3. 给本步必要代码，讲清关键行，不一次铺开全部训练代码。
4. 给使用 `hoyosense` 环境的运行命令和预期输出。
5. 等用户运行、贴出结果，核对通过后再进入下一步。

本项目工作区是 `D:/soft/python/NLP/HoYoSense`；PyCharm SDK 为
`D:/Anaconda3/envs/hoyosense/python.exe`（Python 3.10.22）。`requirements.txt` 已声明 pandas、scikit-learn、jieba 等依赖，
但开始教学时仍应检查当前环境的实际安装情况，不另建虚拟环境或直接换解释器。

## 当前已完成的流程

| 入口 | 已实现的用途 |
| --- | --- |
| `scripts/01_collect_miyoushe.py` | 米游社三游戏采集、恢复；原神资讯调度在 `src/hoyo_sense/collectors/miyoushe/news_batch.py` |
| `scripts/02_collect_bilibili.py`、`03_collect_douyin.py` | 已有采集代码，目前暂缓这两个平台，不继续扩展 |
| `scripts/04_extract_miyoushe.py` | 提取 raw 中已登记的帖子、顶层和楼中楼，保留完整快照 |
| `scripts/05_prepare_analysis.py` | 生成只有 `post_id,comment` 两列的工作表 |
| `scripts/06_clean_comments.py` | 去空、同帖相同文本去重、异常审计和前后 EDA |
| `scripts/07_split_dataset.py` | 按帖子划分 train/dev/test，保存固定归属 |
| `scripts/08_train_baselines.py` | 兼容入口，转到 `modeling/tfidf_logistic/train.py` |

现有数据已就绪，开始训练教学不需要重跑采集、清洗或划分。
`collectors/miyoushe/news_batch.py` 是采集调度模块，不应把训练代码写进该模块。
当前逻辑回归已完成首次训练、保存和 dev 评估：Macro-F1=0.8662、Accuracy=0.9329。
每个算法目录各自存放模型定义、训练、评估和预测代码，共用代码在 `modeling/common/`。
原有模型可以直接加载，当前没有运行 test。路径与命令见 [算法目录说明](modeling_structure.md)。

## 目录约定：沿用现有位置

```text
data/
├── raw/          # 原始采集数据
├── processed/    # 提取快照及两列评论表
├── annotated/    # 用户标注原件
└── splits/       # 固定的 train/dev/test 及帖子归属
src/hoyo_sense/
├── collectors/miyoushe/  # 米游社发现与资讯批次调度
├── collectors/video/     # B站和抖音共用的采集代码
├── analysis/             # 分析表准备及标注复核
├── preprocessing/        # 本地提取、清洗
├── modeling/             # 各算法目录；common/ 放共用代码；保留原有划分代码
└── recommendation/       # 推荐功能预留目录
scripts/                 # 可直接运行的入口
tests/                   # 针对关键行为的测试
outputs/                 # EDA、校验报告、后续模型和指标
docs/                    # 学习说明及交接
```

不要新增 `data` 的同级业务子目录；不要把模型文件、图表或实验日志塞进四种数据目录。
`analysis`、`modeling`、`preprocessing` 这些源码目录已经存在，不需要再建一套平行工程。
公共 `config.py`、`storage.py`、`games.py`、`endpoints.py`、`__init__.py` 保持在包根目录。
用户编写的数据读取代码现位于 `src/hoyo_sense/modeling/common/dataset.py`，内容保留；
旧的 `modeling/dataset.py` 为兼容入口。`src/hoyo_sense/analysis/annotation_review.py` 保持原样。
后续教学先阅读并沿用现有文件，不要覆盖或重复创建。

## 本地数据文件与标签

**2026-10-22 更新：** 表情处理结果已合回原来的三个 `*_labeled.csv`，并过滤纯数字、
普通符号评论。当前 train/dev/test 为 155,587 / 27,905 / 38,161 条，
待审核为 2,098 / 466 / 425 条。后续继续读取原文件名；
下面的导入数量与逐条匹配结论是过滤前记录，最新规则与备份见
[标注合并说明](annotation_merging.md)。原始 `data/splits/` 未变。

清洗输入是 `data/processed/miyoushe/comments.csv`，275,570 行。
成品是同目录 `comments_clean.csv`，224,250 行，只保留 `post_id,comment`。
去除原始空文本 2,649 条、没有可提取文字的图片/表情图片 2,755 条，同帖相同文本合并 45,916 次。
异常原文和重复次数位于 `outputs/cleaning/miyoushe/`。没有根据广告/抽奖等启发式标记直接删除评论。

人工/外部标注文件最初按用户提供的字节原样保存；三个评论标注原件现已备份到 `data/annotated/backups/`：

| 文件 | 内容 |
| --- | --- |
| `data/annotated/posts_analysis_labeled.csv` | 742 篇帖子的类型，实际标注在 class_id；仍有 6 篇 -1 待判定 |
| `data/annotated/class.csv` | 帖子类型定义：资讯活动、游戏攻略、剧情讨论、同人娱乐、玩家交流 |
| `data/annotated/train_labeled.csv` | 训练评论，列为 post_id、comment、label |
| `data/annotated/dev_labeled.csv` | 验证评论，同上 |
| `data/annotated/test_labeled.csv` | 测试评论，同上 |
| `data/annotated/sentiment_classes.csv` | 评论情感定义，来自用户的 class (1).csv |

**不要混用帖子类型 class_id 与评论情感 label。** 情感标签定义是：

| label | 含义 | 首轮处理 |
| --- | --- | --- |
| 0 | 负面 | 使用 |
| 1 | 正面 | 使用 |
| 2 | 中性 | 使用 |
| -1 | 人工审核/无法判断 | 保留原件，暂不参加三分类训练或评分 |

| 集合 | 帖子数 | 总评论 | -1 待审核 | 三分类可用 |
| --- | ---: | ---: | ---: | ---: |
| train | 594 | 157,568 | 13,150 | 144,418 |
| dev | 74 | 28,226 | 2,195 | 26,031 |
| test | 74 | 38,456 | 2,108 | 36,348 |

三个标注 CSV 都与 `data/splits` 下对应的原两列表逐条匹配，帖子交叉为 0。
8:1:1 是帖子数比例，实际评论数比例为约 70.26%/12.59%/17.15%。不要为了凑行数重新打散帖子。
`data/splits/split_manifest.csv` 已固定每个 post_id 所属集合，开始标注后不要反复换 seed。

**标签来源仍待用户确认**：是逐条人工标注、自动标注后人工复核，还是规则/模型自动标注？
如果是自动标签，视为弱标签，dev/test 需有独立人工复核样本后才可将分数解释为真实情感识别效果。
文件字段、数量和哈希校验只证明文件对应，不证明标签正确。

另有 3,497 组跨集合完全相同文本，清单在 `outputs/splitting/miyoushe/cross_split_duplicates.csv`。
先区分常见短句和复制模板，必要时补充去重叠的评估子集；不要静默删掉难例或仅报告较高分数。

## 下一步应该逐个创建哪些文件

下表按当前算法目录组织，路径相对于 `src/hoyo_sense/`。
先核对已有代码的进度，再逐步教学。

| 顺序 | 建议文件 | 本步要教的代码与运行检查 |
| --- | --- | --- |
| 1 | `modeling/common/dataset.py` | 已有读取、三列检查、标签校验和 -1 过滤；保持 ID 为字符串。 |
| 2 | `modeling/tfidf_logistic/model.py` | 已有字符 TF-IDF + 逻辑回归参数，输入只用 comment。 |
| 3 | `modeling/tfidf_logistic/train.py` | 已有 train 拟合、dev 评估和模型保存。 |
| 4 | `modeling/tfidf_logistic/evaluate.py` | 加载现有模型独立评估，默认 dev，不重新 fit。 |
| 5 | `modeling/tfidf_logistic/predict.py` | 用已保存模型对自己的新评论分类。 |
| 6 | `modeling/tfidf_rf/`、`fasttext/`、`bert/`、`llm/` | 已预留目录，后续逐个编写同样职责的入口，复用公共代码。 |
| 7 | 各算法的 `evaluate.py` | 方案冻结后明确指定 test，统一做最终评估，不再用 test 调参。 |

建议首轮模型：

1. DummyClassifier 多数类参照。
2. 字符 TF-IDF + ComplementNB。
3. 字符 TF-IDF + LogisticRegression。
4. 字符 TF-IDF + LinearSVC。

字符特征可先用 1–3 gram；单字、表情、否定词不应提前丢弃。
词表和 IDF 只能在 train 上 fit，dev/test 只 transform。主指标 Macro-F1，辅以分类别召回率和混淆矩阵。
当前训练集三分类样本中，中性 111,016、正面 35,156、负面 7,317，不能只看多数类主导的准确率。

模型与本次运行指标统一放 `outputs/models/<algorithm>/<run_name>/`，逻辑回归当前保存：

```text
pipeline.joblib           # 完整 TF-IDF 与分类器
dev_metrics.json          # dev 指标、训练行数、耗时和混淆矩阵
```

目前不需要先搭建网站、调用外部 API 或下载大模型。先让用户掌握完整的本地稀疏文本基线流程。

## Git 与数据可见性

Git 同步代码、配置、测试和文档。本地 `data/raw`、`data/processed`、`data/annotated`、`data/splits`、`outputs` 已被忽略；
新增数据、标注、模型与报告不会随本次提交上传。旧仓库中少量已经跟踪的 raw 样本仍可能存在，不代表全量数据。
在同一台机器的下一位智能体可读取上述本地路径；如果换电脑或只有 Git 仓库，需另行提供四份情感标注文件，
不能假设 git clone 后会自动出现本地训练数据。

## 可直接发给下一位智能体

> 请先读 README.md、docs/training_handoff.md 和 docs/sentiment_baselines.md。
> 我希望自己一步一步学习写训练代码，重点是在哪个目录建什么文件、每段代码做什么、怎样运行验证。
> 请一次只带我完成一个小步骤，不要直接生成整套训练程序或自动跑全部模型。
> 先阅读 modeling/common/ 和 modeling/tfidf_logistic/ 中的现有代码，
> 从加载已保存的逻辑回归模型，在 dev 上独立评估和预测新评论继续教学。
> test 暂时只确认文件存在，不用它选模型或调参。保持 data 下原有四个目录，实验产物放 outputs。
