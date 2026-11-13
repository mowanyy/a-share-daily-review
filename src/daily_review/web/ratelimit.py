"""进程内滑动窗口限流器（v0.36.3 安全加固：LLM 接口成本保护）。

Web 工作台只服务 127.0.0.1 本机、无认证——任何本机进程/恶意网页脚本都可直接
POST 触发 LLM 调用（/api/qa/ask、/api/fund/analyze、/api/agents/consult），
无限刷会烧光 DeepSeek 额度。这里对每个端点做**进程内**滑动窗口限流：
窗口内超过上限的请求返回 429，窗口滑动后自动恢复。

- 线程安全（Web 多线程 + 测试并发）
- 单进程语义即可：本机单用户工具，按「端点名」限流（不按 IP——恒为 127.0.0.1）
- `clear_limits()` 供测试隔离与手动重置

窗口大小与上限可用环境变量调节（默认 20 次/分钟，对个人使用足够宽松）：
    WEB_LLM_RATE_LIMIT   每窗口允许的请求数（默认 20）
    WEB_LLM_RATE_WINDOW  窗口长度秒（默认 60）
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque

_DEFAULT_LIMIT = 20
_DEFAULT_WINDOW = 60


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def default_limit() -> int:
    """每窗口允许的请求数（WEB_LLM_RATE_LIMIT，默认 20）。"""
    return _env_int("WEB_LLM_RATE_LIMIT", _DEFAULT_LIMIT)


def default_window() -> int:
    """窗口长度秒（WEB_LLM_RATE_WINDOW，默认 60）。"""
    return _env_int("WEB_LLM_RATE_WINDOW", _DEFAULT_WINDOW)


class SlidingWindowLimiter:
    """滑动窗口限流器：窗口内最多 limit 次，线程安全。

    实现：按到达时间戳的 deque，每次检查时先滑出窗口外的旧记录，
    再判断当前窗口内计数是否已达上限。
    """

    def __init__(self, limit: int, window_seconds: int):
        self.limit = limit
        self.window = window_seconds
        self._hits: deque[float] = deque()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        """尝试占用一个配额。窗口内未超限 → True 并记录；已超限 → False。"""
        now = time.monotonic()
        with self._lock:
            while self._hits and now - self._hits[0] > self.window:
                self._hits.popleft()
            if len(self._hits) >= self.limit:
                return False
            self._hits.append(now)
            return True

    def reset(self) -> None:
        """清空窗口记录（测试隔离 / 手动重置）。"""
        with self._lock:
            self._hits.clear()

    def remaining(self) -> int:
        """当前窗口内剩余可用配额（诊断/日志用）。"""
        now = time.monotonic()
        with self._lock:
            while self._hits and now - self._hits[0] > self.window:
                self._hits.popleft()
            return max(0, self.limit - len(self._hits))


# ---------------------------------------------------------------- 全局端点限流器

_LLM_ENDPOINTS = ("qa", "fund", "consult")  # 三个 LLM 消耗端点
_LIMITERS: dict[str, SlidingWindowLimiter] = {}
_LIMITERS_LOCK = threading.Lock()


def limiter(name: str) -> SlidingWindowLimiter:
    """取/建指定端点的限流器（按环境变量配置）。"""
    with _LIMITERS_LOCK:
        lim = _LIMITERS.get(name)
        if lim is None:
            lim = SlidingWindowLimiter(limit=default_limit(), window_seconds=default_window())
            _LIMITERS[name] = lim
        return lim


def clear_limits() -> None:
    """清空所有端点限流状态（测试隔离：autouse fixture 前后调用）。"""
    with _LIMITERS_LOCK:
        for lim in _LIMITERS.values():
            lim.reset()


def endpoint_names() -> tuple[str, ...]:
    return _LLM_ENDPOINTS
