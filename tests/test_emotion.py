"""情绪温度指标测试（离线，合成数据）：分段插值 / 加权合成 / 阶段判定 / 降级链。

覆盖 EmotionStats 的核心行为（docs/数据结构.md §9）：
- _piecewise 分段线性插值 + 端点 clamp
- _overall_promote 晋级延续率（整体口径）
- _day_components + _weighted：五维子分、缺维重归一、0 值 vs 缺失的区分
- _judge_stage：方向优先的四阶段规则链 + 单日兜底
- compute_emotion 端到端：序列结构、缺日、盘中标记、空涨停池降级
"""

from __future__ import annotations

import pytest
import pandas as pd

from daily_review.analysis.emotion import (
    BREAK_ANCHORS,
    DT_ANCHORS,
    HEIGHT_ANCHORS,
    PROMOTE_ANCHORS,
    WEIGHTS,
    ZT_ANCHORS,
    _day_components,
    _judge_stage,
    _overall_promote,
    _piecewise,
    _weighted,
    compute_emotion,
)


def _zt(date, pairs):
    """涨停池 DataFrame（列对齐 emotion 用到的最小集：trade_date/code/lb_num）。"""
    df = pd.DataFrame(pairs, columns=["code", "lb_num"])
    df["trade_date"] = date
    return df


def _zb(date, codes):
    df = pd.DataFrame({"code": [str(c) for c in codes]})
    df["trade_date"] = date
    return df


def _dt(date, codes):
    return _zb(date, codes)


class TestPiecewise:
    def test_forward_interpolation(self):
        # 45 在 20:0 ~ 70:50 段内
        assert _piecewise(45, ZT_ANCHORS) == 25.0

    def test_reverse_interpolation(self):
        # 炸板率 0.22 在 0.30:50 ~ 0.15:100 段内（分数随值下降而上升）
        assert _piecewise(0.22, BREAK_ANCHORS) == 76.7

    def test_clamp_low(self):
        assert _piecewise(5, ZT_ANCHORS) == 0.0
        assert _piecewise(0.0, BREAK_ANCHORS) == 100.0  # 0 炸板 = 满分

    def test_clamp_high(self):
        assert _piecewise(300, ZT_ANCHORS) == 100.0
        assert _piecewise(10, HEIGHT_ANCHORS) == 100.0

    def test_none_passthrough(self):
        assert _piecewise(None, ZT_ANCHORS) is None


class TestOverallPromote:
    def test_overlap_ratio(self):
        prev = _zt("20260805", [("000001", 1), ("000002", 1), ("000003", 1)])
        today = _zt("20260806", [("000002", 2), ("000003", 1), ("000004", 1)])
        assert _overall_promote(prev, today) == pytest.approx(2 / 3, abs=1e-4)

    def test_empty_prev(self):
        assert _overall_promote(_zt("20260805", []), _zt("20260806", [("1", 1)])) is None


