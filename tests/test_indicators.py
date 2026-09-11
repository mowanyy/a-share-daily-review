"""指标层离线测试：晋级率 / 题材阶段与龙头 / 炸板信号（不联网）。"""

from __future__ import annotations

import pandas as pd

from daily_review.analysis.break_flow import analyze_break
from daily_review.analysis.ladder import (_compute_height_position, compute_ladder,
                                            compute_promotion)
from daily_review.analysis.theme import build_themes

ZT_COLS = [
    "trade_date", "code", "name", "lb_num", "first_limit_time",
    "last_limit_time", "open_times", "seal_amount", "turnover", "amount", "industry",
]


def _mkzt(rows, date="20260806") -> pd.DataFrame:
    data = []
    for r in rows:
        data.append({
            "trade_date": date,
            "code": r["code"],
            "name": r.get("name", r["code"]),
            "lb_num": r["lb_num"],
            "first_limit_time": r.get("first_limit_time", "10:00"),
            "last_limit_time": r.get("last_limit_time", "10:00"),
            "open_times": r.get("open_times", 0),
            "seal_amount": r.get("seal_amount", 1e8),
            "turnover": r.get("turnover", 1.0),
            "amount": r.get("amount", 1e8),
            "industry": r.get("industry", "A"),
        })
    return pd.DataFrame(data, columns=ZT_COLS)


def _mkzb(rows) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=[
        "trade_date", "code", "name", "break_times", "first_seal_time", "up_pct", "industry",
    ])


def _mkflow(rows) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=[
        "trade_date", "code", "name", "main_net_inflow", "super_net_inflow", "big_net_inflow",
    ])


class TestPromotion:
    def test_cross_boards(self):
        prev = _mkzt([
            {"code": "600001", "lb_num": 1},
            {"code": "600002", "lb_num": 1},
            {"code": "600003", "lb_num": 2},
        ], date="20260805")
        today = _mkzt([
            {"code": "600001", "lb_num": 2},   # 1→2 晋级
            {"code": "600002", "lb_num": 1},   # 断板
            {"code": "600003", "lb_num": 3},   # 2→3 晋级
        ])
        prom = compute_promotion(prev, today)
        assert prom["1进2"] == 0.5
        assert prom["2进3"] == 1.0

    def test_no_prev_data_returns_empty(self):
        prev = pd.DataFrame(columns=ZT_COLS)
        today = _mkzt([{"code": "600001", "lb_num": 1}])
        assert compute_promotion(prev, today) == {}


class TestLadder:
    def test_ladder_stats(self):
        zt = _mkzt([
            {"code": "600001", "name": "最高股", "lb_num": 3, "first_limit_time": "09:30"},
            {"code": "600002", "name": "二板A", "lb_num": 2},
            {"code": "600003", "name": "二板B", "lb_num": 2, "open_times": 5},
        ])
        zb = _mkzb([{"code": "600009", "name": "炸板", "break_times": 1, "first_seal_time": "10:00", "up_pct": 3.0, "industry": "A"}])
        height = [{"date": "20260806", "max_lb": 3}, {"date": "20260805", "max_lb": 2}]
        res = compute_ladder(zt, zb, _mkzt([], date="20260805"), height)
        assert res["zt_count"] == 3
        assert res["lianban_count"] == 3
        assert res["max_lb"] == 3
        assert res["max_lb_stock"] == "最高股"
        assert res["break_count"] == 1
        assert res["break_rate"] == 0.25
        assert res["height_series"] == height
        # 弱封标记：open_times>=3 的二板B 应被标记
        weak2 = [w for layer in res["ladder"] if layer["height"] == 2 for w in layer["weak"]]
        assert any("600003" in w for w in weak2)

    def test_height_position_high(self):
        """当前空间板高度处于历史高位。"""
        series = [{"date": "20260806", "max_lb": 5}, {"date": "20260805", "max_lb": 3},
                  {"date": "20260804", "max_lb": 2}]
        pos = _compute_height_position(series)
        assert pos["current"] == 5
        assert pos["max"] == 5
        assert pos["min"] == 2
        assert pos["label"] == "高位"
        assert pos["percentile"] >= 0.7
        assert pos["trend"] == "上升"  # 5 > 3
        assert "mean" not in pos

    def test_height_position_low(self):
        """当前空间板高度处于历史低位。"""
        series = [{"date": "20260806", "max_lb": 2}, {"date": "20260805", "max_lb": 5},
                  {"date": "20260804", "max_lb": 4}]
        pos = _compute_height_position(series)
        assert pos["current"] == 2
        assert pos["label"] == "低位"
        assert pos["percentile"] <= 0.3
        assert pos["trend"] == "下降"  # 2 < 5
        assert "mean" not in pos

    def test_height_position_mid(self):
        """当前空间板高度处于历史中位。"""
        series = [{"date": "20260806", "max_lb": 4}, {"date": "20260805", "max_lb": 5},
                  {"date": "20260804", "max_lb": 3}]
        pos = _compute_height_position(series)
        assert pos["current"] == 4
        assert pos["label"] == "中位"
        assert pos["trend"] == "下降"  # 4 < 5
        assert "mean" not in pos

    def test_height_position_single_day(self):
        """仅1日数据时标签为仅1日。"""
        series = [{"date": "20260806", "max_lb": 3}]
        pos = _compute_height_position(series)
        assert pos["label"] == "仅1日"
        assert pos["current"] == 3
        assert pos["trend"] == "持平"  # 仅1日，与自身比较
        assert "mean" not in pos

    def test_height_position_all_same(self):
        """所有值相同时标签为中位。"""
        series = [{"date": "20260806", "max_lb": 4}, {"date": "20260805", "max_lb": 4},
                  {"date": "20260804", "max_lb": 4}]
        pos = _compute_height_position(series)
        assert pos["label"] == "中位"
        assert pos["min"] == pos["max"] == 4
        assert pos["trend"] == "持平"  # 4 == 4
        assert "mean" not in pos

    def test_height_position_in_ladder_output(self):
        """compute_ladder 返回的 height_position 字段包含正确标签。"""
        zt = _mkzt([
            {"code": "600001", "name": "最高股", "lb_num": 5, "first_limit_time": "09:30"},
        ])
        zb = _mkzb([])
        height = [{"date": "20260806", "max_lb": 5}, {"date": "20260805", "max_lb": 3},
                  {"date": "20260804", "max_lb": 2}]
        res = compute_ladder(zt, zb, _mkzt([], date="20260805"), height)
        assert "height_position" in res
        assert res["height_position"]["label"] == "高位"
        assert res["height_position"]["current"] == 5
        assert res["height_position"]["trend"] == "上升"
        assert "mean" not in res["height_position"]

    def test_height_position_with_long_series(self):
        """传入长序列时，height_position 基于长序列计算。"""
        zt = _mkzt([
            {"code": "600001", "name": "最高股", "lb_num": 4, "first_limit_time": "09:30"},
        ])
        zb = _mkzb([])
        height = [{"date": "20260806", "max_lb": 4}]  # 短序列仅1日
        # 长序列 20 天，当前 4 板在序列中偏低
        long_series = [{"date": f"202608{d:02d}", "max_lb": (6 if d % 3 == 0 else 5) if d < 15 else 4}
                       for d in range(20, 0, -1)]
        long_series[0] = {"date": "20260806", "max_lb": 4}  # 今日=4板
        res = compute_ladder(zt, zb, _mkzt([], date="20260805"), height,
                             long_height_series=long_series)
        assert "height_position" in res
        # 基于长序列（非仅1日），应能判断位置
        assert res["height_position"]["label"] != "仅1日"
        assert res["height_position"]["current"] == 4
        assert "mean" not in res["height_position"]


