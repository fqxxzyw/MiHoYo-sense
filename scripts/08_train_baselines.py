"""兼容原训练命令，实际训练入口位于 modeling/tfidf_logistic/train.py。"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hoyo_sense.modeling.tfidf_logistic.train import main


if __name__ == "__main__":
    main()
