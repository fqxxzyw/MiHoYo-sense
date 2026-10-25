"""定义 fastText 情感分类模型的训练参数。"""

DEFAULT_PARAMETERS = dict(lr=0.5, epoch=20, dim=100, wordNgrams=3,
                          minCount=2, bucket=200000, minn=0, maxn=0,
                          loss="softmax", thread=4)


def train_fasttext(train_path, **overrides):
    """仅使用传入的训练文件学习模型。"""
    import fasttext

    parameters = {**DEFAULT_PARAMETERS, **overrides}
    unknown = set(parameters) - set(DEFAULT_PARAMETERS)
    if unknown:
        raise ValueError(f"未知训练参数：{sorted(unknown)}")
    for name in ("lr", "epoch", "dim", "wordNgrams", "minCount", "bucket", "thread"):
        if parameters[name] <= 0:
            raise ValueError(f"{name} 必须大于零")
    return fasttext.train_supervised(input=str(train_path), **parameters)