class TestTheme:
    def test_grouping_leader_and_stage(self):
        zt = _mkzt([
            {"code": "600001", "name": "龙头A", "lb_num": 3, "first_limit_time": "09:30", "industry": "机器人"},
            {"code": "600002", "name": "成员B", "lb_num": 2, "first_limit_time": "10:00", "industry": "机器人"},
            {"code": "600003", "name": "成员C", "lb_num": 1, "first_limit_time": "11:00", "industry": "机器人"},
        ])
        prev_pools = [
            ("20260805", _mkzt([{"code": "600009", "lb_num": 2, "industry": "机器人"}], date="20260805")),
        ]
        themes = build_themes(zt, prev_pools, {})
        assert len(themes) == 1
        t = themes[0]
        assert t["theme_name"] == "机器人"
        assert t["member_count"] == 3
        assert t["max_lb"] == 3
        assert t["leader"]["code"] == "600001"
        assert t["leader"]["name"] == "龙头A"
        assert t["stage"] in {"启动", "发酵", "高潮", "退潮"}
        assert t["stage_reason"], "阶段必须有依据句"
        assert t["prev_member_counts"] == [1, 3]

    def test_empty_zt(self):
        assert build_themes(pd.DataFrame(columns=ZT_COLS), [], {}) == []


class TestBreakFlow:
    def test_signal_classification_and_sort(self):
        zb = _mkzb([
            {"code": "600001", "name": "a", "break_times": 1, "first_seal_time": "09:45", "up_pct": 6.1, "industry": "A"},
            {"code": "600002", "name": "b", "break_times": 4, "first_seal_time": "09:45", "up_pct": 3.0, "industry": "A"},
            {"code": "600003", "name": "c", "break_times": 1, "first_seal_time": "09:45", "up_pct": 1.0, "industry": "A"},
        ])
        flow = _mkflow([
            {"code": "600001", "name": "a", "main_net_inflow": 1.2e8, "super_net_inflow": 1e8, "big_net_inflow": 0.2e8},
            {"code": "600002", "name": "b", "main_net_inflow": 0.5e8, "super_net_inflow": 0.4e8, "big_net_inflow": 0.1e8},
            {"code": "600003", "name": "c", "main_net_inflow": -5e7, "super_net_inflow": -4e7, "big_net_inflow": -1e7},
        ])
        res = analyze_break(zb, flow, break_rate=0.2)
        assert res["break_count"] == 3
        assert res["break_rate"] == 0.2
        table = {r["code"]: r for r in res["table"]}
        assert "反包关注" in table["600001"]["signal"]
        assert "谨慎" in table["600002"]["signal"]
        assert "规避" in table["600003"]["signal"]
        assert table["600001"]["main_net_inflow"] > table["600002"]["main_net_inflow"]

    def test_missing_moneyflow_marked(self):
        zb = _mkzb([
            {"code": "600001", "name": "a", "break_times": 1, "first_seal_time": "09:45", "up_pct": 5.0, "industry": "A"},
        ])
        empty = _mkflow([])
        res = analyze_break(zb, empty)
        assert res["table"][0]["signal"] == "缺资金流"
