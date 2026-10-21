"""抖音官方账号作品、一级评论和回复的小规模采集入口。"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from hoyo_sense.video_cli import main

if __name__ == "__main__":
    raise SystemExit(main("douyin"))
