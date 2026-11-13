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


def build_prev_maps(zt: pd.DataFrame) -> tuple[dict[str, float], dict[str, float]]:
    """从昨日涨停池提取 {code: 昨日封单金额} 与 {code: 昨日成交额}。

    v0.31：统一映射构建，修复「昨日封单」取错列名的 bug——落盘 CSV 列名为
    seal_amount（东财原始字段 fund 写盘时已重命名），此前各调用点判
    `"fund" in zt.columns` 恒为 False 导致 prev_seal 永远缺失。这里兼容
    两种列名（seal_amount 优先、fund 兜底），并顺带取 amount 供竞价量能比。
    """
    seal_col = "seal_amount" if "seal_amount" in zt.columns else ("fund" if "fund" in zt.columns else None)
    seal_map: dict[str, float] = {}
    amount_map: dict[str, float] = {}
    for _, r in zt.iterrows():
        code = str(r.get("code", ""))
        if not code:
            continue
        if seal_col is not None:
            v = r.get(seal_col)
            if v is not None and pd.notna(v):
                seal_map[code] = float(v)
        v = r.get("amount")
        if v is not None and pd.notna(v):
            amount_map[code] = float(v)
    return seal_map, amount_map


def compute_auction(
    quotes: pd.DataFrame,
    prev_amount_map: dict[str, float] | None = None,
    prev_seal_map: dict[str, float] | None = None,
) -> list[dict]:
    """计算竞价指标。

    quotes: 实时行情 DataFrame（含 stock_code, open, pre_close, volume, amount）。
    prev_amount_map: {code: 昨日成交额}，用于计算竞价量能比（竞价成交额/昨日成交额）。
    prev_seal_map: {code: 昨日封单金额}，用于对比。

    返回 [{code, name, auction_pct, auction_volume, auction_amount, auction_ratio, ...}]。
    """
    prev_amount_map = prev_amount_map or {}
    prev_seal_map = prev_seal_map or {}
    rows = []
    for _, r in quotes.iterrows():
        code = str(r.get("stock_code", ""))
        pre_close = r.get("pre_close")
        open_price = r.get("open")
        volume = r.get("volume") or 0
        amount = r.get("amount") or 0
        prev_amount = prev_amount_map.get(code, 0)

        if pre_close and open_price and pre_close > 0 and not math.isnan(pre_close):
            auction_pct = (open_price - pre_close) / pre_close * 100
        else:
            auction_pct = None

        auction_ratio = (amount / prev_amount) if prev_amount and prev_amount > 0 else None
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