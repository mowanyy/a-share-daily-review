"""HTTP 请求封装：UA/超时/重试/间隔。

统一使用 requests（参考共享脚本 `获取实时行情数据.py` 的 requestForNew 模式，
把 urllib 换成 requests，保留 UA 伪装、失败重试、间隔退避）。
"""

from __future__ import annotations

import time

import requests

from daily_review.config import get_settings

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/97.0.4692.71 Safari/537.36 Edg/97.0.1072.62"
    ),
    "Referer": "https://finance.sina.com.cn",
}


def get(
    url: str,
    *,
    headers: dict | None = None,
    timeout: float = 15,
    max_try_num: int | None = None,
    sleep_time: float | None = None,
) -> requests.Response:
    """GET 请求，失败按指数退避重试；全部失败抛出最后一次异常。"""
    settings = get_settings()
    max_try_num = max_try_num or settings.max_retries
    sleep_time = settings.request_interval if sleep_time is None else sleep_time
    hdrs = {**DEFAULT_HEADERS, **(headers or {})}

    last_exc: Exception | None = None
    for attempt in range(max_try_num):
        try:
            resp = requests.get(url, headers=hdrs, timeout=timeout)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < max_try_num - 1:
                time.sleep(sleep_time * (2**attempt))
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"请求失败（max_try_num={max_try_num}）: {url}")


def get_json(url: str, **kw) -> dict:
    """GET 并解析 JSON。"""
    return get(url, **kw).json()


def get_text(url: str, encoding: str = "gbk", **kw) -> str:
    """GET 并按指定编码解码文本（新浪行情为 gbk）。"""
    resp = get(url, **kw)
    return resp.content.decode(encoding, errors="replace")
