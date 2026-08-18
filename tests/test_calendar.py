"""data/trade_calendar.py 测试：文件读写 / 判定 / 回推 / 兜底 / resolve 集成（mock 取数）。"""

from __future__ import annotations

import os
import time

import pytest

from daily_review.data import eastmoney_pool as em
from daily_review.data import trade_calendar as cal


def _seed(tmp_path, monkeypatch, dates: set[str]) -> None:
    monkeypatch.setattr(cal, "_table_path", lambda: tmp_path / "trade_calendar.csv")
    cal.save(dates)
    cal.reload()


class TestSaveLoad:
    def test_roundtrip(self, tmp_path, monkeypatch):
        _seed(tmp_path, monkeypatch, {"20260817", "20260818"})
        assert cal._read_file() == {"20260817", "20260818"}

    def test_corrupt_or_empty_file_yields_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cal, "_table_path", lambda: tmp_path / "trade_calendar.csv")
        (tmp_path / "trade_calendar.csv").write_text("not a date\n", encoding="utf-8")
        cal.reload()
        monkeypatch.setattr(cal, "_fetch_kline_dates", lambda: {"20260818"})
        assert cal._load() == {"20260818"}  # 坏文件视为无表 → 补拉

    def test_expired_file_triggers_refetch(self, tmp_path, monkeypatch):
        p = tmp_path / "trade_calendar.csv"
        p.write_text("20260801\n20260804\n", encoding="utf-8")
        monkeypatch.setattr(cal, "_table_path", lambda: p)
        old = time.time() - cal._TTL_SECONDS - 10
        os.utime(p, (old, old))
        cal.reload()
        monkeypatch.setattr(cal, "_fetch_kline_dates", lambda: {"20260817", "20260818"})
        assert cal._load() == {"20260817", "20260818"}
        assert p.read_text(encoding="utf-8").splitlines() == ["20260817", "20260818"]


class TestIsTradeDate:
    def test_trade_weekend_and_unknown(self, tmp_path, monkeypatch):
        _seed(tmp_path, monkeypatch, {"20260817", "20260818"})  # 周一、周二
        assert cal.is_trade_date("20260818") is True
        assert cal.is_trade_date("20260815") is False   # 周六不在表
        assert cal.is_trade_date("20260210") is False   # 表存在但无此日（休市）
        assert cal.is_trade_date("20260819") is None    # 未来（>表max）→ 未知
        assert cal.is_trade_date("2026-08-18") is None  # 格式不合法

    def test_no_table_network_fail_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cal, "_table_path", lambda: tmp_path / "nope.csv")
        monkeypatch.setattr(cal, "_fetch_kline_dates", lambda: (_ for _ in ()).throw(OSError("down")))
        cal.reload()
        assert cal.is_trade_date("20260818") is None

    def test_recent_trade_dates_backfills(self, tmp_path, monkeypatch):
        _seed(tmp_path, monkeypatch, {"20260818", "20260817", "20260814", "20260813"})
        assert cal.recent_trade_dates("20260818", 3) == ["20260818", "20260817", "20260814"]
        assert cal.recent_trade_dates("20260817", 2) == ["20260817", "20260814"]

    def test_holidays_of_year_excludes_weekend_and_future(self, tmp_path, monkeypatch):
        _seed(tmp_path, monkeypatch, {"20260817", "20260818"})
        h = cal.holidays_of_year(2026)
        assert "20260815" not in h  # 周六不算
        assert "20260816" not in h  # 周日不算
        assert "20260813" in h      # 表内工作日缺 → 休市日（周四）
        assert "20260819" not in h  # 未来（>表max）→ 不列入

    def test_stale_table_triggers_refetch(self, tmp_path, monkeypatch):
        """表最新日期远早于当前 → 触发联网刷新。"""
        import datetime
        import os
        import time

        p = tmp_path / "trade_calendar.csv"
        p.write_text("20260801\n20260802\n", encoding="utf-8")
        monkeypatch.setattr(cal, "_table_path", lambda: p)
        # 旧表 mtime 符合 TTL，但内容最新日期 < today-4 → 触发补拉
        monkeypatch.setattr(cal, "_fetch_kline_dates", lambda: {"20260817", "20260818"})
        dates = cal._load()
        assert "20260817" in dates
        assert "20260818" in dates
        assert "20260801" not in dates  # 旧表被覆盖


class TestResolveIntegration:
    """eastmoney_pool.resolve_recent_trade_dates 日历优先 + 探测兜底。"""

    def test_calendar_used_when_complete(self, monkeypatch):
        monkeypatch.setattr(cal, "recent_trade_dates", lambda start, n_days: ["20260818", "20260817"])
        monkeypatch.setattr(
            em, "_resolve_trade_dates_by_probe",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("应走日历，不走探测")),
        )
        assert em.resolve_recent_trade_dates("20260818", 2) == ["20260818", "20260817"]

    def test_probe_fallback_when_calendar_short(self, monkeypatch):
        monkeypatch.setattr(cal, "recent_trade_dates", lambda start, n_days: ["20260818"])
        monkeypatch.setattr(em, "_resolve_trade_dates_by_probe", lambda start, n_days: ["20260818", "20260817"])
        assert em.resolve_recent_trade_dates("20260818", 2) == ["20260818", "20260817"]

    def test_probe_fallback_when_calendar_raises(self, monkeypatch):
        monkeypatch.setattr(cal, "recent_trade_dates", lambda start, n_days: (_ for _ in ()).throw(OSError("bad")))
        monkeypatch.setattr(em, "_resolve_trade_dates_by_probe", lambda start, n_days: ["20260818"])
        assert em.resolve_recent_trade_dates("20260818", 1) == ["20260818"]