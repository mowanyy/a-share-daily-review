"""数据采集层。

v0.2：新浪实时行情（sina）、东方财富日 K 线（eastmoney）、CSV 落盘（repo）。
v0.3：东财涨跌停池/资金流/概念板块（eastmoney_pool）。
v0.4：东财龙虎榜/买卖席位（eastmoney_lhb）+ 知名游资名单（hotmoney_seats）。
"""

from daily_review.data import eastmoney_lhb, eastmoney_pool, hotmoney_seats
from daily_review.data.eastmoney import build_kline_url, fetch_kline, secid_of
from daily_review.data.repo import load_csv, save_csv
from daily_review.data.sina import build_quote_url, fetch_realtime, prefix_of

__all__ = [
    # 行情（v0.2）
    "fetch_kline",
    "build_kline_url",
    "secid_of",
    "fetch_realtime",
    "build_quote_url",
    "prefix_of",
    # CSV 落盘
    "save_csv",
    "load_csv",
    # 东财池子/资金流/板块（v0.3）
    "eastmoney_pool",
    # 龙虎榜/游资（v0.4）
    "eastmoney_lhb",
    "hotmoney_seats",
]
