"""pipeline 概念板块可选块测试：仅当日采集 + 历史日期硬守卫 + 失败降级 + compute 透传（离线）。"""

from __future__ import annotations

import datetime as _real_dt

import pandas as pd

import daily_review.pipeline as pl
from daily_review.data import eastmoney_pool as em


class _FakeDT:
    """可设 now() 的假 datetime（strptime 透传真实现）。"""
    now_value = _real_dt.datetime(2026, 8, 11, 16, 0)

    @classmethod
    def now(cls):
        return cls.now_value

    strptime = staticmethod(_real_dt.datetime.strptime)


def _boards_df():
    return pd.DataFrame([
        {"board_code": "BK1320", "board_name": "逆变器", "pct": 3.1,
         "main_net_inflow": 1.04e9, "leader_code": "605117",
         "leader_name": "德业股份", "leader_pct": 6.58},
    ])


class TestConceptBoardsBlock:
    def test_today_fetches_and_saves(self, tmp_path, monkeypatch):
        """今日 → 调接口、成功、落盘缓存（save_csv 调用）。"""
        monkeypatch.setattr(pl, "datetime", _FakeDT)
        monkeypatch.setattr(pl, "load_csv", lambda name, d: pd.DataFrame())
        saved = []
        monkeypatch.setattr(pl, "save_csv", lambda df, name, d: saved.append(name))
        monkeypatch.setattr(em, "fetch_concept_boards", lambda: _boards_df())

        df, ok = pl._fetch_concept_boards_block("20260811", fresh=False)
        assert ok is True
        assert list(df.columns) == em.CONCEPT_BOARD_COLUMNS
        assert df.iloc[0]["board_name"] == "逆变器"
        assert "concept_boards" in saved

    def test_historical_returns_empty_false_without_fetch(self, monkeypatch):
        """历史日期 → 空表+False，根本不调接口（防 clist 实时快照误导）。"""
        monkeypatch.setattr(pl, "datetime", _FakeDT)
        called = {"n": 0}
        monkeypatch.setattr(
            em, "fetch_concept_boards",
            lambda: (called.__setitem__("n", called["n"] + 1), _boards_df())[1],
        )
        df, ok = pl._fetch_concept_boards_block("20260806", fresh=True)
        assert ok is False
        assert df.empty
        assert list(df.columns) == em.CONCEPT_BOARD_COLUMNS
        assert called["n"] == 0

    def test_fetch_failure_returns_empty_false(self, monkeypatch):
        """今日但接口失败 → 空表+False，不中断 collect。"""
        monkeypatch.setattr(pl, "datetime", _FakeDT)
        monkeypatch.setattr(pl, "load_csv", lambda name, d: pd.DataFrame())
        monkeypatch.setattr(pl, "save_csv", lambda *a, **k: None)

        def boom():
            raise RuntimeError("network down")

        monkeypatch.setattr(em, "fetch_concept_boards", boom)
        df, ok = pl._fetch_concept_boards_block("20260811", fresh=True)
        assert ok is False
        assert df.empty
        assert list(df.columns) == em.CONCEPT_BOARD_COLUMNS


