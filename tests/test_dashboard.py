"""数据看板测试（离线，合成数据）：趋势行核算 / KPI+图表渲染 / 缺数据降级 / 自包含 HTML。

覆盖 dashboard.py 核心行为：
- build_trend：旧→新、末行为今日、逐日计数口径与 emotion 按 date 匹配（方向无关）
- 缺数据日：0 值行 + missing 标记，不抛错
- render_html：单文件自包含（零外链字面量）、图表化布局（无 LLM / 无明细面板）
- generate_dashboard：monkeypatch 采集/指标 → 落盘标题
"""

from __future__ import annotations

import pandas as pd
import pytest

import daily_review.pipeline as pipeline
from daily_review.dashboard import (
    _assemble_payload,
    build_trend,
    generate_dashboard,
    render_error_html,
    render_html,
)


def _zt(date, pairs):
    """涨停池 DataFrame（列对齐最小集：code/lb_num）。"""
    df = pd.DataFrame(pairs, columns=["code", "lb_num"])
    df["trade_date"] = date
    return df


def _zb(date, codes):
    df = pd.DataFrame({"code": [str(c) for c in codes]})
    df["trade_date"] = date
    return df


def _dt(date, codes):
    return _zb(date, codes)


def _hist_day(date, zt_pairs, zb_codes, dt_codes, *, zb_ok=True, dt_ok=True):
    return {
        "date": date,
        "zt": _zt(date, zt_pairs),
        "zb": _zb(date, zb_codes),
        "dt": _dt(date, dt_codes),
        "zb_ok": zb_ok,
        "dt_ok": dt_ok,
    }


def _collected():
    """标准 3 日窗口：07-30 / 07-31 历史（旧→新）+ 今日 08-06。"""
    hist = [
        _hist_day(
            "20260730",
            [("000001", 4)] + [(f"{i:06d}", 1) for i in range(2, 31)],  # 30 涨停 / 高度 4
            [f"6{i:05d}" for i in range(5)],                             # 5 炸板
            [f"9{i:05d}" for i in range(2)],                             # 2 跌停
        ),
        _hist_day(
            "20260731",
            [("000001", 3)] + [(f"{i:06d}", 1) for i in range(2, 41)],  # 40 涨停 / 高度 3
            [f"6{i:05d}" for i in range(10)],                            # 10 炸板
            [f"9{i:05d}" for i in range(3)],                             # 3 跌停
        ),
    ]
    return {
        "trade_date": "20260806",
        "hist_days": hist,
        "zt": _zt("20260806", [("000001", 5)] + [(f"{i:06d}", 1) for i in range(2, 61)]),  # 60 涨停
        "zb": _zb("20260806", [f"6{i:05d}" for i in range(15)]),          # 15 炸板 → 炸板率 0.20
        "dt": _dt("20260806", [f"9{i:05d}" for i in range(5)]),           # 5 跌停
        "zb_ok": True,
        "dt_ok": True,
    }


def _empty_indicators():
    """全空指标（供全空渲染/生成用例），字段对齐 compute() 产出。"""
    return {
        "ladder": {"zt_count": 0, "lianban_count": 0, "max_lb": 0, "max_lb_stock": "",
                   "break_rate": 0.0, "ladder": [], "promotion": {}},
        "themes": [],
        "break": {"break_count": 0, "break_rate": 0.0, "table": []},
        "lhb": {"overview": {}, "net_rank": [], "hotmoney": []},
        "emotion": {"available": False, "score": None, "stage": None, "stage_reason": None,
                    "series": [], "components": {}},
    }


