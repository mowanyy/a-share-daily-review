"""数据采集层离线测试：URL 构造与解析纯函数，不联网。"""

from __future__ import annotations

import pandas as pd

from daily_review.data import eastmoney, sina
from daily_review.data.eastmoney import _parse_klines, secid_of
from daily_review.data.sina import _parse_line, build_quote_url, prefix_of


class TestSecid:
    def test_sh_main(self):
        assert secid_of("600000") == "1.600000"
        assert secid_of("688981") == "1.688981"  # 科创板
        assert secid_of("900901") == "1.900901"  # 沪 B

    def test_sz_and_bj(self):
        assert secid_of("000001") == "0.000001"
        assert secid_of("300750") == "0.300750"  # 创业板
        assert secid_of("430047") == "0.430047"  # 北交所（暂按 0.）


class TestPrefix:
    def test_prefix(self):
        assert prefix_of("600601") == "sh"
        assert prefix_of("002398") == "sz"
        assert prefix_of("300750") == "sz"
        assert prefix_of("830799") == "bj"
        assert prefix_of("900901") == "sh"


class TestQuoteUrl:
    def test_build_filters_invalid(self):
        url = build_quote_url(["600601", "002398", "", "abc", "300750"])
        assert url == "https://hq.sinajs.cn/list=sh600601,sz002398,sz300750"


class TestKlineParse:
    def test_parse_klines_columns_and_types(self):
        raw = ["2026-08-05,10.00,10.20,10.30,9.90,123456,1234567,3.0,2.0,0.2,1.5"]
        df = _parse_klines(raw)
        assert list(df.columns) == eastmoney.KLINE_COLUMNS
        assert df.loc[0, "trade_date"] == "2026-08-05"
        assert df.loc[0, "close"] == 10.20
        assert df.loc[0, "volume"] == 123456
        assert df.loc[0, "pct_change"] == 2.0


class TestSinaParse:
    @staticmethod
    def _make_line(fields: list[str]) -> str:
        payload = ",".join(fields)
        return f'var hq_str_sh600999="{payload}";'

    def test_parse_valid_line(self):
        fields = [""] * 33
        fields[0] = "某某股份"
        fields[1:10] = ["10.00", "9.80", "10.20", "10.30", "9.85", "10.19", "10.20", "123456", "1234567"]
        fields[30] = "2026-08-06"
        fields[31] = "15:00:00"
        row = _parse_line(self._make_line(fields))
        assert row is not None
        assert row["stock_code"] == "600999"
        assert row["stock_name"] == "某某股份"
        assert row["open"] == 10.00
        assert row["pre_close"] == 9.80
        assert row["close"] == 10.20
        assert row["buy1"] == 10.19
        assert row["volume"] == 123456
        assert row["amount"] == 1234567
        assert row["candle_end_time"] == pd.Timestamp("2026-08-06 15:00:00")

    def test_parse_empty_line_returns_none(self):
        # 停牌/退市等返回空数据串
        assert _parse_line('var hq_str_sh600000="";') is None

    def test_parse_garbage_line_returns_none(self):
        assert _parse_line("not a quote line") is None
        assert _parse_line('var hq_str_sh000000="该证券品种不存在";') is None
