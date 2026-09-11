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
from daily_review.pipeline import collect_dashboard_detail, compute_dashboard_detail


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
        # 未传 detail：仅基础键，不注入明细面板
        assert "ladder" not in payload
        assert "themes" not in payload
        assert "zt_list" not in payload

    def test_detail_adds_panels_and_kpi(self):
        indicators = {**_empty_indicators(), "emotion": {
            "available": True, "score": 80.0, "stage": "高潮期", "stage_reason": "依据",
            "series": [], "components": {}, "raw": {},
        }}
        collected = _collected()
        detail = {
            "ladder": {"ladder": [{"height": 5, "count": 1, "stocks": ["000001 深中华A"], "weak": []}],
                       "promotion": {"1进2": 0.19}, "first_board_count": 52,
                       "height_position": {"label": "高位", "percentile": 0.8, "trend": "上升"}},
            "themes": [{"theme_name": "小家电", "member_count": 5, "max_lb": 4, "stage": "发酵",
                        "stage_reason": "r", "leader": {"code": "002403", "name": "爱仕达", "lb_num": 4},
                        "assists": [], "members": [], "heights": {}, "concepts": [],
                        "prev_member_counts": [], "prev_max_lb_series": [], "is_main": True}],
            "zt_list": [{"code": "002403", "name": "爱仕达", "lb_num": 4, "first_time": "09:25",
                         "open_times": 0, "seal_amount": 0.84, "amount": 5.92, "turnover": 14.7,
                         "industry": "小家电"}],
            "mf_net": -1.2, "lhb_net": 4.2, "zt_amount": 320.5, "promote_rate": 0.19,
            "flags": {"prev_zt_ok": True, "moneyflow_ok": True, "lhb_ok": True},
        }
        payload = _assemble_payload(indicators, build_trend(_collected(), indicators, 3), collected, detail)
        assert payload["ladder"]["ladder"][0]["height"] == 5
        assert payload["themes"][0]["name"] == "小家电"
        assert payload["zt_list"][0]["code"] == "002403"
        assert payload["kpi"]["first_board_count"] == 52
        assert payload["kpi"]["promote_rate"] == 0.19
        assert payload["kpi"]["mf_net"] == -1.2
        assert payload["flags"]["lhb_ok"] is True


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
        # v0.38：Tab 三面板（总览/市场结构/涨停明细）已存在
        assert 'data-tab="overview"' in html_text
        assert 'data-tab="structure"' in html_text
        assert 'data-tab="zt"' in html_text
        assert 'id="ladder"' in html_text
        assert 'id="themes"' in html_text
        assert 'id="zt-list"' in html_text
        assert "renderLadder" in html_text
        assert "renderThemes" in html_text
        assert "renderZtList" in html_text
        assert "renderPager" in html_text
        # 本版不做炸板/龙虎榜独立面板
        assert 'id="break"' not in html_text
        assert 'id="lhb"' not in html_text
        # 无 AI 文案
        assert "多日趋势解读" not in html_text
        assert "（未生成解读）" not in html_text
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
        """web 端 iframe 高度自适应：窗口缩放后重新测高；日期下拉（v0.38.1）时间感知默认。"""
        import pathlib
        tpl = (pathlib.Path(__file__).resolve().parents[1]
               / "src" / "daily_review" / "web" / "templates" / "dashboard.html")
        text = tpl.read_text(encoding="utf-8")
        assert "window.addEventListener('resize', function(){ fitFrame(); });" in text
        assert "btnDash" not in text
        assert "dLlm" not in text
        assert "loadDash()" in text
        # v0.38.1：日期由文本框改下拉，首屏经 /api/dashboard/dates 填充并时间感知默认
        assert '<select id="dDate"' in text
        assert "fillDates" in text
        assert "ensureDateOption" in text
        assert "api('/api/dashboard/dates')" in text
        assert "/api/review/recent_date" not in text
        # v0.38.2：天数 N 也改快捷下拉（7/10/20/30），select 用 change 直接刷新
        assert '<select id="dDays"' in text
        assert "scheduleLoad" not in text
        assert "addEventListener('change', loadDash)" in text


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
        detail = {
            "ladder": {"ladder": [{"height": 5, "count": 1, "stocks": ["000001 深中华A"], "weak": []}],
                       "promotion": {"1进2": 0.19}, "first_board_count": 52,
                       "height_position": {"label": "高位", "percentile": 0.8, "trend": "上升"}},
            "themes": [], "zt_list": [], "mf_net": None, "lhb_net": None, "zt_amount": None,
            "promote_rate": 0.19,
            "flags": {"prev_zt_ok": True, "moneyflow_ok": False, "lhb_ok": False},
        }
        monkeypatch.setattr(pipeline, "collect_dashboard", lambda trade_date, n_days=10: collected)
        monkeypatch.setattr(pipeline, "compute_dashboard", lambda c: indicators)
        monkeypatch.setattr(pipeline, "collect_dashboard_detail",
                            lambda trade_date, n_days=10: {"trade_date": trade_date, "prev_zt": _zt(trade_date, []),
                                                           "prev_zt_ok": True, "prev_date": "20260805",
                                                           "height_series": [], "moneyflow": _zt(trade_date, []),
                                                           "moneyflow_ok": False, "lhb_daily": _zt(trade_date, []),
                                                           "lhb_seats": _zt(trade_date, []), "lhb_ok": False})
        monkeypatch.setattr(pipeline, "compute_dashboard_detail", lambda c, d: detail)
        out = tmp_path / "output" / "20260806_看板.html"
        html_text = generate_dashboard("20260806", n_days=3, out_path=out)
        assert out.exists()
        assert "数据看板" in html_text
        assert "2026-08-06" in html_text
        assert "const DATA =" in html_text
        assert "（未生成解读）" not in html_text
        assert "多日趋势解读" not in html_text
        assert 'data-tab="structure"' in html_text

    def test_generate_detail_failure_degrades(self, monkeypatch, tmp_path):
        """明细组装失败 → 降级为基础看板，不阻断生成。"""
        collected = _collected()
        indicators = {**_empty_indicators(), "emotion": {"available": False, "series": []}}
        monkeypatch.setattr(pipeline, "collect_dashboard", lambda trade_date, n_days=10: collected)
        monkeypatch.setattr(pipeline, "compute_dashboard", lambda c: indicators)
        monkeypatch.setattr(pipeline, "collect_dashboard_detail",
                            lambda trade_date, n_days=10: (_ for _ in ()).throw(RuntimeError("detail boom")))
        monkeypatch.setattr(pipeline, "compute_dashboard_detail", lambda c, d: None)
        out = tmp_path / "output" / "20260806_看板.html"
        html_text = generate_dashboard("20260806", n_days=3, out_path=out)
        assert out.exists()
        assert "数据不足" in html_text