def _small_payload():
    """最小前端载荷（render_html 直接消费）。"""
    return {
        "trade_date": "20260806",
        "weekday": "周四",
        "n_days": 3,
        "trend": [
            {"date": "20260730", "zt_count": 30, "lianban_count": 1, "max_lb": 4,
             "break_count": 5, "break_rate": 0.1429, "dt_count": 2, "emotion": 60.0, "missing": []},
            {"date": "20260731", "zt_count": 40, "lianban_count": 2, "max_lb": 3,
             "break_count": 10, "break_rate": 0.2, "dt_count": 3, "emotion": 70.0, "missing": []},
            {"date": "20260806", "zt_count": 60, "lianban_count": 1, "max_lb": 5,
             "break_count": 15, "break_rate": 0.2, "dt_count": 5, "emotion": 80.0, "missing": []},
        ],
        "kpi": {"emotion_score": 80.0, "emotion_stage": "高潮期", "emotion_reason": "…",
                "zt_count": 60, "lianban_count": 1, "max_lb": 5, "max_lb_stock": "000001",
                "break_rate": 0.2, "dt_count": 5},
        "emotion": {"available": True, "score": 80.0, "stage": "高潮期",
                    "stage_reason": "依据：…", "components": {}, "raw": {}},
    }


class TestBuildTrend:
    def test_old_to_new_and_counts(self):
        rows = build_trend(_collected(), _empty_indicators(), n_days=3)
        assert [r["date"] for r in rows] == ["20260730", "20260731", "20260806"]
        today = rows[-1]
        assert today["zt_count"] == 60 and today["lianban_count"] == 1
        assert today["max_lb"] == 5 and today["break_rate"] == pytest.approx(0.2)
        assert today["dt_count"] == 5 and today["missing"] == []
        assert rows[0]["zt_count"] == 30 and rows[0]["max_lb"] == 4
        assert rows[0]["break_rate"] == pytest.approx(0.1429)
        assert rows[1]["zt_count"] == 40

    def test_n_days_trims_oldest(self):
        rows = build_trend(_collected(), _empty_indicators(), n_days=2)
        assert [r["date"] for r in rows] == ["20260731", "20260806"]
        assert rows[-1]["date"] == "20260806"

    def test_emotion_matched_by_date_order_independent(self):
        indicators = {"emotion": {"series": [
            {"date": "20260806", "score": 80.0},
            {"date": "20260731", "score": 70.0},
            {"date": "20260730", "score": 60.0},
        ]}}
        rows = build_trend(_collected(), indicators, n_days=3)
        assert [r["emotion"] for r in rows] == [60.0, 70.0, 80.0]

    def test_missing_day_kept_with_flags(self):
        collected = {
            "trade_date": "20260806",
            "hist_days": [_hist_day("20260730", [], [], [], zb_ok=False, dt_ok=False)],
            "zt": _zt("20260806", []),
            "zb": _zb("20260806", []), "dt": _dt("20260806", []),
            "zb_ok": True, "dt_ok": True,
        }
        indicators = {"emotion": {"series": [{"date": "20260730", "score": None}]}}
        rows = build_trend(collected, indicators, n_days=2)
        assert rows[0]["missing"] == ["涨停缺失", "炸板缺失", "跌停缺失"]
        assert rows[0]["zt_count"] == 0 and rows[0]["break_rate"] == 0.0
        assert rows[0]["emotion"] is None
        assert rows[1]["missing"] == ["涨停缺失"]
        assert rows[1]["dt_count"] == 0


class TestAssemblePayload:
    def test_emotion_contains_raw_for_components(self):
        indicators = {**_empty_indicators(), "emotion": {
            "available": True, "score": 80.0, "stage": "高潮期", "stage_reason": "依据",
            "series": [], "components": {"zt": 30.0, "height": 80.0},
            "raw": {"zt_count": 60, "max_lb": 5},
        }}
        collected = _collected()
        payload = _assemble_payload(indicators, build_trend(_collected(), indicators, 3), collected)
        assert payload["emotion"]["raw"] == {"zt_count": 60, "max_lb": 5}
        assert payload["emotion"]["components"] == {"zt": 30.0, "height": 80.0}
        # 图表化看板：不再下发明细章节
        assert "ladder" not in payload
        assert "themes" not in payload
        assert "break" not in payload
        assert "lhb" not in payload


