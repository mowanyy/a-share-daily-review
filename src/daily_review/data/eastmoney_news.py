"""东财 7x24 快讯采集（隔夜预案消息源）。

API 说明：
  newsapi.eastmoney.com/kuaixun/v1/getlist_102_ajaxResult_{N}_{P}.html
  返回 JSONP: var ajaxResult = { "data": [...], "code": 0 };
  - 102 = 7x24 快讯栏目 ID
  - N = 每页条数
  - P = 页码
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

from daily_review.data.http_client import DEFAULT_HEADERS, get_text

_KUAIBASE = "https://newsapi.eastmoney.com/kuaixun/v1/getlist_102_ajaxResult_{pageSize}_{page}_.html"
_OVERNIGHT_START_HOUR = 17  # 昨日 17:00
_OVERNIGHT_END_HOUR = 9     # 今早 9:00

# 东财接口专用头（对齐 eastmoney_pool.EM_HEADERS，覆盖 http_client 默认的 sina Referer）
_EM_HEADERS = {
    **DEFAULT_HEADERS,
    "Referer": "https://quote.eastmoney.com/",
}


def _strip_jsonp(text: str) -> dict:
    """去掉 JSONP 外包装（var ajaxResult = {...}），返回纯 dict。"""
    text = text.strip()
    # 去掉 var 前缀和尾部分号
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        text = m.group()
    return json.loads(text)


def fetch_kuaixun(page_size: int = 50, page: int = 1) -> list[dict]:
    """获取东财 7x24 快讯列表。

    返回格式：
        { "title": str, "content": str, "show_time": str, "source": str }
    按时间倒序（最新在前）。
    """
    url = _KUAIBASE.format(pageSize=page_size, page=page)
    raw = get_text(url, encoding="utf-8", headers=_EM_HEADERS)
    data = _strip_jsonp(raw)
    items = data.get("LivesList") or data.get("data") or []
    results = []
    for item in items:
        results.append({
            "title": (item.get("title") or "").strip(),
            "content": (item.get("digest") or item.get("simdigest") or "").strip(),
            "show_time": (item.get("showtime") or item.get("showTime") or "").strip(),
            "source": (item.get("simtype_zh") or item.get("sourceType") or "").strip(),
        })
    return results


def filter_overnight(items: list[dict], trade_date: str) -> list[dict]:
    """过滤隔夜消息：昨日 17:00 到今早 9:00 之间发布的。

    trade_date: 今天日期 YYYYMMDD（用于推算「昨日」）。
    """
    today = datetime.strptime(trade_date, "%Y%m%d")
    yesterday = today - timedelta(days=1)
    start = yesterday.replace(hour=_OVERNIGHT_START_HOUR, minute=0, second=0)
    end = today.replace(hour=_OVERNIGHT_END_HOUR, minute=0, second=0)

    filtered = []
    for item in items:
        t = item.get("show_time", "")
        if not t:
            continue
        try:
            dt = datetime.strptime(t, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            try:
                dt = datetime.strptime(t, "%Y-%m-%d %H:%M")
            except ValueError:
                continue
        if start <= dt <= end:
            filtered.append(item)
    return filtered


def fetch_overnight_news(trade_date: str, page_size: int = 50, max_pages: int = 3) -> list[dict]:
    """一站式获取隔夜消息（多页拉取直到覆盖隔夜窗口，返回去重后的隔夜消息）。

    分页策略：从第 1 页起逐页拉取，若该页最早一条已早于窗口起点（昨日 17:00），
    说明后续页更旧、无需再拉；否则继续下一页，最多 max_pages 页。
    """
    today = datetime.strptime(trade_date, "%Y%m%d")
    window_start = (today - timedelta(days=1)).replace(hour=_OVERNIGHT_START_HOUR, minute=0, second=0)

    seen: set[str] = set()
    results: list[dict] = []
    for page in range(1, max_pages + 1):
        items = fetch_kuaixun(page_size=page_size, page=page)
        if not items:
            break
        page_min_dt = None
        for it in items:
            t = it.get("show_time", "")
            if not t:
                continue
            try:
                dt = datetime.strptime(t, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            if page_min_dt is None or dt < page_min_dt:
                page_min_dt = dt
            key = f"{t}|{it.get('title', '')}"
            if key in seen:
                continue
            seen.add(key)
            if dt >= window_start:
                results.append(it)
        # 该页最早一条已早于窗口起点 → 后续页更旧，停止翻页
        if page_min_dt is not None and page_min_dt < window_start:
            break
    return results