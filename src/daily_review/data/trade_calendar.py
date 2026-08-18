"""交易日历（v0.23 A3）：以上证指数日K 实证生成权威交易日表。

原理：上证指数（secid=1.000001）日K 的每个日期 = 一个真实交易日。用它建表比
「涨停池探测」（极端行情可能误判）权威，也免去手写节假日表的维护与出错风险
（2026 春节 9 天等长假安排由交易所实际开市记录直接裁决）。

产物：data/trade_calendar.csv（gitignored；一列=交易日 YYYYMMDD，约 5 年 1300 行）。
- is_trade_date(date) -> bool | None：True=交易日 / False=休市 / None=无法判定（无表且取数失败或日期不在表内）
- recent_trade_dates(start, n_days)：表内由近及远回推 n_days 个交易日（纯离线）
- 表文件 30 天 TTL，过期自动补拉；CLI `calendar` 子命令可查看/强制更新

注意：本表只回答「这天是否交易」，不休市日即交易日；周六周日自然不在 K 线里=False。
"""

from __future__ import annotations

import datetime as _dt
import os
import re
from pathlib import Path

from daily_review.config import get_settings
from daily_review.data.http_client import get_json

_TABLE_FILENAME = "trade_calendar.csv"
_KLINE_URL = (
    "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    "?secid=1.000001&klt=101&fqt=0&lmt=1300&end=20500101"
    "&fields1=f1,f2,f3&fields2=f51"
)
_TTL_SECONDS = 30 * 24 * 3600  # 30 天：日K 是历史事实，月度刷新即可

_TABLE: set[str] | None = None  # 进程内缓存（YYYYMMDD 交易日集合）


# ---------------------------------------------------------------- 落盘


def _table_path() -> Path:
    return get_settings().data_dir / _TABLE_FILENAME


def save(dates: set[str]) -> Path:
    """原子写交易日表（tmp + os.replace）。"""
    p = _table_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text("\n".join(sorted(dates)) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return p


def _read_file() -> set[str]:
    p = _table_path()
    if not p.exists():
        return set()
    if os.path.getmtime(p) < _dt.datetime.now().timestamp() - _TTL_SECONDS:
        return set()  # 过期 → 视为无表，触发补拉
    out: set[str] = set()
    for ln in p.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if re.fullmatch(r"\d{8}", ln):
            out.add(ln)
    return out


# ---------------------------------------------------------------- 取数


def _fetch_kline_dates() -> set[str]:
    """拉上证指数日K 日期集合（真实交易日）。失败抛 OSError。"""
    data = get_json(_KLINE_URL, timeout=20)
    klines = (data.get("data") or {}).get("klines")
    if not klines:
        raise OSError("上证指数日K 返回为空")
    dates = {row.split(",")[0].replace("-", "") for row in klines if row}
    if not dates:
        raise OSError("上证指数日K 无日期")
    return dates


def reload() -> None:
    """清空内存缓存（测试隔离/强制重读文件）。"""
    global _TABLE
    _TABLE = None


def refresh() -> set[str]:
    """强制联网刷新交易日表并重置内存缓存。网络失败抛 OSError/ValueError。"""
    dates = _fetch_kline_dates()
    save(dates)
    reload()
    return dates


def _load() -> set[str]:
    """读表；无表或表过期（缺最近交易日）→ 联网补拉一次；失败保留旧表（stale 但比空好）。

    表过期判定：A 股最多周末休 2 天，表最新日期若早于 4 个自然日前（如长假的周一
    后首日）说明缺最近行情 → 需要刷新；否则视为有效。
    """
    global _TABLE
    if _TABLE is not None:
        return _TABLE
    try:
        dates = _read_file()
    except OSError:
        dates = set()
    if dates and not _is_stale(dates):
        _TABLE = dates
        return dates
    # 无表或过期 → 联网拉取；失败保留旧表（stale 但比空好）
    try:
        dates = _fetch_kline_dates()
        save(dates)
    except Exception:
        pass  # 保留旧 dates（可能非空 stale 或空集）
    _TABLE = dates
    return dates


def _is_stale(dates: set[str]) -> bool:
    """表是否缺最近交易日（需要刷新）：最新日期早于 4 个自然日前 → 过期。"""
    if not dates:
        return True
    threshold = (_dt.datetime.now().date() - _dt.timedelta(days=4)).strftime("%Y%m%d")
    return max(dates) < threshold


def _max_date(dates: set[str]) -> str:
    return max(dates) if dates else ""


# ---------------------------------------------------------------- 查询


def is_trade_date(date: str) -> bool | None:
    """date(YYYYMMDD) 是否交易日。

    - True：交易日（在表中）
    - False：非交易日（表覆盖范围内但无此日）
    - None：无法判定（无表且取数失败，或日期超出表覆盖=未来/未发生，勿当休市）
    """
    if not re.fullmatch(r"\d{8}", date):
        return None
    table = _load()
    if not table:
        return None
    if date > _max_date(table):
        return None  # 未来（表未覆盖）→ 未知，不能判 False（否则未来交易日被误判休市）
    return date in table


def recent_trade_dates(start: str, n_days: int) -> list[str]:
    """从 start（含）往前回推 n_days 个交易日，由近及远；表缺失返回 []。"""
    table = _load()
    if not table:
        return []
    out: list[str] = []
    cur = start
    guard = 0
    max_scan = n_days * 8 + 60
    while len(out) < n_days and guard < max_scan:
        if cur in table:
            out.append(cur)
        cur = _prev_day(cur)
        guard += 1
    return out


def holidays_of_year(year: int) -> list[str]:
    """当年「周一~周五但不在表中」的休市日（法定节假日等），升序。

    只统计表覆盖范围内（≤ 表最新交易日）的日期——未来未发生的工作日不算休市。
    """
    table = _load()
    if not table:
        return []
    maxd = _max_date(table)
    out: list[str] = []
    d = _dt.date(year, 1, 1)
    end = _dt.date(year, 12, 31)
    while d <= end:
        s = d.strftime("%Y%m%d")
        if s <= maxd and d.weekday() < 5 and s not in table:
            out.append(s)
        d += _dt.timedelta(days=1)
    return out


def _prev_day(date: str) -> str:
    d = _dt.datetime.strptime(date, "%Y%m%d").date()
    return (d - _dt.timedelta(days=1)).strftime("%Y%m%d")