class TestRenderHtml:
    def test_self_contained_no_external(self):
        html_text = render_html(_small_payload())
        for token in ("<!DOCTYPE html>", "const DATA =", "drawLineChart", "drawBarChart", "</html>"):
            assert token in html_text
        for bad in ("http://", "https://", "<script src", "<link", "src="):
            assert bad not in html_text, f"自包含断言失败：出现 {bad!r}"

    def test_chart_panels_present_no_llm_no_detail(self):
        html_text = render_html(_small_payload())
        assert 'id="trend-summary"' in html_text
        assert 'id="emotion-comp"' in html_text
        assert "renderTrendSummary" in html_text
        assert "renderEmotionComp" in html_text
        assert "多日趋势解读" not in html_text
        assert "（未生成解读）" not in html_text
        assert 'id="ladder"' not in html_text
        assert 'id="themes"' not in html_text
        assert 'id="break"' not in html_text
        assert 'id="lhb"' not in html_text
        assert "renderLadder" not in html_text
        assert "class=\"tables\"" in html_text

    def test_render_error_html_self_contained_escaped(self):
        html_text = render_error_html("20260806", 'ConnectionError: <boom> & "x"')
        assert "数据看板加载失败" in html_text
        assert "20260806" in html_text
        assert "ConnectionError" in html_text
        assert "<boom>" not in html_text
        for bad in ("http://", "https://", "<script src", "<link"):
            assert bad not in html_text, f"错误页自包含断言失败：{bad!r}"

    def test_json_script_injection_escaped(self):
        payload = _small_payload()
        payload["kpi"]["max_lb_stock"] = '</script><script>alert(1)</script>'
        html_text = render_html(payload)
        assert "</script><script>" not in html_text
        assert "\\u003c" not in html_text or "<\\/" in html_text

    def test_all_empty_no_crash(self):
        html_text = render_html({**_small_payload(), "trend": []})
        assert "const DATA =" in html_text
        assert "数据不足" in html_text

    def test_table_overflow_guard(self):
        html_text = render_html(_small_payload())
        assert "#trend-summary, #emotion-comp" in html_text
        assert "overflow-x: auto" in html_text

    def test_dashboard_template_iframe_resize(self):
        """web 端 iframe 高度自适应：窗口缩放后重新测高。"""
        import pathlib
        tpl = (pathlib.Path(__file__).resolve().parents[1]
               / "src" / "daily_review" / "web" / "templates" / "dashboard.html")
        text = tpl.read_text(encoding="utf-8")
        assert "window.addEventListener('resize', function(){ fitFrame(); });" in text
        assert "btnDash" not in text
        assert "dLlm" not in text
        assert "loadDash()" in text


class TestGenerate:
    def test_generate_writes_file(self, monkeypatch, tmp_path):
        collected = _collected()
        indicators = {**_empty_indicators(), "emotion": {"available": True, "score": 80.0,
                                                         "stage": "高潮期", "stage_reason": "依据：…",
                                                         "series": [
                                                             {"date": "20260806", "score": 80.0},
                                                             {"date": "20260731", "score": 70.0},
                                                             {"date": "20260730", "score": 60.0},
                                                         ], "components": {}, "raw": {}}}
        monkeypatch.setattr(pipeline, "collect_dashboard", lambda trade_date, n_days=10: collected)
        monkeypatch.setattr(pipeline, "compute_dashboard", lambda c: indicators)
        out = tmp_path / "output" / "20260806_看板.html"
        html_text = generate_dashboard("20260806", n_days=3, out_path=out)
        assert out.exists()
        assert "数据看板" in html_text
        assert "2026-08-06" in html_text
        assert "const DATA =" in html_text
        assert "（未生成解读）" not in html_text
        assert "多日趋势解读" not in html_text
