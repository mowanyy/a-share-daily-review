"""东方财富龙虎榜（LHB）接口（v0.4）。

数据中心 datacenter-web（**历史日期可用**，盘后更新）：
- 每日榜单  `RPT_DAILYBILLBOARD_DETAILSNEW`：上榜股票 + 净买额 + 上榜原因 + 买卖席位数
- 买卖席位  `RPT_OPERATEDEPT_TRADE_DETAILS`：营业部 × 股票 的买/卖/净额（当日全量，分页）

时间说明：龙虎榜盘后（约 18:00 起）更新；盘中/未更新日返回空表，调用方应优雅降级。

去重约定（在 analysis/lhb.py 实现）：
- 一只股票因多个上榜原因出现多行 → 按代码去重，取 |净额| 最大的一条为准，原因合并；
- 同一 (股票, 营业部) 因多原因重复 → 同上取 |净额| 最大一条，避免重复计数。
"""

from __future__ import annotations

from urllib.parse import urlencode

import pandas as pd

from daily_review.data.eastmoney_pool import _ensure_list

from daily_review.data.eastmoney_pool import EM_HEADERS, _num, _throttle
from daily_review.data.http_client import get_json

LHB_BASE = "https://datacenter-web.eastmoney.com"
PAGE_SIZE = 500

# 每日榜单字段 → DataFrame 列
LHB_DAILY_COLUMNS = [
    "trade_date", "code", "secucode", "name", "close_price", "change_rate",
    "turnover_rate", "accum_amount", "lhb_net_amt", "lhb_buy_amt", "lhb_sell_amt",
    "deal_amount_ratio", "deal_net_ratio", "reason", "reason_type",
    "market", "trade_market", "buy_seats", "sell_seats", "buy_ratio", "sell_ratio",
]
# 买卖席位字段 → DataFrame 列
LHB_SEAT_COLUMNS = [
    "trade_date", "code", "name", "seat_code", "seat_name", "seat_abbr",
    "act_buy", "act_sell", "net_amt", "reason", "change_rate",
]


def _lhb_page(report_name: str, filter_str: str, page: int, page_size: int,
              sort_columns: str | None = None, sort_types: str | None = None) -> list[dict]:
    """拉取一页龙虎榜数据中心数据，返回 data 列表（空表时返回 []）。"""
    params = {
        "reportName": report_name,
        "columns": "ALL",
        "source": "WEB",
        "client": "WEB",
        "pageNumber": page,
        "pageSize": page_size,
        "filter": filter_str,
    }
    if sort_columns:
        params["sortColumns"] = sort_columns
        params["sortTypes"] = sort_types or "-1"
    _throttle()
    url = f"{LHB_BASE}/api/data/v1/get?{urlencode(params)}"
    payload = get_json(url, headers=EM_HEADERS)
    if not payload.get("success"):
        return []
    result = payload.get("result") or {}
    return _ensure_list(result.get("data"), "data", "lhb")


def _paginate(report_name: str, filter_str: str,
              sort_columns: str | None = None) -> list[dict]:
    """分页拉全量龙虎榜数据。"""
    rows: list[dict] = []
    page = 1
    for _ in range(20):  # 防死循环兜底（上限 20 页 = 1 万条）
        batch = _lhb_page(report_name, filter_str, page, PAGE_SIZE, sort_columns)
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        page += 1
    return rows


def _filter_date(trade_date: str) -> str:
    """YYYYMMDD → 东财 filter 里的 'YYYY-MM-DD'。"""
    return f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"


def fetch_lhb_daily(trade_date: str) -> pd.DataFrame:
    """每日龙虎榜榜单（原始行，一只股票可能因多原因多行，去重交给分析层）。

    列：trade_date/code/secucode/name/close_price/change_rate/turnover_rate/
        accum_amount/lhb_net_amt/lhb_buy_amt/lhb_sell_amt/deal_amount_ratio/
        deal_net_ratio/reason/reason_type/market/trade_market/buy_seats/sell_seats/
        buy_ratio/sell_ratio
    """
    date_s = _filter_date(trade_date)
    rows = _paginate(
        "RPT_DAILYBILLBOARD_DETAILSNEW",
        f"(TRADE_DATE='{date_s}')",
        sort_columns="BILLBOARD_NET_AMT",
    )
    out = []
    for r in rows:
        out.append({
            "trade_date": trade_date,
            "code": str(r.get("SECURITY_CODE", "")),
            "secucode": str(r.get("SECUCODE", "")),
            "name": str(r.get("SECURITY_NAME_ABBR", "")),
            "close_price": _num(r.get("CLOSE_PRICE")),
            "change_rate": _num(r.get("CHANGE_RATE")),
            "turnover_rate": _num(r.get("TURNOVERRATE")),
            "accum_amount": _num(r.get("ACCUM_AMOUNT")),
            "lhb_net_amt": _num(r.get("BILLBOARD_NET_AMT")),
            "lhb_buy_amt": _num(r.get("BILLBOARD_BUY_AMT")),
            "lhb_sell_amt": _num(r.get("BILLBOARD_SELL_AMT")),
            "deal_amount_ratio": _num(r.get("DEAL_AMOUNT_RATIO")),
            "deal_net_ratio": _num(r.get("DEAL_NET_RATIO")),
            "reason": str(r.get("EXPLANATION", "")),
            "reason_type": str(r.get("EXPLAIN", "")),
            "market": str(r.get("MARKET", "")),
            "trade_market": str(r.get("TRADE_MARKET", "")),
            "buy_seats": _num(r.get("BUY_SEAT")),
            "sell_seats": _num(r.get("SELL_SEAT")),
            "buy_ratio": _num(r.get("BUY_RATIO")),
            "sell_ratio": _num(r.get("SELL_RATIO")),
        })
    df = pd.DataFrame(out, columns=LHB_DAILY_COLUMNS)
    for col in ("close_price", "change_rate", "turnover_rate", "accum_amount",
                "lhb_net_amt", "lhb_buy_amt", "lhb_sell_amt", "deal_amount_ratio",
                "deal_net_ratio", "buy_seats", "sell_seats", "buy_ratio", "sell_ratio"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def fetch_lhb_seats(trade_date: str) -> pd.DataFrame:
    """当日买卖席位成交明细（营业部 × 股票，全量分页）。

    列：trade_date/code/name/seat_code/seat_name/seat_abbr/
        act_buy/act_sell/net_amt/reason/change_rate
    """
    date_s = _filter_date(trade_date)
    rows = _paginate(
        "RPT_OPERATEDEPT_TRADE_DETAILS",
        f"(TRADE_DATE='{date_s}')",
        sort_columns="NET_AMT",
    )
    out = []
    for r in rows:
        out.append({
            "trade_date": trade_date,
            "code": str(r.get("SECURITY_CODE", "")),
            "name": str(r.get("SECURITY_NAME_ABBR", "")),
            "seat_code": str(r.get("OPERATEDEPT_CODE", "")),
            "seat_name": str(r.get("OPERATEDEPT_NAME", "")),
            "seat_abbr": str(r.get("ORG_NAME_ABBR", "")),
            "act_buy": _num(r.get("ACT_BUY")),
            "act_sell": _num(r.get("ACT_SELL")),
            "net_amt": _num(r.get("NET_AMT")),
            "reason": str(r.get("EXPLANATION", "")),
            "change_rate": _num(r.get("CHANGE_RATE")),
        })
    df = pd.DataFrame(out, columns=LHB_SEAT_COLUMNS)
    for col in ("act_buy", "act_sell", "net_amt", "change_rate"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df
