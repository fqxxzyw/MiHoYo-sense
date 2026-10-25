# 评论情感基线模型计划

目前已完成字符 TF-IDF + LogisticRegression 的首次训练和 dev 评估，模型保存在
`outputs/models/tfidf_logistic/20261022_075724_679057_UTC/`，dev Macro-F1=0.8662、Accuracy=0.9329。
训练、评估、预测分属算法目录，共用代码放 `modeling/common/`，见 [算法目录说明](modeling_structure.md)。
标注来源待确认；若标签由规则或其他模型生成，
先将其视为弱标签。最终指标要用独立人工复核的 dev/test 样本核验，不能把拟合自动标签的分数直接当成真实情感准确率。

## 输入

当前输入位于 `data/annotated/`：`train_labeled.csv`、`dev_labeled.csv`、`test_labeled.csv`。
2026-10-22 已合回 v2 表情处理并过滤纯数字、普通符号评论；导入原件保存在同目录 `backups/` 中。
规则和逐行记录见 [标注合并说明](annotation_merging.md)。
评论数据列为 `post_id,comment,label`。情感类别定义为 `sentiment_classes.csv`，与帖子类型的 `class.csv` 分开。

| 标签 | 含义 | 首轮三分类处理 |
| --- | --- | --- |
| 0 | 负面 | 使用 |
| 1 | 正面 | 使用 |
| 2 | 中性 | 使用 |
| -1 | 人工审核 | 保留源文件，不参加首轮三分类训练或评分 |

三份文件在导入时与原始 train/dev/test 的帖子 ID 和评论多重集合完全一致；
过滤后为原划分子集，帖子归属不变、交叉为 0。
导入历史校验在 `outputs/annotation_validation/miyoushe/sentiment_labels_report.json`，
最新处理记录在 `outputs/annotation_review/annotation_merge_latest.json`。

| 集合 | 总行数 | 待审核 | 三分类可用行数 |
| --- | ---: | ---: | ---: |
| train | 155,587 | 2,098 | 153,489 |
| dev | 27,905 | 466 | 27,439 |
| test | 38,161 | 425 | 37,736 |

可用 train 中负面 7,317、正面 35,156、中性 111,016，存在明显类别不均衡。
上述是文件数量核验，并不代表已确认标注正确性。

## 第一轮模型

| 模型 | 作用 |
| --- | --- |
| DummyClassifier（多数类） | 作为始终预测训练集多数类的最低参照 |
| 字符 TF-IDF + ComplementNB | 快速的稀疏文本基线 |
| 字符 TF-IDF + LogisticRegression | 主基线，可输出类别概率 |
| 字符 TF-IDF + LinearSVC | 线性文本分类对照 |

后三者使用相同的字符 TF-IDF 特征作为可比起点，可从 1–3 gram 开始，保留中文单字和表情信号。
这是一轮实验方案，不预先断言哪种模型最好。暂不需要先训练深度模型。

模型输入只使用 comment；post_id 用于关联和分组，不作为情感特征。帖子类型 class_id 也不是评论情感标签。
TF-IDF 词表、IDF 和分类器只能在 train 上拟合。dev 用来选择正则强度、是否使用类别权重等设置。
选择完成并冻结流程后，再在 test 上做最终评估，不通过 test 反复挑模型。

## 评价与误差分析

主要看 Macro-F1，同时记录 Accuracy、各类别 Precision/Recall/F1、混淆矩阵和训练/预测耗时。
Macro-F1 对每一类别同等加权，可帮助发现多数类准确率掩盖的负面类漏判。
记录排除 -1 后的覆盖率；不能把待审核评论直接当作中性。

重点抽查反讽、正负混合、抽奖祝福、纯表情、短句和领域专有名词。
现有划分报告中的 3,497 组跨集合相同文本仍需检查。完全相同短句与复制模板应区分，
需要时补充不含重叠文本的评估子集并同时报告样本覆盖，不能只保留较高的一组分数。

模型与指标后续统一写到 `outputs`，不继续改写标注数据或重新划分测试集。

## 官方参考

- [scikit-learn 稀疏文本分类对比](https://scikit-learn.org/stable/auto_examples/text/plot_document_classification_20newsgroups.html)
- [scikit-learn F1 定义及平均方式](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.f1_score.html)
