"""web/monitor.py 异常检测引擎测试：4 类规则的纯函数逻辑。"""

from __future__ import annotations

from daily_review.web.monitor import (
    CARD_COLOR_GREEN,
    CARD_COLOR_RED,
    detect_anomalies,
)


def _make_baseline(zt_count: int = 30, zb_count: int = 5) -> dict:
    return {
        "zt_count": zt_count,
        "zb_count": zb_count,
        "zt": {"codes": [f"Z{i:04d}" for i in range(zt_count)], "seal_map": {}, "open_times_map": {}},
        "zb": {"codes": [f"B{i:04d}" for i in range(zb_count)], "seal_map": {}, "open_times_map": {}},
    }


def _make_delta(
    new_zt: list[str] | None = None,
    broken: list[str] | None = None,
    re_sealed: list[str] | None = None,
    zt_count: int = 30,
    zb_count: int = 5,
) -> dict:
    return {
        "new_zt": new_zt or [],
        "broken": broken or [],
        "re_sealed": re_sealed or [],
        "zt_count": zt_count,
        "zb_count": zb_count,
        "timestamp": "2026-08-20T10:30:00",
    }


# ---------------------------------------------------------------- 炸板潮


class TestBrokenSurge:
    def test_below_threshold_no_anomaly(self):
        """4 只炸板不触发（阈值 5）。"""
        bl = _make_baseline(zt_count=30)
        delta = _make_delta(broken=[f"Z{i:04d}" for i in range(4)])
        result = detect_anomalies(bl, delta)
        types = [a.type for a in result]
        assert "炸板潮" not in types

    def test_warning_at_threshold(self):
        """5 只炸板触发 warning。"""
        bl = _make_baseline(zt_count=30)
        delta = _make_delta(broken=[f"Z{i:04d}" for i in range(5)])
        result = detect_anomalies(bl, delta)
        anomalies = [a for a in result if a.type == "炸板潮"]
        assert len(anomalies) == 1
        assert anomalies[0].severity == "warning"
        assert anomalies[0].card_color == CARD_COLOR_RED

    def test_alert_at_ten(self):
        """10 只炸板触发 alert。"""
        bl = _make_baseline(zt_count=30)
        delta = _make_delta(broken=[f"Z{i:04d}" for i in range(10)])
        result = detect_anomalies(bl, delta)
        anomalies = [a for a in result if a.type == "炸板潮"]
        assert len(anomalies) == 1
        assert anomalies[0].severity == "alert"
        assert anomalies[0].card_color == CARD_COLOR_RED

    def test_includes_stock_list(self):
        """炸板潮异常包含股票列表。"""
        bl = _make_baseline(zt_count=30)
        stocks = ["Z0001", "Z0002", "Z0003", "Z0004", "Z0005"]
        delta = _make_delta(broken=stocks)
        result = detect_anomalies(bl, delta)
        anomalies = [a for a in result if a.type == "炸板潮"]
        assert anomalies[0].stocks == stocks
        assert "5 只" in anomalies[0].message


# ---------------------------------------------------------------- 题材爆发


class TestThemeOutbreak:
    def test_below_threshold_no_anomaly(self):
        """同行业 2 只新涨停不触发（阈值 3）。"""
        bl = _make_baseline()
        delta = _make_delta(new_zt=["A001", "A002"])
        industry_map = {"A001": "半导体", "A002": "半导体", "A003": "半导体"}
        result = detect_anomalies(bl, delta, industry_map=industry_map)
        types = [a.type for a in result]
        assert "题材爆发" not in types

    def test_outbreak_detected(self):
        """同行业 3 只新涨停触发题材爆发。"""
        bl = _make_baseline()
        delta = _make_delta(new_zt=["A001", "A002", "A003"])
        industry_map = {"A001": "半导体", "A002": "半导体", "A003": "半导体"}
        result = detect_anomalies(bl, delta, industry_map=industry_map)
        anomalies = [a for a in result if a.type == "题材爆发"]
        assert len(anomalies) == 1
        assert "半导体" in anomalies[0].message
        assert anomalies[0].card_color == CARD_COLOR_GREEN

    def test_multiple_industries(self):
        """多个行业各自计数，不互相干扰。"""
        bl = _make_baseline()
        delta = _make_delta(new_zt=["A001", "A002", "A003", "B001", "B002"])
        industry_map = {"A001": "半导体", "A002": "半导体", "A003": "半导体",
                        "B001": "医药", "B002": "医药"}
        result = detect_anomalies(bl, delta, industry_map=industry_map)
        anomalies = [a for a in result if a.type == "题材爆发"]
        assert len(anomalies) == 1  # 只有半导体达到 3 只
        assert "半导体" in anomalies[0].message

    def test_unknown_industry_defaults_to_other(self):
        """无行业映射的股票归为"其他"，仅 2 只时不触发题材爆发（阈值 3）。"""
        bl = _make_baseline()
        delta = _make_delta(new_zt=["A001", "A002"])  # 仅 2 只
        industry_map = {}  # 无映射 → 都归"其他"
        result = detect_anomalies(bl, delta, industry_map=industry_map)
        types = [a.type for a in result]
        assert "题材爆发" not in types

    def test_no_industry_map_skips_theme_check(self):
        """不传 industry_map 时跳过题材爆发检测。"""
        bl = _make_baseline()
        delta = _make_delta(new_zt=["A001", "A002", "A003"])
        result = detect_anomalies(bl, delta, industry_map=None)
        types = [a.type for a in result]
        assert "题材爆发" not in types


# ---------------------------------------------------------------- 龙头异动