class TestConceptBoardsCollectGuard:
    def _zt(self):
        return pd.DataFrame([{"code": "600000", "name": "浦发银行", "lb_num": 1}])

    def test_non_trading_day_no_fetch(self, monkeypatch):
        """zt 为空（非交易日「今日」）→ 根本不调概念接口，避免今日快照写成非交易日数据。"""
        called = {"n": 0}
        monkeypatch.setattr(
            em, "fetch_concept_boards",
            lambda: (called.__setitem__("n", called["n"] + 1), _boards_df())[1],
        )
        df, ok = pl._concept_boards_block(pd.DataFrame(), "20260808")
        assert ok is False
        assert df.empty
        assert list(df.columns) == em.CONCEPT_BOARD_COLUMNS
        assert called["n"] == 0

    def test_fetch_failure_retries_once(self, monkeypatch):
        """今日但接口瞬时失败 → 重试一次并成功，数据落到块（供 concept_map 共用）。"""
        monkeypatch.setattr(pl, "datetime", _FakeDT)
        monkeypatch.setattr(pl, "load_csv", lambda name, d: pd.DataFrame())
        monkeypatch.setattr(pl, "save_csv", lambda *a, **k: None)
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("network flake")
            return _boards_df()

        monkeypatch.setattr(em, "fetch_concept_boards", flaky)
        df, ok = pl._concept_boards_block(self._zt(), "20260811")
        assert ok is True
        assert calls["n"] == 2
        assert df.iloc[0]["board_name"] == "逆变器"

    def test_fetch_failure_both_times_returns_empty(self, monkeypatch):
        monkeypatch.setattr(pl, "datetime", _FakeDT)
        monkeypatch.setattr(pl, "load_csv", lambda name, d: pd.DataFrame())
        monkeypatch.setattr(pl, "save_csv", lambda *a, **k: None)

        def boom():
            raise RuntimeError("down")

        monkeypatch.setattr(em, "fetch_concept_boards", boom)
        df, ok = pl._concept_boards_block(self._zt(), "20260811")
        assert ok is False
        assert df.empty
        assert list(df.columns) == em.CONCEPT_BOARD_COLUMNS


class TestBuildConceptMapReuse:
    def test_accepts_boards_no_internal_fetch(self, monkeypatch):
        """传 boards → 复用概念块数据，不重复联网。"""
        fetched = {"n": 0}
        monkeypatch.setattr(
            em, "fetch_concept_boards",
            lambda: (fetched.__setitem__("n", fetched["n"] + 1), _boards_df())[1],
        )
        monkeypatch.setattr(em, "fetch_board_constituents", lambda code: ["600000", "300001"])
        mapping = pl._build_concept_map(["600000", "300001"], "20260811", boards=_boards_df())
        assert fetched["n"] == 0
        assert mapping.get("600000") == ["逆变器"]
        assert mapping.get("300001") == ["逆变器"]

    def test_boards_none_internal_fetch(self, monkeypatch):
        """boards=None → 旧行为内部自取。"""
        fetched = {"n": 0}
        monkeypatch.setattr(
            em, "fetch_concept_boards",
            lambda: (fetched.__setitem__("n", fetched["n"] + 1), _boards_df())[1],
        )
        monkeypatch.setattr(em, "fetch_board_constituents", lambda code: ["600000"])
        mapping = pl._build_concept_map(["600000"], "20260811", boards=None)
        assert fetched["n"] == 1
        assert mapping.get("600000") == ["逆变器"]

    def test_internal_fetch_failure_returns_empty(self, monkeypatch):
        monkeypatch.setattr(em, "fetch_concept_boards", lambda: (_ for _ in ()).throw(RuntimeError()))
        assert pl._build_concept_map(["600000"], "20260811") == {}


