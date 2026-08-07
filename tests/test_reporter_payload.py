"""reporter 载荷对齐测试：字段名与 module prompt 输入契约一致（离线，不发 LLM 请求）。"""

from __future__ import annotations

from daily_review.llm.reporter import (
    _break_payload,
    _headline,
    _ladder_forced_headline,
    _ladder_payload,
    _strip_section_heading,
    _theme_payload,
    _to_jsonable,
)


def _indicators() -> dict:
    return {
        "ladder": {
            "trade_date": "20260806", "zt_count": 79, "lianban_count": 22,
            "max_lb": 10, "max_lb_stock": "爱丽家居", "break_count": 20,
            "break_rate": 0.202, "first_board_count": 57,
            "promotion": {"1进2": 0.1268, "3进4": 1.0},
            "ladder": [{"height": 10, "count": 1, "stocks": [], "weak": []}],
            "height_series": [{"date": "20260806", "max_lb": 10}, {"date": "20260805", "max_lb": 8}],
        },
        "themes": [{
            "theme_name": "机器人", "member_count": 3, "max_lb": 3, "stage": "发酵",
            "stage_reason": "连续 2 日 1→3 家，判定发酵",
            "leader": {"code": "600001", "name": "龙头", "lb_num": 3, "first_limit_time": "09:30"},
            "assists": [], "members": [], "heights": {}, "concepts": [], "is_main": True,
        }],
        "break": {
            "break_count": 20, "break_rate": 0.202,
            "table": [{
                "code": "600378", "name": "昊华科技", "industry": "化学制品",
                "break_times": 1, "first_seal_time": "09:45", "up_pct": 9.51,
                "main_net_inflow": 3.66e8, "super_net_inflow": 1e8,
                "big_net_inflow": 2e8, "signal": "🟢 反包关注",
            }],
            "watch": [],
        },
        "zt_pool": [{
            "code": "603221", "name": "爱丽家居", "lb_num": 10, "first_limit_time": "09:25",
            "open_times": 1, "seal_amount": 6.5e7, "industry": "家居用品", "concepts": [],
        }],
    }


class TestLadderContract:
    def test_stats_fields(self):
        stats = _ladder_payload(_indicators())["连板统计"]
        for k in ("zt_count", "lianban_count", "max_lb", "max_lb_stock",
                  "break_count", "break_rate", "promotion"):
            assert k in stats, f"连板统计缺字段 {k}"

    def test_pool_fields(self):
        pool0 = _ladder_payload(_indicators())["当日涨停池"][0]
        for k in ("code", "name", "lb_num", "first_limit_time",
                  "open_times", "seal_amount", "concepts"):
            assert k in pool0, f"涨停池缺字段 {k}"

    def test_precomputed_ladder_grouping(self):
        assert _ladder_payload(_indicators())["梯队分组(已核算)"]


class TestThemeContract:
    def test_theme_fields(self):
        t = _theme_payload(_indicators())["当日各题材"][0]
        for k in ("theme_name", "member_count", "members"):
            assert k in t, f"题材缺字段 {k}"


class TestBreakContract:
    def test_break_table_fields(self):
        r = _break_payload(_indicators())["炸板股资金流向"][0]
        for k in ("code", "name", "break_times", "first_seal_time", "up_pct",
                  "main_net_inflow", "super_net_inflow", "big_net_inflow", "signal"):
            assert k in r, f"炸板表缺字段 {k}"


class TestHelpers:
    def test_forced_headline_matches_stats(self):
        line = _ladder_forced_headline(_indicators())
        assert "79" in line and "22" in line and "10" in line and "爱丽家居" in line
        assert "20.2%" in line

    def test_headline_fields(self):
        h = _headline(_indicators())
        assert h["zt_count"] == 79 and h["max_lb"] == 10
        assert h["max_lb_stock"] == "爱丽家居"

    def test_to_jsonable_handles_nonfinite(self):
        assert _to_jsonable(float("nan")) is None
        assert _to_jsonable(float("inf")) is None
        assert _to_jsonable({"a": float("nan"), "b": [1, 2], "c": None})["a"] is None
        assert _to_jsonable({"d": {"e": 1.5}}) == {"d": {"e": 1.5}}

    def test_strip_section_heading(self):
        assert _strip_section_heading("## 五、次日预案\n\n正文", "五、次日预案") == "正文"
        assert _strip_section_heading("正文无标题", "一、总览") == "正文无标题"
        assert _strip_section_heading("## 一、总览\n\nabc\n## 五、次日预案\n\nxyz", "一、总览") == "abc\n## 五、次日预案\n\nxyz"
