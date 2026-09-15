"""跨平台非阻塞文件锁（v0.23，A1）：多 worker 部署的全局单飞锁。

`JobManager._running` 只是进程内存变量，gunicorn workers>1 时会穿透——两个 worker
各自认为"我在跑"，并发执行两个复盘任务。这里用**文件锁**补上跨进程互斥：

- Windows：`msvcrt.locking`（锁定文件首字节，非阻塞 `LK_NBLCK`）
- POSIX：`fcntl.flock`（`LOCK_EX|LOCK_NB`）
- 失败立即抛 `LockHeld`（上层转 JobBusy → HTTP 409，与现状单飞行为一致）
- 进程崩溃时 OS 自动释放锁（fd 关闭即解锁），锁文件本身残留无害
"""

from __future__ import annotations

import os

_LOCK_BYTE = 1


class LockHeld(RuntimeError):
    """锁已被其他进程持有（非阻塞获取失败）。"""


class FileLock:
    """进程级互斥文件锁。用法：with FileLock(path) as lock: ..."""

    def __init__(self, path):
        self._path = str(path)
        self._fd: int | None = None

    def acquire(self) -> None:
        """非阻塞获取锁；被占用抛 LockHeld（不等待）。"""
        if self._fd is not None:
            return
        parent = os.path.dirname(os.path.abspath(self._path))
        os.makedirs(parent, exist_ok=True)
        fd = os.open(self._path, os.O_CREAT | os.O_RDWR)
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, _LOCK_BYTE)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            raise LockHeld(f"任务锁被占用：{self._path}") from exc
        self._fd = fd

    def release(self) -> None:
        """释放锁并关闭 fd（幂等，可重复调用）。"""
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, _LOCK_BYTE)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    # ------------------------------------------------------------- 上下文管理器

    def __enter__(self) -> "FileLock":
        self.acquire()
        return self

    def __exit__(self, *exc_info) -> None:
        self.release()