"""定义字符 TF-IDF + 随机森林情感分类模型。"""

from sklearn.ensemble import RandomForestClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.pipeline import Pipeline


def build_rf_baseline():
    """创建 RF 基线，TF-IDF 设置与逻辑回归保持一致。"""
    model = Pipeline([
        (
            "tfidf",
            TfidfVectorizer(
                analyzer="char",
                ngram_range=(1, 3),
                min_df=2,
                max_features=100_000,
            ),
        ),
        (
            "classifier",
            RandomForestClassifier(
                n_estimators=100,
                max_depth=80,
                min_samples_leaf=2,
                max_features="sqrt",
                n_jobs=-1,
                random_state=42,
                class_weight="balanced",
            ),
        ),
    ])

    return model


if __name__ == "__main__":
    print(build_rf_baseline())