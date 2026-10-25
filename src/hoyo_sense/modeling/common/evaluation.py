"""统一不同算法的标签顺序、分类指标和控制台评估格式。"""

from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score


LABELS = [0, 1, 2]
LABEL_NAMES = ["负面", "正面", "中性"]


def classification_metrics(y_true, predictions):
    """只计算指标，不训练模型，也不读取数据。"""
    return {
        "macro_f1": f1_score(y_true, predictions, labels=LABELS, average="macro", zero_division=0),
        "accuracy": accuracy_score(y_true, predictions),
        "classification_report": classification_report(
            y_true, predictions, labels=LABELS, target_names=LABEL_NAMES,
            output_dict=True, zero_division=0,
        ),
        "confusion_matrix": confusion_matrix(y_true, predictions, labels=LABELS).tolist(),
    }


def print_evaluation(y_true, predictions, *, split="dev"):
    """打印与原训练入口一致的评估信息，并返回可保存的指标。"""
    metrics = classification_metrics(y_true, predictions)
    print(f"\n{split} Macro-F1：{metrics['macro_f1']:.4f}")
    print(f"{split} Accuracy：{metrics['accuracy']:.4f}")
    print("\n各类别的评估结果：")
    print(classification_report(
        y_true, predictions, labels=LABELS, target_names=LABEL_NAMES,
        digits=4, zero_division=0,
    ))
    print("混淆矩阵：行是真实类别，列是预测类别")
    print("类别顺序：负面、正面、中性")
    print(confusion_matrix(y_true, predictions, labels=LABELS))
    return metrics