class TestDayComponentsAndWeight:
    def _normal_inputs(self):
        """标准日：60 涨停 / 高度 5 / 晋级延续率 0.35 / 炸板率 0.20 / 5 跌停 → 63.5 分。"""
        zt = _zt("20260806", [("000001", 5)] + [(f"{i:06d}", 1) for i in range(2, 61)])
        zb = _zb("20260806", [f"6{i:05d}" for i in range(15)])   # 15 家 → 炸板率 15/75=0.20
        dt = _dt("20260806", [f"9{i:05d}" for i in range(5)])    # 5 家
        # prev：40 家，其中与今日重叠 14 家 → 晋级延续率 0.35
        prev = _zt(
            "20260805",
            [("000001", 4)] + [(f"{i:06d}", 1) for i in range(2, 15)] + [(f"8{i:05d}", 1) for i in range(26)],
        )
        return zt, zb, dt, prev

    def test_normal_day_components(self):
        zt, zb, dt, prev = self._normal_inputs()
        components, raw, missing = _day_components(zt, zb, dt, prev, zb_ok=True, dt_ok=True)
        assert missing == []
        assert components == {"zt": 40.0, "height": 62.5, "promote": 70.0, "break": 83.3, "dt": 83.3}
        assert raw["zt_count"] == 60 and raw["max_lb"] == 5
        assert raw["promote"] == pytest.approx(0.35, abs=1e-4)
        assert raw["break_rate"] == pytest.approx(0.2, abs=1e-4)

    def test_normal_day_score(self):
        zt, zb, dt, prev = self._normal_inputs()
        components, _, _ = _day_components(zt, zb, dt, prev, zb_ok=True, dt_ok=True)
        score, weights_used = _weighted(components)
        assert score == 63.5
        assert weights_used == WEIGHTS  # 全维齐 → 权重不重归一

    def test_missing_promote_renormalize(self):
        zt, zb, dt, _ = self._normal_inputs()
        components, _, missing = _day_components(zt, zb, dt, None, zb_ok=True, dt_ok=True)
        assert "promote" in missing
        score, weights_used = _weighted(components)
        assert score is not None
        assert abs(sum(weights_used.values()) - 1.0) < 1e-6
        assert set(weights_used) == {"zt", "height", "break", "dt"}

    def test_dt_zero_vs_missing(self):
        zt, zb, _, _ = self._normal_inputs()
        # 真实 0 家：dt_ok=True 且空表 → dt 子分 100（反向维满分）
        c0, _, m0 = _day_components(zt, zb, _dt("20260806", []), None, zb_ok=True, dt_ok=True)
        assert "dt" not in m0 and c0["dt"] == 100.0
        # 数据缺失：dt_ok=False → 该维剔除
        c1, _, m1 = _day_components(zt, zb, _dt("20260806", ["900001"]), None, zb_ok=True, dt_ok=False)
        assert "dt" in m1 and "dt" not in c1
        # 炸板同规则
        c2, _, m2 = _day_components(zt, _zb("20260806", ["600001"]), _dt("20260806", []), None, zb_ok=False, dt_ok=True)
        assert "break" in m2 and "break" not in c2


class TestJudgeStage:
    def test_stage_retreat(self):
        stage, reason = _judge_stage([80.0, 75.0, 65.0])
        assert stage == "退潮期"
        assert "判定退潮期" in reason and "65" in reason

    def test_stage_boom(self):
        stage, _ = _judge_stage([50.0, 62.0, 74.0])
        assert stage == "高潮期"

    def test_stage_repair(self):
        stage, _ = _judge_stage([40.0, 42.0, 45.0])
        assert stage == "修复期"

    def test_stage_ice(self):
        stage, _ = _judge_stage([50.0, 44.0, 40.0])
        assert stage == "冰点期"

    def test_high_cross_to_boom(self):
        # 65→70 抬升跨入高潮带 → 高潮期（方向优先于绝对带）
        assert _judge_stage([60.0, 65.0, 70.0])[0] == "高潮期"

    def test_single_day_absolute_band(self):
        stage, reason = _judge_stage([80.0])
        assert stage == "高潮期" and "仅单日" in reason
        assert _judge_stage([50.0])[0] == "修复期"
        assert _judge_stage([30.0])[0] == "冰点期"


