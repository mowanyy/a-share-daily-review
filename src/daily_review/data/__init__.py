"""数据采集层（v0.2 起步）。

已实现：新浪实时行情（sina）、东方财富日 K 线（eastmoney）、CSV 落盘（repo）。
待实现（后续迭代）：涨停池/炸板池/资金流/题材等东财接口。
"""

from daily_review.data.eastmoney import build_kline_url, fetch_kline, secid_of
from daily_review.data.repo import load_csv, save_csv
from daily_review.data.sina import build_quote_url, fetch_realtime, prefix_of

__all__ = [
    "fetch_kline",
    "build_kline_url",
    "secid_of",
    "fetch_realtime",
    "build_quote_url",
    "prefix_of",
    "save_csv",
    "load_csv",
]
