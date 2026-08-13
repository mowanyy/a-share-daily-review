"""竞价指标计算：高开幅度、竞价量能。

供开盘策略（9:25-9:30）使用，基于新浪实时行情获取竞价数据。
"""

from __future__ import annotations

import math

import pandas as pd

from daily_review.data.sina import fetch_realtime


def fetch_auction_data(codes: list[str]) -> pd.DataFrame:
    """获取竞价数据（9:25 后调用）。

    codes: 股票代码列表（6 位数字，如 ["600601", "002398"]）。
    返回 DataFrame，含 stock_code, stock_name, open, pre_close, volume, amount。
    """
    df = fetch_realtime(codes)
    return df


def compute_auction(
    quotes: pd.DataFrame,
    prev_volume_map: dict[str, float] | None = None,
    prev_seal_map: dict[str, float] | None = None,
) -> list[dict]:
    """计算竞价指标。

    quotes: 实时行情 DataFrame（含 stock_code, open, pre_close, volume, amount）。
    prev_volume_map: {code: 昨日成交量}，用于计算竞价量比。
    prev_seal_map: {code: 昨日封单金额}，用于对比。

    返回 [{code, name, auction_pct, auction_volume, auction_amount, auction_ratio, ...}]。
    """
    prev_volume_map = prev_volume_map or {}
    prev_seal_map = prev_seal_map or {}
    rows = []
    for _, r in quotes.iterrows():
        code = str(r.get("stock_code", ""))
        pre_close = r.get("pre_close")
        open_price = r.get("open")
        volume = r.get("volume") or 0
        amount = r.get("amount") or 0
        prev_vol = prev_volume_map.get(code, 0)

        if pre_close and open_price and pre_close > 0 and not math.isnan(pre_close):
            auction_pct = (open_price - pre_close) / pre_close * 100
        else:
            auction_pct = None

        auction_ratio = (volume / prev_vol) if prev_vol and prev_vol > 0 else None
        prev_seal = prev_seal_map.get(code)

        rows.append({
            "code": code,
            "name": str(r.get("stock_name", "")),
            "auction_pct": round(auction_pct, 2) if auction_pct is not None else None,
            "auction_volume": int(volume) if volume else 0,
            "auction_amount": float(amount) if amount else 0.0,
            "auction_ratio": round(auction_ratio, 2) if auction_ratio is not None else None,
            "open": open_price,
            "pre_close": pre_close,
            "prev_seal": prev_seal,
        })
    return rows