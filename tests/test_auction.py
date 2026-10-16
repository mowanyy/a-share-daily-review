"""竞价指标离线回归测试：build_prev_maps 列名修复 + 竞价量能比计算。

v0.31：此前「昨日封单」因调用点判 `"fund" in zt.columns` 恒为 False 而永远缺失
（落盘列名为 seal_amount），「量比」也从未实现（prev_amount_map 无人传入）。
本测试锁定修复后的确定性行为。纯函数测试，不联网。
"""

from __future__ import annotations

import pandas as pd

from daily_review.analysis.auction import build_prev_maps, compute_auction


def _zt_df(**overrides) -> pd.DataFrame:
    """构造昨日涨停池 DataFrame（列名对齐落盘 CSV：seal_amount + amount）。"""
    data = {
        "trade_date": ["20260818", "20260818"],
        "code": ["600613", "002820"],
        "name": ["神奇制药", "桂发祥"],
        "lb_num": [4, 3],
        "seal_amount": [92817360.0, 150905417.0],
        "amount": [560836352.0, 50512768.0],
        "industry": ["化学制药", "休闲食品"],
    }
    data.update(overrides)
    return pd.DataFrame(data)


class TestBuildPrevMaps:
    def test_reads_seal_amount_column(self):
        """真实列名 seal_amount（落盘 CSV 列名）能正确提取封单，回归旧 bug。"""
        seal_map, amount_map = build_prev_maps(_zt_df())
        assert seal_map == {"600613": 92817360.0, "002820": 150905417.0}
        assert amount_map == {"600613": 560836352.0, "002820": 50512768.0}

    def test_compatible_with_old_fund_column(self):
        """东财原始字段 fund 列名亦兼容（数据管线中途使用场景）。"""
        df = _zt_df()
        df = df.drop(columns=["seal_amount"]).rename(columns={"amount": "fund_amount"})
        df["fund"] = [92817360.0, 150905417.0]
        seal_map, amount_map = build_prev_maps(df)
        assert seal_map == {"600613": 92817360.0, "002820": 150905417.0}
        # amount 列缺失 → 成交额映射为空，不抛异常
        assert amount_map == {}

    def test_nan_seal_skipped(self):
        """封单为 NaN 的行跳过（不写入映射），不影响有值股票。"""
        df = _zt_df(seal_amount=[float("nan"), 150905417.0])
        seal_map, _ = build_prev_maps(df)
        assert seal_map == {"002820": 150905417.0}


class TestComputeAuction:
    @staticmethod
    def _quotes() -> pd.DataFrame:
        """新浪实时行情形状（竞价口径：open 为竞价价）。"""
        return pd.DataFrame(
            [
                {"stock_code": "600613", "stock_name": "神奇制药",
                 "open": 11.01, "pre_close": 10.01, "volume": 200000, "amount": 2.2e8},
                {"stock_code": "002820", "stock_name": "桂发祥",
                 "open": 11.0, "pre_close": 12.0, "volume": 100000, "amount": 5.0e6},
                {"stock_code": "000001", "stock_name": "无前日数据",
                 "open": 5.0, "pre_close": 4.8, "volume": 5000, "amount": 2.5e5},
            ]
        )

    def test_ratio_and_seal_populated(self):
        """竞价量能比 = 竞价成交额/昨日成交额；昨日封单来自映射。"""
        rows = compute_auction(self._quotes(), prev_seal_map={"600613": 92817360.0},
                               prev_amount_map={"600613": 560836352.0, "002820": 50512768.0})
        by_code = {r["code"]: r for r in rows}

        r = by_code["600613"]
        assert r["auction_pct"] == round((11.01 - 10.01) / 10.01 * 100, 2)
        # 2.2e8 / 560836352 ≈ 0.39
        assert r["auction_ratio"] is not None and round(r["auction_ratio"], 2) == 0.39
        assert r["prev_seal"] == 92817360.0

        r = by_code["002820"]
        assert r["auction_ratio"] is not None and r["auction_ratio"] == round(5.0e6 / 50512768.0, 2)
        assert r["prev_seal"] is None  # 未提供封单映射 → None（不抛异常）

    def test_missing_prev_data_omitted(self):
        """前日数据缺失（无映射）→ 字段为 None，调用方省略不写，不抛异常。"""
        rows = compute_auction(self._quotes())
        r = rows[2]
        assert r["code"] == "000001"
        assert r["auction_ratio"] is None
        assert r["prev_seal"] is None
        assert r["auction_pct"] is not None  # 竞价涨跌照常计算