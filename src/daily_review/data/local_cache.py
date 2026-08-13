"""静态缓存：跨日全局数据（行业映射 / 交易日历 / 概念成分）落盘 data/cache/。

与 data/{日期}/*.csv 分日缓存不同，这些数据是全局/跨日的，不适合按日期分目录：
  - industry_map.csv        全市场 代码→行业 全名映射（基本不变，7 天 TTL）
  - trade_dates.csv         已确认交易日历（每次解析后追加，长期有效）
  - board_constituents/     概念板块成分股（{board_code}.csv，3 天 TTL）

所有缓存带 mtime 时效判断；过期自动重拉并覆盖。TTL 由调用方从东财重新拉取。
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from daily_review.config import get_settings

INDUSTRY_MAP_TTL = 7 * 24 * 3600      # 行业映射：7 天
BOARD_CONSTITUENTS_TTL = 3 * 24 * 3600  # 概念成分：3 天
TRADE_DATES_TTL = 30 * 24 * 3600      # 交易日历：30 天


def cache_dir() -> Path:
    return get_settings().cache_dir


def _fresh(path: Path, ttl: float) -> bool:
    """文件存在且未超 TTL。"""
    try:
        return path.exists() and (time.time() - path.stat().st_mtime) <= ttl
    except OSError:
        return False


# ---------------------------------------------------------------- 行业映射

def industry_map_path() -> Path:
    return cache_dir() / "industry_map.csv"


def load_industry_map() -> dict[str, str] | None:
    """读取行业映射缓存；不存在或过期返回 None（调用方重新拉取）。"""
    path = industry_map_path()
    if not _fresh(path, INDUSTRY_MAP_TTL):
        return None
    try:
        df = pd.read_csv(path, dtype=str)
        return dict(zip(df["code"], df["industry"]))
    except Exception:
        return None


def save_industry_map(mapping: dict[str, str]) -> Path:
    """全量落盘行业映射。"""
    path = industry_map_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(
        [{"code": c, "industry": i} for c, i in mapping.items()],
        columns=["code", "industry"],
    )
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path


# ---------------------------------------------------------------- 概念成分

def board_constituents_path(board_code: str) -> Path:
    return cache_dir() / "board_constituents" / f"{board_code}.csv"


def load_board_constituents(board_code: str) -> list[str] | None:
    """读取概念板块成分股缓存；不存在/过期返回 None。"""
    path = board_constituents_path(board_code)
    if not _fresh(path, BOARD_CONSTITUENTS_TTL):
        return None
    try:
        df = pd.read_csv(path, dtype=str)
        return [c for c in df["code"] if c]
    except Exception:
        return None


def save_board_constituents(board_code: str, codes: list[str]) -> Path:
    path = board_constituents_path(board_code)
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"code": codes}).to_csv(path, index=False, encoding="utf-8-sig")
    return path


# ---------------------------------------------------------------- 交易日历

def trade_dates_path() -> Path:
    return cache_dir() / "trade_dates.csv"


def load_trade_dates() -> set[str]:
    """读取已确认的交易日集合（YYYYMMDD）。"""
    path = trade_dates_path()
    if not path.exists():
        return set()
    try:
        df = pd.read_csv(path, dtype=str)
        return set(df["date"])
    except Exception:
        return set()


def save_trade_dates(dates: set[str]) -> Path:
    path = trade_dates_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"date": sorted(dates)}).to_csv(path, index=False, encoding="utf-8-sig")
    return path


def add_trade_dates(dates: set[str]) -> Path:
    """合并新增交易日到日历缓存（读旧集合并集后全量落盘）。"""
    merged = load_trade_dates() | set(dates)
    return save_trade_dates(merged)


# ---------------------------------------------------------------- 刷新（update-data 用）

def refresh_all(fetch_industry_map, fetch_trade_dates, *, force: bool = False) -> dict:
    """刷新全部静态缓存。

    fetch_industry_map/fetch_trade_dates: 重拉函数（避免此处 import 东财模块造成循环依赖）。
    force: True 强制重拉（无视 TTL）；False 仅拉过期/缺失的。
    返回 {"industry_map": bool, "trade_dates": bool} 各自是否重拉。
    """
    result = {"industry_map": False, "trade_dates": False}

    if force or load_industry_map() is None:
        try:
            mapping = fetch_industry_map()
            if mapping:
                save_industry_map(mapping)
                result["industry_map"] = True
        except Exception:
            pass

    if force or not trade_dates_path().exists():
        try:
            dates = fetch_trade_dates()
            if dates:
                add_trade_dates(dates)
                result["trade_dates"] = True
        except Exception:
            pass

    return result