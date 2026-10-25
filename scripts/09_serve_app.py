"""启动本地多算法情感预测界面及公共 API。"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from api.server import main


if __name__ == "__main__":
    main()
