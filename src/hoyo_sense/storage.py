"""共用 JSON 保存、校验和文件锁；采集模块与发现模块复用，不改写既有 raw。"""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path) -> Any:
    with Path(path).open(encoding="utf-8") as file:
        return json.load(file)


def save_json(data, path):
    """先写同目录临时文件，再原子替换，避免中断留下半份 JSON。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_digest(path, expected):
    """兼容 Git 在 Windows/Linux 间仅转换 CRLF/LF；其他字节变化仍拒绝。

    返回实际 SHA；旧 SHA 被保留为元数据，不改写原始 JSON。
    JSON 字符串中的换行是转义字符，不受这里的文件换行转换影响。
    """
    content = Path(path).read_bytes()
    actual = hashlib.sha256(content).hexdigest()
    if expected and actual != expected:
        lf = content.replace(b"\r\n", b"\n")
        candidates = (lf, lf.replace(b"\n", b"\r\n"))
        if not any(hashlib.sha256(data).hexdigest() == expected for data in candidates):
            raise ValueError(f"已保存文件发生修改，校验失败：{path}")
    return actual


@contextmanager
def run_lock(run_dir):
    """系统文件锁：同一目录只能有一个采集进程；进程退出后自动释放。"""
    run_dir.mkdir(parents=True, exist_ok=True)
    with (run_dir / ".collector.lock").open("a+b") as file:
        if file.seek(0, os.SEEK_END) == 0:
            file.write(b"0")
            file.flush()
        file.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(file, fcntl.LOCK_UN)

