"""显式下载中文 BERT 预训练权重到统一模型目录；其他入口不会自动联网。"""

import argparse

from hoyo_sense.config import project_path
from hoyo_sense.modeling.bert.model import PRETRAINED_ID, pretrained_dir
from hoyo_sense.storage import save_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", default="main", help="可填写 Hugging Face commit 固定版本")
    parser.add_argument("--output", help="默认 outputs/models/_pretrained/bert-base-chinese")
    args = parser.parse_args()
    from huggingface_hub import HfApi, snapshot_download

    destination = project_path(args.output) if args.output else pretrained_dir()
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError(f"不覆盖已有预训练目录：{destination}；已有模型可直接用于训练")
    commit = HfApi().model_info(PRETRAINED_ID, revision=args.revision).sha
    snapshot_download(repo_id=PRETRAINED_ID, revision=commit, local_dir=destination,
                      allow_patterns=["config.json", "model.safetensors", "vocab.txt",
                                      "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json"])
    save_json({"model_id": PRETRAINED_ID, "revision": commit}, destination / "download_info.json")
    print(f"预训练权重已下载：{destination}")


if __name__ == "__main__":
    main()