class TestDashboardDetail:
    """compute_dashboard_detail：梯队/题材/涨停明细组装 + KPI 汇总 + 缺失降级（全离线）。"""

    def _collected(self):
        """完整字段集合：zt 带 name/industry/封单/成交额/换手/首封。"""
        zt = pd.DataFrame([
            {"code": "002403", "name": "爱仕达", "lb_num": 4, "first_limit_time": "09:25",
             "open_times": 1, "seal_amount": 8e7, "amount": 5.9e8, "turnover": 14.7,
             "industry": "小家电", "trade_date": "20260908"},
            {"code": "000523", "name": "红棉股份", "lb_num": 2, "first_limit_time": "09:25",
             "open_times": 0, "seal_amount": 2e8, "amount": 4.9e8, "turnover": 6.9,
             "industry": "小家电", "trade_date": "20260908"},
            {"code": "000823", "name": "超声电子", "lb_num": 1, "first_limit_time": "10:02",
             "open_times": 2, "seal_amount": 1e7, "amount": 1.2e8, "turnover": 8.2,
             "industry": "消费电子", "trade_date": "20260908"},
            {"code": "000798", "name": "中水渔业", "lb_num": 1, "first_limit_time": "10:30",
             "open_times": 0, "seal_amount": 3e7, "amount": 0.8e8, "turnover": 3.1,
             "industry": "渔业", "trade_date": "20260908"},
        ])
        zb = pd.DataFrame({"code": ["600001", "600002"], "trade_date": "20260908"})
        prev_zt = pd.DataFrame([
            {"code": "002403", "lb_num": 3, "industry": "小家电", "trade_date": "20260907"},
            {"code": "000523", "lb_num": 1, "industry": "小家电", "trade_date": "20260907"},
            {"code": "000823", "lb_num": 1, "industry": "消费电子", "trade_date": "20260907"},
            {"code": "000798", "lb_num": 1, "industry": "渔业", "trade_date": "20260907"},
        ])
        return {
            "trade_date": "20260908",
            "zt": zt, "zb": zb,
            "hist_days": [{"date": "20260907", "zt": prev_zt}],
        }

    def _detail(self, **over):
        d = {
            "prev_zt": pd.DataFrame([
                {"code": "002403", "lb_num": 3, "trade_date": "20260907"},
                {"code": "000523", "lb_num": 1, "trade_date": "20260907"},
                {"code": "000823", "lb_num": 1, "trade_date": "20260907"},
                {"code": "000798", "lb_num": 1, "trade_date": "20260907"},
            ]),
            "prev_zt_ok": True,
            "height_series": [{"date": "20260908", "max_lb": 4}, {"date": "20260907", "max_lb": 3}],
            "moneyflow": pd.DataFrame([
                {"code": "600001", "main_net_inflow": -1e8},
                {"code": "600002", "main_net_inflow": 5e7},
            ]),
            "moneyflow_ok": True,
            "lhb_daily": pd.DataFrame([{"code": "002403", "lhb_net_amt": 4e8}]),
            "lhb_seats": pd.DataFrame(),
            "lhb_ok": True,
        }
        d.update(over)
        return d

    def test_assembles_ladder_themes_ztlist(self):
        res = compute_dashboard_detail(self._collected(), self._detail())
        # 梯队表：板数从高到低（含空板位，与复盘 _build_ladder_table 口径一致）
        heights = [row["height"] for row in res["ladder"]["ladder"]]
        assert heights == [4, 3, 2, 1]
        assert res["ladder"]["ladder"][0]["count"] == 1
        assert sum(row["count"] for row in res["ladder"]["ladder"]) == 4
        # 晋级率：今日连板 2 / 昨日涨停 4
        assert res["promote_rate"] == pytest.approx(0.5)
        # 题材按 industry 分组：小家电 2 只 / 消费电子 1 只 / 渔业 1 只
        names = [t["theme_name"] for t in res["themes"]]
        assert set(names) == {"消费电子", "小家电", "渔业"}
        assert next(t for t in res["themes"] if t["theme_name"] == "小家电")["max_lb"] == 4
        # 涨停明细：金额转亿元、列齐
        assert len(res["zt_list"]) == 4
        row = next(r for r in res["zt_list"] if r["code"] == "002403")
        assert row["seal_amount"] == pytest.approx(0.8)
        assert row["amount"] == pytest.approx(5.9)
        assert row["turnover"] == pytest.approx(14.7)
        assert row["industry"] == "小家电"
        # KPI 汇总
        assert res["mf_net"] == pytest.approx(-0.5)   # (-1e8 + 5e7) / 1e8
        assert res["lhb_net"] == pytest.approx(4.0)
        assert res["zt_amount"] == pytest.approx(12.8)
        assert res["flags"] == {"prev_zt_ok": True, "moneyflow_ok": True, "lhb_ok": True}

    def test_missing_optional_dimensions(self):
        collected = self._collected()
        detail = self._detail(prev_zt_ok=False, moneyflow_ok=False, lhb_ok=False)
        res = compute_dashboard_detail(collected, detail)
        assert res["promote_rate"] is None
        assert res["mf_net"] is None
        assert res["lhb_net"] is None
        assert res["flags"]["prev_zt_ok"] is False

    def test_empty_zt_degrades(self):
        collected = self._collected()
        collected["zt"] = pd.DataFrame(
            columns=["code", "name", "lb_num", "first_limit_time", "open_times",
                     "seal_amount", "amount", "turnover", "industry"])
        res = compute_dashboard_detail(collected, self._detail())
        assert res["zt_list"] == []
        assert res["ladder"]["ladder"] == []
        assert res["themes"] == []
        assert res["zt_amount"] is None


