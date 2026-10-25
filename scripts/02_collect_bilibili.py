"""B站官方账号作品、一级评论和回复的小规模采集入口。"""

from pathlib import Path
import sys

# 从任何工作目录运行时，都加载本项目 src 下的共用实现。
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from hoyo_sense.collectors.video.cli import main

if __name__ == "__main__":
    raise SystemExit(main("bilibili"))
