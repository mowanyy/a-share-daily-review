"""东方财富 K 线接口（移植自共享脚本：push2his .../stock/kline/get）。"""

from __future__ import annotations

from urllib.parse import urlencode

import pandas as pd

from daily_review.data.http_client import get_json

KLINE_BASE = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
FIELDS1 = "f1,f2,f3,f4,f5,f6"
FIELDS2 = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"

# 东财 kline 的 f51..f61 字段（顺序固定）
KLINE_COLUMNS = [
    "trade_date",
    "open",
    "close",
    "high",
    "low",
    "volume",
    "amount",
    "amplitude",  # 振幅 %
    "pct_change",  # 涨跌幅 %
    "change",  # 涨跌额
    "turnover",  # 换手率 %
]


def secid_of(code: str) -> str:
    """股票代码 → 东财 secid 市场前缀。

    6/9 开头（沪市主板/科创板/B股）→ `1.`；其余（0/3 深市、4/8 北交所）→ `0.`。
    北交所东财 secid 亦为 `0.`，暂统一处理。
    """
    code = code.strip()
    if code.startswith(("6", "9")):
        return "1." + code
    return "0." + code


def build_kline_url(
    code: str,
    klt: int = 101,
    fqt: int = 0,
    end: str = "20500101",
    lmt: int = 120,
    **extra,
) -> str:
    """构造东财 K 线请求 URL。klt: 101=日线 102=周线；fqt: 0=不复权 1=前复权 2=后复权。"""
    params = {
        "secid": secid_of(code),
        "fields1": FIELDS1,
        "fields2": FIELDS2,
        "klt": klt,
        "fqt": fqt,
        "end": end,
        "lmt": lmt,
    }
    params.update(extra)
    return f"{KLINE_BASE}?{urlencode(params)}"


def _parse_klines(klines: list[str]) -> pd.DataFrame:
    """把东财返回的逗号分隔 kline 行解析为 DataFrame，数值列转为 numeric。"""
    rows = [line.split(",") for line in klines]
    df = pd.DataFrame(rows, columns=KLINE_COLUMNS)
    for col in KLINE_COLUMNS[1:]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def fetch_kline(
    code: str,
    klt: int = 101,
    fqt: int = 0,
    end: str = "20500101",
    lmt: int = 120,
    **extra,
) -> pd.DataFrame:
    """获取单只股票 K 线（日/周/月），返回 DataFrame。"""
    url = build_kline_url(code, klt=klt, fqt=fqt, end=end, lmt=lmt, **extra)
    payload = get_json(url)
    data = payload.get("data")
    if not data or not data.get("klines"):
        raise ValueError(f"K 线数据为空（代码 {code}，URL {url}）")
    return _parse_klines(data["klines"])
