"""数据落盘：data/{trade_date}/{name}.csv（目录约定见 docs/数据结构.md）。"""

from __future__ import annotations

import os
import tempfile
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
    原子写：先写同目录唯一临时文件，再 os.replace 换入——Web 工作台可能并发
    collect（复盘任务线程 + 看板请求线程），避免 torn/半截 CSV 被 load_csv 读到。
    """
    if name.endswith(".csv"):
        name = name[:-4]
    path = _date_dir(trade_date) / f"{name}.csv"
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    os.close(fd)
    try:
        df.to_csv(tmp_name, index=index, encoding="utf-8-sig")
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
    return path


def load_csv(name: str, trade_date: str | None = None) -> pd.DataFrame:
    """读取 data/{trade_date}/{name}.csv。"""
    if name.endswith(".csv"):
        name = name[:-4]
    path = _date_dir(trade_date) / f"{name}.csv"
    return pd.read_csv(path)
