# fastText、BERT 与模型压缩学习

本次补全训练、评估、预测、量化和蒸馏入口。实际训练需要对应依赖和本地模型。
代码导入和 `--help` 不会下载权重、执行训练或修改数据。暂不运行全量实验。

## 文件放在哪里

```text
src/hoyo_sense/modeling/
├── common/
│   ├── dataset.py          # 读取原标注 CSV，过滤 -1
│   ├── evaluation.py       # 0 负面、1 正面、2 中性的统一指标
│   ├── model_io.py         # 创建独立运行目录；保留 sklearn 的读写
│   └── experiments.py     # 模型元信息、路径、源模型复制及哈希验证
├── fasttext/
│   ├── prepare.py         # CSV → 字符分隔的 TXT
│   ├── model.py           # fastText 参数与训练函数
│   ├── train.py           # train 学习，dev 评分，保存 model.bin
│   ├── evaluate.py        # 加载原始/量化/蒸馏模型评估
│   ├── predict.py         # 新评论 → 标签，与准备数据使用同一预处理
│   ├── quantize.py        # 原生乘积量化，保存 model.ftz
│   └── distill.py         # 教师硬标签 → 较小的 fastText 学生
└── bert/
    ├── download.py        # 显式下载预训练模型，记录仓库 commit
    ├── model.py           # BERT 分类头、tokenizer、保存与加载
    ├── training.py        # 可逐行阅读的 PyTorch 训练循环与蒸馏损失
    ├── train.py           # 微调，按 dev Macro-F1 保存最佳 epoch
    ├── evaluate.py        # 独立评估；默认 dev
    ├── predict.py         # 分批预测，复用保存的 tokenizer 和 max_length
    ├── quantize.py        # CPU 动态 INT8 Linear 量化
    └── distill.py         # 冻结 BERT 教师，训练较少层学生
```

数据路径保持：

```text
data/annotated/{train,dev,test}_labeled.csv    # 原始标注表，不改写
data/annotated/fasttext/{train,dev,test}.txt   # fastText 派生格式
```

BERT 直接读取 CSV 中的原评论，不能使用 fastText 加空格后的文本。
每次训练/蒸馏检查 train/dev 帖子无交叉；三组交叉检查在 `fasttext/prepare.py` 中已有。
训练和蒸馏只学习 train，dev 用于选模型；test 只有独立评估时显式指定才读取。
fastText 训练前逐行检查 train.txt 与当前 CSV 一致，避免使用过期转换文件。

## 原模型、副本和结果

```text
outputs/models/
├── _pretrained/bert-base-chinese/     # 下载的预训练原件，不用于预测界面
├── fasttext/<UTC运行时间>/
│   ├── artifact.json                 # 算法、original/quantized/distilled、文件格式
│   ├── model.bin 或 model.ftz
│   ├── training_config.json          # 训练或蒸馏参数
│   ├── dev_metrics.json
│   └── source_copy/                  # 量化/蒸馏时才有，保留本次教师或源模型
└── bert/<UTC运行时间>/
    ├── artifact.json
    ├── model/                        # tokenizer、config、权重和推理长度
    ├── training_config.json
    ├── history.json                  # 训练/蒸馏逐轮指标
    ├── dev_metrics.json
    ├── comparison.json               # 量化/蒸馏前后 dev 指标和模型大小
    └── source_copy/
```

每次量化和蒸馏各复制一份源模型到新实验的 `source_copy/`，然后从副本加载。
蒸馏还会创建独立学生模型。原始运行目录不被覆盖；副本也保留不动。
`source_hashes.json` 记录复制时的文件 SHA256，实验结束再检查原件和副本。
不递归复制旧实验的副本，避免“副本里面再套副本”。

fastText 硬标签蒸馏的 `distillation_train.txt` 是本次实验产物，位于该运行目录；不会替换人工标签。
`comparison.json` 中的大小只统计推理产物，不包含备份、日志和伪标签文件。
保留源模型副本会增加实验目录总占用，这和推理模型压缩率是两个概念。

## 依赖