class TestComputePassthrough:
    def _noop_analysis(self, monkeypatch):
        """分析函数置空：compute_ladder 返回 {break_rate:…}（compute 下标依赖），其余空容器。"""
        monkeypatch.setattr("daily_review.pipeline.compute_ladder", lambda *a, **k: {"break_rate": 0.0})
        monkeypatch.setattr("daily_review.pipeline.build_themes", lambda *a, **k: [])
        monkeypatch.setattr("daily_review.pipeline.analyze_break", lambda *a, **k: {})
        monkeypatch.setattr("daily_review.pipeline.analyze_lhb", lambda *a, **k: {})
        monkeypatch.setattr("daily_review.pipeline.compute_emotion", lambda *a, **k: {})

    def test_concept_boards_list(self, monkeypatch):
        """compute 把 concept_boards 透传为压缩列表（供热点模型）。"""
        self._noop_analysis(monkeypatch)
        collected = {
            "trade_date": "20260811",
            "zt": pd.DataFrame(), "zb": pd.DataFrame(), "dt": pd.DataFrame(),
            "prev_zt": pd.DataFrame(), "prev_pools": [], "height_series": [],
            "concept_map": {}, "moneyflow": pd.DataFrame(),
            "lhb_daily": pd.DataFrame(), "lhb_seats": pd.DataFrame(),
            "hist_days": [], "zb_ok": True, "dt_ok": True,
            "is_intraday": False, "timeline_dates": ["20260811"],
            "concept_boards": _boards_df(),
        }
        ind = pl.compute(collected)
        assert ind["concept_boards"][0]["board_name"] == "逆变器"
        assert ind["concept_boards"][0]["leader_name"] == "德业股份"
        assert ind["concept_boards"][0]["pct"] == 3.1

    def test_missing_concept_boards_empty(self, monkeypatch):
        """无概念块（历史/失败）→ 空列表，报告不注入。"""
        self._noop_analysis(monkeypatch)
        collected = {
            "trade_date": "20260806",
            "zt": pd.DataFrame(), "zb": pd.DataFrame(), "dt": pd.DataFrame(),
            "prev_zt": pd.DataFrame(), "prev_pools": [], "height_series": [],
            "concept_map": {}, "moneyflow": pd.DataFrame(),
            "lhb_daily": pd.DataFrame(), "lhb_seats": pd.DataFrame(),
            "hist_days": [], "zb_ok": True, "dt_ok": True,
            "is_intraday": False, "timeline_dates": ["20260806"],
        }
        ind = pl.compute(collected)
        assert ind["concept_boards"] == []

    def _collected_with(self, cb, **extra):
        base = {
            "trade_date": "20260811",
            "zt": pd.DataFrame(), "zb": pd.DataFrame(), "dt": pd.DataFrame(),
            "prev_zt": pd.DataFrame(), "prev_pools": [], "height_series": [],
            "concept_map": {}, "moneyflow": pd.DataFrame(),
            "lhb_daily": pd.DataFrame(), "lhb_seats": pd.DataFrame(),
            "hist_days": [], "zb_ok": True, "dt_ok": True,
            "is_intraday": False, "timeline_dates": ["20260811"],
        }
        base["concept_boards"] = cb
        base.update(extra)
        return base

    def test_csv_roundtrip_sanitizes_leaders(self, monkeypatch):
        """CSV 往返后领涨代码变 float/NaN（前导零丢失、空→nan）→ 标准化为 6 位/空串。"""
        self._noop_analysis(monkeypatch)
        # 模拟 to_csv→read_csv：000001 → 1.0；空 leader_name → NaN；整列空 board_name → NaN
        cb = pd.DataFrame({
            "board_code": ["BK1320", "BK1001"],
            "board_name": ["逆变器", None],
            "pct": [3.1, 2.0],
            "main_net_inflow": [1.04e9, None],
            "leader_code": [1.0, None],          # 000001 → 1.0
            "leader_name": [None, "德业股份"],
            "leader_pct": [6.58, None],
        })
        ind = pl.compute(self._collected_with(cb))
        rows = ind["concept_boards"]
        assert rows[0]["board_name"] == "逆变器"
        assert rows[0]["leader_code"] == "000001"   # 前导零补回
        assert rows[0]["leader_name"] == ""          # NaN → ""
        assert rows[1]["board_name"] == ""           # NaN → ""
        assert rows[1]["main_net_inflow"] is None
        assert rows[1]["leader_pct"] is None

    def test_missing_pct_column_no_crash(self, monkeypatch):
        """缺 pct 列（缓存损坏/旧版）→ 不抛 KeyError，按原顺序透传。"""
        self._noop_analysis(monkeypatch)
        cb = pd.DataFrame({
            "board_code": ["BK1320"],
            "board_name": ["逆变器"],
            "main_net_inflow": [1.04e9],
            "leader_name": ["德业股份"],
        })
        ind = pl.compute(self._collected_with(cb))
        assert ind["concept_boards"][0]["board_name"] == "逆变器"
        assert ind["concept_boards"][0]["pct"] is None
