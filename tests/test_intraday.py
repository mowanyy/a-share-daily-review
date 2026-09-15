"""analysis/intraday.py 盘中增量监控测试：diff 纯函数逻辑 + 基准/快照流（mock 接口）。"""

from __future__ import annotations

import pandas as pd

from daily_review.analysis.intraday import (
    _baseline_path,
    diff,
    load_snapshots,
    snapshot,
    summary,
    take_baseline,
)


def _make_zt_df(codes: list[str], open_times: list[int] | None = None) -> pd.DataFrame:
    """构造涨停池 DataFrame（行数与 codes 一致）。"""
    if open_times is None:
        open_times = [1] * len(codes)
    return pd.DataFrame({
        "trade_date": ["20260818"] * len(codes),
        "code": codes,
        "name": [f"股{c}" for c in codes],
        "lb_num": [1] * len(codes),
        "first_limit_time": ["09:30"] * len(codes),
        "open_times": open_times,
        "seal_amount": [1e8] * len(codes),
        "industry": [""] * len(codes),
    })


def _make_zb_df(codes: list[str]) -> pd.DataFrame:
    return pd.DataFrame({
        "trade_date": ["20260818"] * len(codes),
        "code": codes,
        "name": [f"炸{c}" for c in codes],
        "break_times": [1] * len(codes),
        "first_seal_time": ["09:30"] * len(codes),
        "up_pct": [9.5] * len(codes),
        "industry": [""] * len(codes),
    })


# ---------------------------------------------------------------- diff 纯函数


class TestDiff:
    def test_new_zt_detected(self):
        bl = {"zt": {"codes": ["A", "B"], "seal_map": {}, "open_times_map": {"A": 1, "B": 1}}}
        cur = _make_zt_df(["A", "B", "C"])
        d = diff(bl, cur, _make_zb_df([]))
        assert d["new_zt"] == ["C"]
        assert d["broken"] == []
        assert d["re_sealed"] == []

    def test_broken_detected(self):
        bl = {"zt": {"codes": ["A", "B", "C"], "seal_map": {}, "open_times_map": {"A": 1, "B": 1, "C": 1}}}
        cur = _make_zt_df(["A", "B"])
        d = diff(bl, cur, _make_zb_df([]))
        assert d["broken"] == ["C"]
        assert d["new_zt"] == []

    def test_re_sealed_detected(self):
        bl = {"zt": {"codes": ["A", "B"], "seal_map": {}, "open_times_map": {"A": 1, "B": 1}}}
        cur = _make_zt_df(["A", "B"], open_times=[2, 1])
        d = diff(bl, cur, _make_zb_df([]))
        assert d["re_sealed"] == ["A"]
        assert d["new_zt"] == []
        assert d["broken"] == []

    def test_no_change_returns_empty(self):
        bl = {"zt": {"codes": ["A", "B"], "seal_map": {}, "open_times_map": {"A": 1, "B": 1}}}
        cur = _make_zt_df(["A", "B"])
        d = diff(bl, cur, _make_zb_df([]))
        assert d["new_zt"] == []
        assert d["broken"] == []
        assert d["re_sealed"] == []

    def test_empty_baseline(self):
        bl = {"zt": {"codes": [], "seal_map": {}, "open_times_map": {}}}
        cur = _make_zt_df(["A", "B"])
        d = diff(bl, cur, _make_zb_df([]))
        assert d["new_zt"] == ["A", "B"]


# ---------------------------------------------------------------- 基准/快照流


class TestTakeBaseline:
    def test_creates_file_and_returns_dict(self, monkeypatch, tmp_path):
        monkeypatch.setattr("daily_review.data.eastmoney_pool.fetch_zt_pool", lambda td: _make_zt_df(["A", "B"]))
        monkeypatch.setattr("daily_review.data.eastmoney_pool.fetch_zb_pool", lambda td: _make_zb_df(["C"]))
        monkeypatch.setattr("daily_review.config.get_settings", lambda: type("s", (), {"data_dir": tmp_path})())
        bl = take_baseline("20260818")
        assert bl["zt_count"] == 2
        assert bl["zb_count"] == 1
        assert _baseline_path("20260818").exists()
        # 第二次调用（force=False）返回缓存，不再调用 fetch
        calls = []
        monkeypatch.setattr("daily_review.data.eastmoney_pool.fetch_zt_pool", lambda td: calls.append(1) or _make_zt_df(["X"]))
        bl2 = take_baseline("20260818")
        assert bl2["zt_count"] == 2  # 缓存数据，不是新拉的
        assert len(calls) == 0

    def test_force_refreshes(self, monkeypatch, tmp_path):
        monkeypatch.setattr("daily_review.data.eastmoney_pool.fetch_zt_pool", lambda td: _make_zt_df(["A"]))
        monkeypatch.setattr("daily_review.data.eastmoney_pool.fetch_zb_pool", lambda td: _make_zb_df([]))
        monkeypatch.setattr("daily_review.config.get_settings", lambda: type("s", (), {"data_dir": tmp_path})())
        take_baseline("20260818")
        monkeypatch.setattr("daily_review.data.eastmoney_pool.fetch_zt_pool", lambda td: _make_zt_df(["B"]))
        bl2 = take_baseline("20260818", force=True)
        assert bl2["zt_count"] == 1
        assert bl2["zt"]["codes"] == ["B"]


class TestSnapshotFlow:
    def test_snapshot_creates_record(self, monkeypatch, tmp_path):
        monkeypatch.setattr("daily_review.config.get_settings", lambda: type("s", (), {"data_dir": tmp_path})())
        monkeypatch.setattr("daily_review.data.eastmoney_pool.fetch_zt_pool", lambda td: _make_zt_df(["A", "B"]))
        monkeypatch.setattr("daily_review.data.eastmoney_pool.fetch_zb_pool", lambda td: _make_zb_df([]))
        take_baseline("20260818")
        monkeypatch.setattr("daily_review.data.eastmoney_pool.fetch_zt_pool", lambda td: _make_zt_df(["A", "B", "C"]))
        delta = snapshot("20260818")
        assert delta["new_zt"] == ["C"]
        records = load_snapshots("20260818")
        assert len(records) == 1
        assert records[0]["new_zt"] == ["C"]

    def test_summary_aggregates(self, monkeypatch, tmp_path):
        monkeypatch.setattr("daily_review.config.get_settings", lambda: type("s", (), {"data_dir": tmp_path})())
        monkeypatch.setattr("daily_review.data.eastmoney_pool.fetch_zt_pool", lambda td: _make_zt_df(["A", "B"]))
        monkeypatch.setattr("daily_review.data.eastmoney_pool.fetch_zb_pool", lambda td: _make_zb_df([]))
        take_baseline("20260818")
        monkeypatch.setattr("daily_review.data.eastmoney_pool.fetch_zt_pool", lambda td: _make_zt_df(["A", "B", "C"]))
        snapshot("20260818")
        s = summary("20260818")
        assert s["status"] == "ok"
        assert s["cumulative_new_zt"] == ["C"]
        assert s["baseline_zt_count"] == 2
        assert s["latest_zt_count"] == 3