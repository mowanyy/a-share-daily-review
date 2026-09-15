"""web/joblock.py 跨平台文件锁测试：同进程幂等 + 子进程跨进程互斥（全离线）。"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from daily_review.web.joblock import FileLock, LockHeld

_ROOT = Path(__file__).resolve().parents[1]


def test_acquire_release_roundtrip(tmp_path):
    lock = FileLock(tmp_path / "jobs.lock")
    lock.acquire()
    assert lock._fd is not None
    lock.acquire()  # 幂等：已持有不重复加锁
    lock.release()
    lock.release()  # 幂等：重复释放不炸
    assert lock._fd is None


def test_second_process_conflicts_until_release(tmp_path):
    """子进程持有文件锁 → 本进程非阻塞 acquire 抛 LockHeld；子进程释放后可重获。"""
    lock_file = tmp_path / "jobs.lock"
    hold_code = (
        "import sys, time\n"
        "from daily_review.web.joblock import FileLock\n"
        f'lock = FileLock(r"{lock_file}")\n'
        "lock.acquire()\n"
        "print('HELD', flush=True)\n"
        "time.sleep(1.5)\n"
    )
    env = {**os.environ, "PYTHONPATH": str(_ROOT / "src")}
    child = subprocess.Popen(
        [sys.executable, "-c", hold_code], env=env,
        stdout=subprocess.PIPE, text=True,
    )

    def _wait_held() -> bool:
        deadline = time.time() + 8
        while time.time() < deadline:
            if child.poll() is not None:
                return False  # 子进程提前退出
            line = child.stdout.readline()
            if "HELD" in line:
                return True
        return False

    try:
        assert _wait_held(), "子进程应输出 HELD（持有锁）"
        lock = FileLock(lock_file)
        with pytest.raises(LockHeld):
            lock.acquire()
        # 子进程退出（fd 关闭）→ OS 自动释放锁
        child.wait(timeout=10)
        lock.acquire()
        lock.release()
    finally:
        if child.poll() is None:
            child.kill()


def test_lock_file_is_regular_empty_after_release(tmp_path):
    lock_file = tmp_path / "jobs.lock"
    lock = FileLock(lock_file)
    lock.acquire()
    lock.release()
    assert lock_file.exists()
    assert lock_file.stat().st_size == 0