"""统一学习入口：选择算法和动作，再把其余参数传给对应模块。"""

import argparse
import importlib
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

ACTIONS = {
    "fasttext": {"prepare", "train", "evaluate", "predict", "quantize", "distill"},
    "bert": {"download", "train", "evaluate", "predict", "quantize", "distill"},
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("algorithm", choices=sorted(ACTIONS))
    parser.add_argument("action")
    args = parser.parse_args(sys.argv[1:3])
    remaining = sys.argv[3:]
    if args.action not in ACTIONS[args.algorithm]:
        parser.error(f"{args.algorithm} 支持：{', '.join(sorted(ACTIONS[args.algorithm]))}")
    module = importlib.import_module(f"hoyo_sense.modeling.{args.algorithm}.{args.action}")
    sys.argv = [f"{args.algorithm}/{args.action}.py", *remaining]
    module.main()


if __name__ == "__main__":
    main()