class TestCollectDetail:
    """collect_dashboard_detail：prev_zt / height_series 读盘重建 + 可选项降级（零网络）。"""

    def test_rebuilds_prev_and_height(self, monkeypatch):
        prev = pd.DataFrame([{"trade_date": "20260907", "code": "002403", "lb_num": 3}])
        day = pd.DataFrame([{"trade_date": "20260906", "code": "000001", "lb_num": 5}])

        def fake_resolve(today, n_days=1):
            dates = []
            d = 20260907
            for _ in range(n_days):
                dates.append(str(d))
                d -= 1
            return dates

        def fake_cached(name, trade_date, fetch_fn, use_cache=True):
            return prev if trade_date == "20260907" else day

        def fake_load_csv(name, trade_date):
            raise FileNotFoundError(name)

        monkeypatch.setattr(pipeline.em, "resolve_recent_trade_dates", fake_resolve)
        monkeypatch.setattr(pipeline, "_cached", fake_cached)
        monkeypatch.setattr(pipeline, "load_csv", fake_load_csv)
        detail = collect_dashboard_detail("20260908", n_days=4)
        assert detail["prev_date"] == "20260907"
        assert detail["prev_zt_ok"] is True and len(detail["prev_zt"]) == 1
        # height_series：最新在前，20260908 用当日池（day=5 板）
        assert detail["height_series"][0] == {"date": "20260908", "max_lb": 5}
        assert detail["height_series"][1] == {"date": "20260907", "max_lb": 3}
        # 可选项读盘失败 → ok=False，不抛
        assert detail["moneyflow_ok"] is False
        assert detail["lhb_ok"] is False
