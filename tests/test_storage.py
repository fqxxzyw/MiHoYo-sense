"""断点替换遇到 Windows 文件占用时，重试且保持原子保存。"""

import errno
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hoyo_sense import storage


def windows_error(code):
    error = PermissionError(errno.EACCES, "模拟 Windows 文件占用")
    error.winerror = code
    return error


@pytest.mark.parametrize("code", [5, 32, 33])
def test_transient_replace_error_retries_without_rewriting_json(monkeypatch, tmp_path, code):
    path = tmp_path / "manifest.json"
    storage.save_json({"status": "old"}, path)
    original_replace = storage.os.replace
    attempts, waits = [], []

    def replace(source, target):
        attempts.append(Path(source).read_bytes())
        assert storage.read_json(path) == {"status": "old"}
        if len(attempts) <= 2:
            raise windows_error(code)
        original_replace(source, target)

    monkeypatch.setattr(storage.os, "replace", replace)
    monkeypatch.setattr(storage.time, "sleep", waits.append)
    storage.save_json({"status": "新断点"}, path)

    assert storage.read_json(path) == {"status": "新断点"}
    assert len(attempts) == 3 and attempts[0] == attempts[1] == attempts[2]
    assert len(waits) == 2 and all(delay > 0 for delay in waits)
    assert not path.with_name(path.name + ".tmp").exists()


def test_persistent_replace_error_is_bounded_and_preserves_old_checkpoint(monkeypatch, tmp_path):
    path = tmp_path / "manifest.json"
    storage.save_json({"status": "old"}, path)
    original = path.read_bytes()
    error = windows_error(5)
    waits = []

    def denied(*args):
        raise error

    monkeypatch.setattr(storage.os, "replace", denied)
    monkeypatch.setattr(storage.time, "sleep", waits.append)
    with pytest.raises(PermissionError) as caught:
        storage.save_json({"status": "new"}, path)

    assert caught.value is error
    assert waits and sum(waits) <= 6 and len(waits) <= 10
    assert path.read_bytes() == original
    assert storage.read_json(path.with_name(path.name + ".tmp")) == {"status": "new"}


@pytest.mark.parametrize("error", [
    PermissionError(errno.EACCES, "非 Windows 权限错误"),
    OSError(errno.ENOSPC, "磁盘空间不足"),
    windows_error(19),
])
def test_other_errors_propagate_without_retry(monkeypatch, tmp_path, error):
    def fail(*args):
        raise error

    monkeypatch.setattr(storage.os, "replace", fail)
    monkeypatch.setattr(storage.time, "sleep", lambda _: pytest.fail("此错误不应重试"))
    with pytest.raises(OSError) as caught:
        storage.save_json({}, tmp_path / "manifest.json")
    assert caught.value is error


@pytest.mark.skipif(os.name != "nt", reason="需要真实的 Windows 文件共享锁")
def test_real_windows_handle_blocks_replace_until_released(monkeypatch, tmp_path):
    import ctypes
    from ctypes import wintypes

    path = tmp_path / "manifest.json"
    storage.save_json({"status": "old"}, path)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    # GENERIC_READ、FILE_SHARE_READ | FILE_SHARE_WRITE、OPEN_EXISTING；不共享删除。
    handle = kernel32.CreateFileW(str(path), 0x80000000, 3, None, 3, 0x80, None)
    assert handle != wintypes.HANDLE(-1).value, ctypes.get_last_error()
    waits = []

    def release_on_retry(delay):
        nonlocal handle
        waits.append(delay)
        assert storage.read_json(path) == {"status": "old"}
        assert kernel32.CloseHandle(handle)
        handle = None

    monkeypatch.setattr(storage.time, "sleep", release_on_retry)
    try:
        storage.save_json({"status": "new"}, path)
    finally:
        if handle is not None:
            kernel32.CloseHandle(handle)
    assert len(waits) == 1
    assert storage.read_json(path) == {"status": "new"}
