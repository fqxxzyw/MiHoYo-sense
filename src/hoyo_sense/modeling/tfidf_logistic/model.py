"""定义评论情感分类的基线模型。"""

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline


def build_logistic_baseline():
    """创建字符 TF-IDF + 逻辑回归模型。"""
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
            LogisticRegression(max_iter=1000),
        ),
    ])

    return model


if __name__ == "__main__":
    model = build_logistic_baseline()
    print(model)