class TestComputeEmotion:
    def test_empty_zt_unavailable(self):
        r = compute_emotion(_zt("20260806", []), _zb("20260806", []), _dt("20260806", []), [])
        assert r["available"] is False
        assert r["score"] is None and r["stage"] is None
        assert "涨停池为空" in r["notes"][0]

    def test_single_day_high_band(self):
        # 80 涨停 / 高度 6 / 15 炸板 / 8 跌停，无历史 → 温度 ≥70 → 高潮期（单日兜底）
        zt = _zt("20260806", [("000001", 6)] + [(f"{i:06d}", 1) for i in range(2, 81)])
        zb = _zb("20260806", [f"6{i:05d}" for i in range(15)])
        dt = _dt("20260806", [f"9{i:05d}" for i in range(8)])
        r = compute_emotion(zt, zb, dt, [])
        assert r["available"] is True
        assert r["stage"] == "高潮期"
        assert r["days_used"] == 1
        assert r["score"] == pytest.approx(73.3, abs=0.1)

    def test_normal_day_repair_end_to_end(self):
        # 近 3 日 40→40→60 家（晋级延续率 0.35），温度 49→57→63.5 → 修复期
        hist = [
            {
                "date": "20260803",
                "zt": _zt("20260803", [("000001", 2)] + [(f"{i:06d}", 1) for i in range(2, 41)]),
                "zb": _zb("20260803", []), "dt": _dt("20260803", []),
            },
            {
                "date": "20260804",
                "zt": _zt(
                    "20260804",
                    [("000001", 3)] + [(f"{i:06d}", 1) for i in range(2, 15)] + [(f"8{i:05d}", 1) for i in range(26)],
                ),
                "zb": _zb("20260804", []), "dt": _dt("20260804", []),
            },
        ]
        zt = _zt("20260806", [("000001", 5)] + [(f"{i:06d}", 1) for i in range(2, 61)])
        zb = _zb("20260806", [f"6{i:05d}" for i in range(15)])
        dt = _dt("20260806", [f"9{i:05d}" for i in range(5)])
        r = compute_emotion(zt, zb, dt, hist)
        assert r["available"] is True
        assert r["score"] == 63.5
        assert r["stage"] == "修复期"
        assert r["days_used"] == 3
        assert "判定修复期" in r["stage_reason"]

    def test_multi_day_series_structure(self):
        hist = [
            {
                "date": "20260803",
                "zt": _zt("20260803", [("000001", 3)] + [(f"{i:06d}", 1) for i in range(2, 31)]),
                "zb": _zb("20260803", []), "dt": _dt("20260803", []),
            },
            {
                "date": "20260804",
                "zt": _zt("20260804", [("000001", 4)] + [(f"{i:06d}", 1) for i in range(2, 46)]),
                "zb": _zb("20260804", []), "dt": _dt("20260804", []),
            },
        ]
        zt = _zt("20260806", [("000001", 5)] + [(f"{i:06d}", 1) for i in range(2, 61)])
        r = compute_emotion(zt, _zb("20260806", []), _dt("20260806", []), hist)
        assert r["available"] is True
        assert r["days_used"] == 3
        assert [s["date"] for s in r["series"]] == ["20260806", "20260804", "20260803"]  # 最新在前
        assert r["series"][0]["score"] is not None
        assert r["stage"] in ("修复期", "高潮期")

    def test_series_missing_day(self):
        hist = [
            {
                "date": "20260801",
                "zt": _zt("20260801", [("000001", 4)] + [(f"{i:06d}", 1) for i in range(2, 51)]),
                "zb": _zb("20260801", []), "dt": _dt("20260801", []),
            },
            {
                "date": "20260802",
                "zt": _zt("20260802", []),  # 该日涨停池为空 → 序列缺日
                "zb": _zb("20260802", []), "dt": _dt("20260802", []),
            },
        ]
        zt = _zt("20260806", [("000001", 5)] + [(f"{i:06d}", 1) for i in range(2, 61)])
        r = compute_emotion(zt, _zb("20260806", []), _dt("20260806", []), hist)
        assert r["available"] is True
        assert r["days_used"] == 2  # 缺日不计入
        assert any("20260802" in n for n in r["notes"])

    def test_intraday_note(self):
        zt = _zt("20260806", [("000001", 5)] + [(f"{i:06d}", 1) for i in range(2, 61)])
        r = compute_emotion(zt, _zb("20260806", []), _dt("20260806", []), [], is_intraday=True)
        assert any("盘中" in n for n in r["notes"])

    def test_no_prev_promote_dropped(self):
        zt = _zt("20260806", [("000001", 5)] + [(f"{i:06d}", 1) for i in range(2, 61)])
        r = compute_emotion(zt, _zb("20260806", []), _dt("20260806", []), [])
        assert r["available"] is True
        assert "promote" not in r["components"]
        assert abs(sum(r["weights_used"].values()) - 1.0) < 1e-6
        assert any("promote 数据缺失" in n for n in r["notes"])