class TestLeaderMove:
    def test_leader_broken(self):
        """空间板炸板触发 alert。"""
        bl = _make_baseline()
        delta = _make_delta(broken=["KING"])
        space_board = {"code": "KING", "lb_num": 6}
        result = detect_anomalies(bl, delta, space_board=space_board)
        anomalies = [a for a in result if a.type == "龙头异动"]
        assert len(anomalies) == 1
        assert anomalies[0].severity == "alert"
        assert "6 连板" in anomalies[0].message
        assert anomalies[0].card_color == CARD_COLOR_RED

    def test_leader_re_sealed(self):
        """空间板回封触发 info。"""
        bl = _make_baseline()
        delta = _make_delta(re_sealed=["KING"])
        space_board = {"code": "KING", "lb_num": 5}
        result = detect_anomalies(bl, delta, space_board=space_board)
        anomalies = [a for a in result if a.type == "龙头异动"]
        assert len(anomalies) == 1
        assert anomalies[0].severity == "info"
        assert "5 连板" in anomalies[0].message
        assert anomalies[0].card_color == CARD_COLOR_GREEN

    def test_no_leader_move(self):
        """空间板无变化时不触发。"""
        bl = _make_baseline()
        delta = _make_delta(new_zt=["OTHER"])
        space_board = {"code": "KING", "lb_num": 6}
        result = detect_anomalies(bl, delta, space_board=space_board)
        types = [a.type for a in result]
        assert "龙头异动" not in types

    def test_no_space_board_skips_check(self):
        """无空间板信息时跳过龙头异动检测。"""
        bl = _make_baseline()
        delta = _make_delta(broken=["KING"])
        result = detect_anomalies(bl, delta, space_board=None)
        types = [a.type for a in result]
        assert "龙头异动" not in types


# ---------------------------------------------------------------- 情绪骤变


class TestEmotionCrash:
    def test_crash_detected(self):
        """涨停数从 30 骤降至 15（降 50% > 30%），触发 warning。"""
        bl = _make_baseline(zt_count=30)
        delta = _make_delta(zt_count=15)
        result = detect_anomalies(bl, delta)
        anomalies = [a for a in result if a.type == "情绪骤变"]
        assert len(anomalies) == 1
        assert anomalies[0].severity == "warning"
        assert "50%" in anomalies[0].message or "50" in anomalies[0].message
        assert anomalies[0].card_color == CARD_COLOR_RED

    def test_no_crash_if_stable(self):
        """涨停数从 30 到 28（降 6.7% < 30%），不触发。"""
        bl = _make_baseline(zt_count=30)
        delta = _make_delta(zt_count=28)
        result = detect_anomalies(bl, delta)
        types = [a.type for a in result]
        assert "情绪骤变" not in types

    def test_crash_uses_prev_delta(self):
        """有上轮数据时，基于上轮对比而非 baseline。"""
        bl = _make_baseline(zt_count=30)
        prev = _make_delta(zt_count=25)  # 上轮 25
        delta = _make_delta(zt_count=15)  # 当前 15（降 40%）
        result = detect_anomalies(bl, delta, prev_delta=prev)
        anomalies = [a for a in result if a.type == "情绪骤变"]
        # 降 40% > 30%，应触发
        assert len(anomalies) == 1

    def test_no_crash_against_prev_when_stable(self):
        """有上轮数据但变化不大，不触发。"""
        bl = _make_baseline(zt_count=30)
        prev = _make_delta(zt_count=25)
        delta = _make_delta(zt_count=24)  # 降 4%
        result = detect_anomalies(bl, delta, prev_delta=prev)
        types = [a.type for a in result]
        assert "情绪骤变" not in types

    def test_no_crash_when_baseline_zero(self):
        """基准涨停数为 0 时不触发（避免除零）。"""
        bl = _make_baseline(zt_count=0)
        delta = _make_delta(zt_count=0)
        result = detect_anomalies(bl, delta)
        types = [a.type for a in result]
        assert "情绪骤变" not in types


# ---------------------------------------------------------------- 综合


class TestCombined:
    def test_multiple_anomalies(self):
        """同时触发多种异常时全部返回。"""
        bl = _make_baseline(zt_count=30)
        delta = _make_delta(
            broken=[f"Z{i:04d}" for i in range(10)],
            new_zt=["A001", "A002", "A003"],
            zt_count=15,
        )
        space_board = {"code": "Z0001", "lb_num": 6}
        industry_map = {"A001": "半导体", "A002": "半导体", "A003": "半导体"}
        result = detect_anomalies(bl, delta, space_board=space_board, industry_map=industry_map)
        types = {a.type for a in result}
        # 炸板潮 10 只 + 情绪骤变 50% + 龙头异动（Z0001 炸板）+ 题材爆发
        assert "炸板潮" in types
        assert "题材爆发" in types
        assert "龙头异动" in types
        assert "情绪骤变" in types

    def test_no_anomaly_returns_empty(self):
        """无变化时返回空列表。"""
        bl = _make_baseline(zt_count=30)
        delta = _make_delta(zt_count=30)  # 完全无变化
        result = detect_anomalies(bl, delta)
        assert result == []

    def test_empty_data_does_not_crash(self):
        """空数据不崩溃。"""
        bl = {"zt_count": 0, "zb_count": 0, "zt": {"codes": []}, "zb": {"codes": []}}
        delta = {"new_zt": [], "broken": [], "re_sealed": [], "zt_count": 0, "zb_count": 0, "timestamp": ""}
        result = detect_anomalies(bl, delta)
        assert isinstance(result, list)