当前工作环境：`D:/Anaconda3/envs/hoyosense/python.exe`，Python 3.10。
可选依赖在 `requirements-modeling.txt`，基础 `requirements.txt` 没有被整体升级。
本机检测到 RTX 3060 Laptop 6GB；CPU INT8 量化模型只能在 CPU 上推理。

安装前确认正在使用 hoyosense 环境。下面命令针对 Windows PowerShell：

```powershell
$py = "D:\Anaconda3\envs\hoyosense\python.exe"
# GPU 训练环境：先安装匹配的 PyTorch CUDA 构建；需驱动支持 CUDA 12.4。
& $py -m pip install "torch==2.6.0" --index-url https://download.pytorch.org/whl/cu124
& $py -m pip install -r requirements-modeling.txt
```

如只用 CPU，将第一条安装命令的 index-url 换成 `https://download.pytorch.org/whl/cpu`。
不要同时安装 `fasttext` 和 `fasttext-wheel`：两者均提供 `import fasttext`，Windows 使用 wheel 包。
选择 PyTorch 构建前可用 `nvidia-smi` 核对驱动；安装步骤由用户执行，不由训练脚本自动执行。
这里固定 Transformers 4.57.1 和 PyTorch 2.6 系列，以便学习代码与动态量化加载方式保持一致。

## 运行方式和顺序

所有命令在项目根目录运行。统一入口会设置 src 搜索路径，不需要每个算法文件重复修改 sys.path：

```powershell
$py = "D:\Anaconda3\envs\hoyosense\python.exe"
& $py scripts/10_model_experiment.py fasttext train --help
```

PyCharm 可以直接运行算法文件（沿用 src 源码根目录）；也可运行 `scripts/10_model_experiment.py`，
在脚本参数里填 `fasttext train` 等。后者不依赖 IDE 的源码路径设置。

### 1. fastText 原始模型

```powershell
# 已转换且 CSV 没变时，不需要重复 prepare。
& $py scripts/10_model_experiment.py fasttext prepare
& $py scripts/10_model_experiment.py fasttext train
```

复制控制台打印的实际运行目录，固定本次学习对象：

```powershell
$ft = "outputs/models/fasttext/<刚打印的运行时间>"
& $py scripts/10_model_experiment.py fasttext evaluate --model $ft
& $py scripts/10_model_experiment.py fasttext predict --model $ft --text "这个活动很好玩" "奖励太少了"
& $py scripts/10_model_experiment.py fasttext quantize --model $ft
```

fastText 量化使用 `qnorm=True, retrain=False`，不再拟合数据，保存 `.ftz` 并重新加载核验预测。
这是 fastText 原生乘积量化，不等同于 BERT 的 INT8。

### 2. BERT 原始模型

```powershell
# 这一步会联网下载 google-bert/bert-base-chinese；已有权重时不要重跑。
& $py scripts/10_model_experiment.py bert download
& $py scripts/10_model_experiment.py bert train --epochs 3 --batch-size 8 --accumulation-steps 2 --max-length 128
```

训练默认 auto 设备，检测到可用 CUDA 才使用显卡。显存不足时将 batch-size 改为 4，
accumulation-steps 改为 4；两者相乘是通常的有效批次大小。
默认使用 FP32，较容易理解；没有在这次学习代码中叠加 AMP、分布式或训练框架。
每轮评估 dev，仅保存 Macro-F1 最佳的模型；`history.json` 保留所有轮次。
`training_seconds` 包含训练和逐轮 dev 评估，不能和只统计 fit 的 sklearn 耗时直接比较。

```powershell
$bert = "outputs/models/bert/<刚打印的原始模型运行时间>"
& $py scripts/10_model_experiment.py bert evaluate --model $bert --device auto
& $py scripts/10_model_experiment.py bert predict --model $bert --text "今天更新了新版本"
& $py scripts/10_model_experiment.py bert quantize --model $bert
```

BERT 量化将 Linear 的权重改为动态 INT8，Embedding 和 LayerNorm 仍为浮点。
量化后以 state_dict 保存，加载时重建量化结构；不用 joblib 保存整个 PyTorch 模型。
模型体积和准确率是否改善，以 `comparison.json` 实测为准；没有承诺必然更快或不损失精度。

