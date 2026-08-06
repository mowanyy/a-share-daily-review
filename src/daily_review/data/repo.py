"""数据落盘：data/{trade_date}/{name}.csv（目录约定见 docs/数据结构.md）。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from daily_review.config import get_settings


def _date_dir(trade_date: str | None = None) -> Path:
    settings = get_settings()
    if trade_date is None:
        trade_date = datetime.today().strftime("%Y%m%d")
    path = settings.data_dir / str(trade_date)
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_csv(
    df: pd.DataFrame,
    name: str,
    trade_date: str | None = None,
    *,
    index: bool = False,
) -> Path:
    """保存 DataFrame 到 data/{trade_date}/{name}.csv，返回完整路径。

    name 可带可不带 .csv 后缀。使用 utf-8-sig 便于 Excel 直接打开。
    """
    if name.endswith(".csv"):
        name = name[:-4]
    path = _date_dir(trade_date) / f"{name}.csv"
    df.to_csv(path, index=index, encoding="utf-8-sig")
    return path


def load_csv(name: str, trade_date: str | None = None) -> pd.DataFrame:
    """读取 data/{trade_date}/{name}.csv。"""
    if name.endswith(".csv"):
        name = name[:-4]
    path = _date_dir(trade_date) / f"{name}.csv"
    return pd.read_csv(path)
