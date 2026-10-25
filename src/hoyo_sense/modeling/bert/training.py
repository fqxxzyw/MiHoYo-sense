"""BERT 微调和蒸馏共用的显式训练循环，便于逐行学习。"""

import random

from hoyo_sense.modeling.bert.model import BertBundle, save_bundle
from hoyo_sense.modeling.bert.predict import predict_labels
from hoyo_sense.modeling.common.evaluation import print_evaluation
from hoyo_sense.storage import save_json


def set_seed(seed):
    import numpy as np
    import torch

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def distillation_loss(student_logits, teacher_logits, labels, *, temperature=2.0, alpha=0.5):
    import torch.nn.functional as F

    if temperature <= 0 or not 0 <= alpha <= 1:
        raise ValueError("temperature 必须大于零，alpha 必须在 [0, 1]")
    # alpha 控制软标签占比；detach 保证教师不参与反向传播。
    soft_loss = F.kl_div(
        F.log_softmax(student_logits / temperature, dim=-1),
        F.softmax(teacher_logits.detach() / temperature, dim=-1),
        reduction="batchmean",
    ) * temperature ** 2
    hard_loss = F.cross_entropy(student_logits, labels)
    return alpha * soft_loss + (1 - alpha) * hard_loss


def fit(bundle, train, dev, run, *, epochs=3, batch_size=8, accumulation_steps=2,
        lr=2e-5, seed=42, teacher=None, temperature=2.0, alpha=0.5):
    import torch
    from torch.utils.data import DataLoader
    from tqdm.auto import tqdm

    if min(epochs, batch_size, accumulation_steps) < 1 or lr <= 0:
        raise ValueError("轮数、批次、累积步数和学习率必须大于零")
    if bundle.max_length < 2 or bundle.max_length > bundle.model.config.max_position_embeddings:
        raise ValueError("max_length 超过模型支持的范围")
    set_seed(seed)
    device = bundle.device
    bundle.model.to(device)
    if teacher is not None:
        teacher.model.to(device).eval()
        teacher.model.requires_grad_(False)

    def collate(rows):
        texts, labels = zip(*rows)
        inputs = bundle.tokenizer(list(texts), padding=True, truncation=True,
                                  max_length=bundle.max_length, return_tensors="pt")
        return inputs, torch.tensor(labels, dtype=torch.long)

    rows = list(zip(train["comment"].tolist(), train["label"].tolist()))
    generator = torch.Generator().manual_seed(seed)
    loader = DataLoader(rows, batch_size=batch_size, shuffle=True, collate_fn=collate,
                        generator=generator, num_workers=0)
    optimizer = torch.optim.AdamW(bundle.model.parameters(), lr=lr, weight_decay=0.01)
    history, best_score, best_metrics, best_epoch = [], -1.0, None, None
    for epoch in range(1, epochs + 1):
        bundle.model.train()
        optimizer.zero_grad(set_to_none=True)
        total_loss = 0.0
        for step, (inputs, labels) in enumerate(tqdm(loader, desc=f"epoch {epoch}/{epochs}")):
            inputs = {key: value.to(device) for key, value in inputs.items()}
            labels = labels.to(device)
            logits = bundle.model(**inputs).logits
            if teacher is None:
                loss = torch.nn.functional.cross_entropy(logits, labels)
            else:
                with torch.no_grad():
                    teacher_logits = teacher.model(**inputs).logits
                loss = distillation_loss(logits, teacher_logits, labels,
                                         temperature=temperature, alpha=alpha)
            total_loss += loss.item() * len(labels)
            # 按本累积组的实际样本数归一化，最后一个不足整批的 batch 也正确加权。
            group_start = (step // accumulation_steps) * accumulation_steps
            group_rows = min(accumulation_steps * batch_size, len(rows) - group_start * batch_size)
            (loss * len(labels) / group_rows).backward()
            if (step + 1) % accumulation_steps == 0 or step + 1 == len(loader):
                torch.nn.utils.clip_grad_norm_(bundle.model.parameters(), 1.0)
                optimizer.step()
                optimizer.zero_grad(set_to_none=True)
        metrics = print_evaluation(dev["label"], predict_labels(bundle, dev["comment"], batch_size=batch_size))
        history.append({"epoch": epoch, "train_loss": total_loss / len(train), **metrics})
        if metrics["macro_f1"] > best_score:
            best_score, best_metrics, best_epoch = metrics["macro_f1"], metrics, epoch
            save_bundle(bundle, run / "model")
        save_json(history, run / "history.json")
    # 返回的是已保存的最佳 epoch 指标，不将最后一轮误报为最佳模型。
    return best_metrics, best_epoch


def make_student(teacher_model, layers):
    from copy import deepcopy
    from transformers import BertForSequenceClassification

    count = teacher_model.config.num_hidden_layers
    if not 1 <= layers < count:
        raise ValueError(f"学生层数必须在 1 到 {count - 1} 之间")
    config = deepcopy(teacher_model.config)
    config.num_hidden_layers = layers
    config._attn_implementation = "eager"
    student = BertForSequenceClassification(config)
    student.bert.embeddings.load_state_dict(teacher_model.bert.embeddings.state_dict())
    student.bert.pooler.load_state_dict(teacher_model.bert.pooler.state_dict())
    student.classifier.load_state_dict(teacher_model.classifier.state_dict())
    selected = [(i + 1) * count // layers - 1 for i in range(layers)]
    for index, source_index in enumerate(selected):
        student.bert.encoder.layer[index].load_state_dict(teacher_model.bert.encoder.layer[source_index].state_dict())
    return student, selected