### 3. BERT 教师 → 较少层 BERT 学生

```powershell
& $py scripts/10_model_experiment.py bert distill --teacher $bert --layers 4 --epochs 3
```

学生沿用教师 tokenizer、embedding、分类头及均匀选取的 encoder 层，得到独立参数。
教师固定 eval 并关闭梯度。学生学习：

```text
loss = alpha × T² × KL(教师软分布 || 学生软分布)
       + (1 - alpha) × CE(真实标签, 学生输出)
```

默认温度 T=2、alpha=0.5。软分布由 train 评论产生；dev 标签不参与反向传播。
这是一种简化的输出层知识蒸馏，不宣称复现 DistilBERT 的完整预训练方法。

学生模型还可独立量化，以形成“原始 / 原始量化 / 蒸馏 / 蒸馏后量化”四组实验：

```powershell
$student = "outputs/models/bert/<蒸馏打印的运行时间>"
& $py scripts/10_model_experiment.py bert quantize --model $student
```

### 4. BERT 教师 → fastText 学生（可选学习实验）

```powershell
& $py scripts/10_model_experiment.py fasttext distill --teacher $bert --teacher-algorithm bert
```

原生 fastText 监督接口不接收完整软标签分布，因此此入口使用教师的最高概率类别作为硬标签。
只在 train 上产生伪标签，并用原始 dev 真实标签评估学生。这不是上面的温度 KL 蒸馏。
也可用较大的原始 fastText 作教师：`--teacher $ft --teacher-algorithm fasttext`。
学生默认 dim=30、bucket=50000，不保证能优于直接用真实标签训练的基线。

### 5. 最终 test 与界面

冻结各模型配置后再显式运行测试集，例如：

```powershell
& $py scripts/10_model_experiment.py bert evaluate --model $student --split test --save
```

不传 `--save` 只打印；传入后另存 `<split>_evaluation.json`，不覆盖训练时的 dev 指标。
evaluate/predict 未指定模型时选择最新完成版本，并打印路径；压缩默认选择最新 original。
正式比较建议始终显式指定目录，避免混用版本。

启动 `scripts/09_serve_app.py`，公共后端会自动发现有 `artifact.json` 的已保存模型。
前端版本框显示原始、量化或蒸馏。只有源码、没有权重时显示“尚无已保存模型”。
fastText/BERT 依赖采用延迟导入；缺少它们不影响原有 LR/RF 界面。

## 验证与参考

2026-10-25 本次验证：原环境全套测试 270 通过、2 项因可选依赖缺失跳过。
随后在临时目录准备 fasttext-wheel 0.9.2、PyTorch 2.6.0 CPU、Transformers 4.57.1，
仍使用同一 hoyosense Python 解释器，模型相关 29 项测试全部通过，包含前面跳过的两项真实模型测试。
测试设置 Hugging Face 离线模式，不下载预训练权重；实际标注数据未用于训练。
PyTorch 的 INT8 重载出现一条 TypedStorage 弃用提示，不影响预测一致性验证。
临时验证依赖未安装到 hoyosense 环境，正式训练前仍需执行上面的依赖安装步骤。

`tests/test_learning_experiments.py` 验证副本哈希、路径限制、数据集边界、格式一致性、后端分发。
安装可选依赖后还会在临时目录训练微型随机 BERT/fastText，验证保存、量化重载与蒸馏梯度；
这些测试不下载权重，不使用真实语料，也不代表实际模型效果。依赖缺失时会明确 skip。

- [fastText 分类与量化](https://fasttext.cc/docs/en/python-module.html)
- [BERT 分类模型](https://huggingface.co/docs/transformers/v4.57.1/en/model_doc/bert)
- [PyTorch 知识蒸馏教程](https://docs.pytorch.org/tutorials/beginner/knowledge_distillation_tutorial.html)
- [PyTorch 动态量化](https://docs.pytorch.org/docs/2.6/generated/torch.ao.quantization.quantize_dynamic.html)
- [PyTorch 2.6 安装组合](https://pytorch.org/get-started/previous-versions/)
