# 按算法学习训练、评估与预测

每个算法有自己的目录，训练、评估、预测放在一起；可复用代码放在 `common/`。
当前逻辑回归与随机森林已实现训练、评估和预测。fastText、BERT 已补全相同入口及量化、蒸馏，
详见 [模型压缩学习说明](model_compression_learning.md)；LLM 仍预留。

```text
src/hoyo_sense/modeling/
├── common/
│   ├── __init__.py
│   ├── dataset.py          # 读取 annotated、校验、过滤 -1，沿用用户原代码
│   ├── evaluation.py       # 统一标签顺序、Macro-F1、分类报告、混淆矩阵
│   └── model_io.py         # 创建运行目录，保存、加载 sklearn Pipeline
├── tfidf_logistic/
│   ├── __init__.py
│   ├── model.py            # TF-IDF 和逻辑回归参数，沿用用户原代码
│   ├── train.py            # 读 train/dev → fit → dev 评估 → 保存
│   ├── evaluate.py         # 加载模型 → 读 dev/test → 预测 → 指标，不调用 fit
│   └── predict.py          # 加载模型 → 新评论 → 情感类别，不读取训练数据
├── tfidf_rf/               # 已有 model.py、train.py、evaluate.py、predict.py
├── fasttext/               # prepare/model/train/evaluate/predict/quantize/distill
├── bert/                   # download/model/training/train/evaluate/predict/quantize/distill
├── llm/                    # 预留，后续明确提示分类或微调
├── splitting.py            # 原有按帖子划分代码，保持位置
└── dataset.py              # 旧导入路径的兼容入口，实际读取代码在 common/
```

`model.py` 定义算法，`train.py` 使用数据学习参数；`evaluate.py` 需要有标签的数据计算指标，
`predict.py` 给没有标签的新评论分类。后续算法复用读取和指标计算，模型的专用保存格式可在实现时补充，
不把 fastText 或 BERT 强行当成 sklearn Pipeline。

所有命令在项目根目录的 PowerShell 运行，先设置一次源码路径：

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
$env:PYTHONIOENCODING = "utf-8"
```

训练（执行后会真的训练并生成新运行目录）：

```powershell
& "D:\Anaconda3\envs\hoyosense\python.exe" -m hoyo_sense.modeling.tfidf_logistic.train
```

独立评估已保存的模型，默认 dev：

```powershell
& "D:\Anaconda3\envs\hoyosense\python.exe" -m hoyo_sense.modeling.tfidf_logistic.evaluate --model outputs/models/tfidf_logistic/20261022_075724_679057_UTC
```

当前模型已验证 dev Macro-F1=0.8662、Accuracy=0.9329；这是对当前标注数据的结果。
评估入口默认只打印，另存报告可添加 `--output outputs/reports/tfidf_logistic/dev_evaluation.json`。
`--split test` 仅在方案冻结后的最终评估时明确指定。

预测自己的新评论：

```powershell
& "D:\Anaconda3\envs\hoyosense\python.exe" -m hoyo_sense.modeling.tfidf_logistic.predict --model outputs/models/tfidf_logistic/20261022_075724_679057_UTC --text "这个活动很好玩" "奖励太少了"
```

`--model` 可以传运行目录，也可以直接传 `pipeline.joblib` 文件。相对路径由公共配置按项目根目录解析。
在 PyCharm 中运行上述入口时，将运行配置设置为模块名（例如 `hoyo_sense.modeling.tfidf_logistic.evaluate`），
并填写脚本参数；保持现有 `src` 源码根目录和 `hoyosense` 解释器。

保存格式保持不变，每次训练建独立目录：

```text
outputs/models/tfidf_logistic/<UTC时间>/
├── pipeline.joblib         # 包含 TF-IDF 词表、IDF 和分类器
└── dev_metrics.json        # 行数、训练耗时、dev 指标、分类报告和混淆矩阵
```

原来的 `python scripts/08_train_baselines.py` 仍可用，它只转到算法目录的训练入口。
原来的 `hoyo_sense.modeling.dataset` 仍转到公共读取代码；新代码统一导入
`hoyo_sense.modeling.common.dataset`。原学习文件内容已移入 `common/dataset.py`，
数据、已保存模型和历史评估记录不随源码调整而重写。

## 统一预测界面与后端

项目根目录的 `app/` 和 `api/` 提供统一预测界面和后端：

```text
app/                            # 选择算法、输入评论、显示预测结果
    ↓
api/                            # 公共预测接口，按算法分发请求
    ↓
src/hoyo_sense/modeling/<algorithm>/predict.py
    ↓
outputs/models/<algorithm>/<run>/
```

启动 `python scripts/09_serve_app.py` 后打开 `http://127.0.0.1:8765`，
选择算法和模型版本，一行输入一条评论即可预测。目前已有逻辑回归与随机森林权重，
fastText、BERT 接口也已注册，训练保存后自动可选；LLM 待接入。
随机森林当前选定版本为 `20261023_085825_426077_UTC`，dev Macro-F1=0.8019、Accuracy=0.8889；界面也保留其他已保存版本供切换。
算法目录中的 `predict.py` 继续保留，后续由公共后端调用，避免在界面或接口中重复写算法逻辑。
