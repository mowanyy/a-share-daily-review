"""data/trade_calendar.py 测试：文件读写 / 判定 / 回推 / 兜底 / resolve 集成（mock 取数）。

v0.31.1：`_is_stale` 改为「表最新日期 >= 昨日前最近工作日」——8/20 时表只到 8/18
（缺 8/19 交易日）即判过期触发刷新，prev 日期不再误用两天前数据。所有会经
`_load()` 的用例必须 mock `_fetch_kline_dates`（否则判过期后会真联网）。
"""

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


def _mock_fetch(monkeypatch, dates: set[str]) -> None:
    """mock 联网拉表：返回指定集合（含 8/19 时即模拟刷新成功）。"""
    monkeypatch.setattr(cal, "_fetch_kline_dates", lambda: dates)


def _mock_fetch_fail(monkeypatch) -> None:
    """mock 联网拉表失败（保留旧表，不更新）。"""
    monkeypatch.setattr(
        cal, "_fetch_kline_dates",
        lambda: (_ for _ in ()).throw(OSError("down")),
    )


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
        # 刷新失败 → 保留旧表：表内交易日 True，表覆盖内无此日 False，>max 且未拉到 → None
        _seed(tmp_path, monkeypatch, {"20260817", "20260818"})  # 周一、周二
        _mock_fetch_fail(monkeypatch)
        assert cal.is_trade_date("20260818") is True
        assert cal.is_trade_date("20260815") is False   # 周六不在表
        assert cal.is_trade_date("20260210") is False   # 表存在但无此日（休市）
        assert cal.is_trade_date("20260819") is None    # 缺最近交易日且刷新失败 → 未知
        assert cal.is_trade_date("2026-08-18") is None  # 格式不合法

    def test_no_table_network_fail_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cal, "_table_path", lambda: tmp_path / "nope.csv")
        _mock_fetch_fail(monkeypatch)
        cal.reload()
        assert cal.is_trade_date("20260818") is None

    def test_recent_trade_dates_backfills(self, tmp_path, monkeypatch):
        _seed(tmp_path, monkeypatch, {"20260818", "20260817", "20260814", "20260813"})
        _mock_fetch(monkeypatch, {"20260818", "20260817", "20260814", "20260813"})
        assert cal.recent_trade_dates("20260818", 3) == ["20260818", "20260817", "20260814"]
        assert cal.recent_trade_dates("20260817", 2) == ["20260817", "20260814"]

    def test_holidays_of_year_excludes_weekend_and_future(self, tmp_path, monkeypatch):
        _seed(tmp_path, monkeypatch, {"20260817", "20260818"})
        _mock_fetch_fail(monkeypatch)
        h = cal.holidays_of_year(2026)
        assert "20260815" not in h  # 周六不算
        assert "20260816" not in h  # 周日不算
        assert "20260813" in h      # 表内工作日缺 → 休市日（周四）
        assert "20260819" not in h  # >表max（刷新失败未拉到）→ 不列入

    def test_stale_table_triggers_refetch(self, tmp_path, monkeypatch):
        """表最新日期早于昨日前最近工作日（缺交易日）→ 触发联网刷新。"""
        import datetime
        import os
        import time

        p = tmp_path / "trade_calendar.csv"
        p.write_text("20260801\n20260802\n", encoding="utf-8")
        monkeypatch.setattr(cal, "_table_path", lambda: p)
        # 旧表 mtime 符合 TTL，但内容最新日期远早于最近工作日 → 触发补拉
        _mock_fetch(monkeypatch, {"20260817", "20260818"})
        dates = cal._load()
        assert "20260817" in dates
        assert "20260818" in dates
        assert "20260801" not in dates  # 旧表被覆盖


class TestStaleRefresh:
    """v0.31.1 核心回归：过期判定收紧后 prev 日期不再误用两天前数据。"""

    def _seed_old_table(self, tmp_path, monkeypatch):
        """8/18 前有数据但缺 8/19 交易日的旧表（= 8/20 开盘策略 bug 现场）。"""
        _seed(tmp_path, monkeypatch, {"20260814", "20260817", "20260818"})
        # 模拟联网拉表成功：上证日K 带回 8/19（含今天 8/20 的日K 盘中未生成）
        _mock_fetch(monkeypatch, {"20260814", "20260817", "20260818", "20260819"})

    def test_load_refreshes_stale_table(self, tmp_path, monkeypatch):
        """表缺最近交易日 → _load() 自动联网补拉到最新，返回含新日期的表。"""
        self._seed_old_table(tmp_path, monkeypatch)
        dates = cal._load()
        assert "20260819" in dates  # 旧表被刷新，8/19 交易日已入表

    def test_recent_trade_dates_includes_refreshed_date(self, tmp_path, monkeypatch):
        """刷新后 recent_trade_dates 正确回推：8/20 的前一交易日是 8/19。"""
        self._seed_old_table(tmp_path, monkeypatch)
        assert cal.recent_trade_dates("20260820", 2) == ["20260819", "20260818"]

    def test_resolve_prev_trade_date_regression(self, tmp_path, monkeypatch):
        """resolve_recent_trade_dates("20260820", 2) 必须返回 8/19（此前错误返回 8/18）。"""
        self._seed_old_table(tmp_path, monkeypatch)
        dates = em.resolve_recent_trade_dates("20260820", n_days=2)
        assert dates[0] == "20260819", f"prev 日期应为 8/19，实际 {dates}"

    def test_is_trade_date_refreshed_recent_day(self, tmp_path, monkeypatch):
        """刷新成功后 8/19 判定为交易日（此前因 >表max 被误判为未来/None）。

        注意：mock 表只拉到 8/19（模拟盘中当天日K 未生成），8/20 > 表max → None 为正确。
        """
        self._seed_old_table(tmp_path, monkeypatch)
        assert cal.is_trade_date("20260819") is True
        assert cal.is_trade_date("20260818") is True


class TestResolveIntegration:
    """eastmoney_pool.resolve_recent_trade_dates 日历优先 + 探测兜底。"""

    def test_calendar_used_when_complete(self, monkeypatch):
        """日历新鲜且长度足 → 采信日历，不走探测。"""
        monkeypatch.setattr(cal, "recent_trade_dates", lambda start, n_days: ["20260818", "20260817"])
        monkeypatch.setattr(cal, "is_fresh", lambda: True)
        monkeypatch.setattr(
            em, "_resolve_trade_dates_by_probe",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("应走日历，不走探测")),
        )
        assert em.resolve_recent_trade_dates("20260818", 2) == ["20260818", "20260817"]

    def test_probe_fallback_when_calendar_short(self, monkeypatch):
        monkeypatch.setattr(cal, "recent_trade_dates", lambda start, n_days: ["20260818"])
        monkeypatch.setattr(cal, "is_fresh", lambda: True)
        monkeypatch.setattr(em, "_resolve_trade_dates_by_probe", lambda start, n_days: ["20260818", "20260817"])
        assert em.resolve_recent_trade_dates("20260818", 2) == ["20260818", "20260817"]

    def test_probe_fallback_when_calendar_raises(self, monkeypatch):
        monkeypatch.setattr(cal, "recent_trade_dates", lambda start, n_days: (_ for _ in ()).throw(OSError("bad")))
        monkeypatch.setattr(em, "_resolve_trade_dates_by_probe", lambda start, n_days: ["20260818"])
        assert em.resolve_recent_trade_dates("20260818", 1) == ["20260818"]

    def test_probe_fallback_when_stale_calendar_full_length(self, monkeypatch):
        """v0.31.1：日历过期（缺最近交易日）即使长度足够也不采信 → 走探测。

        此前旧日历能凑够 n_days 就直接返回，导致 prev 引用两天前数据。
        """
        monkeypatch.setattr(cal, "recent_trade_dates", lambda start, n_days: ["20260818", "20260817"])
        monkeypatch.setattr(cal, "is_fresh", lambda: False)  # 表过期
        monkeypatch.setattr(em, "_resolve_trade_dates_by_probe", lambda start, n_days: ["20260819", "20260818"])
        assert em.resolve_recent_trade_dates("20260820", 2) == ["20260819", "20260818"]


class TestProbeSkipsWeekend:
    """v0.31.1：探测兜底跳过周六/周日，清理缓存中的周末误判日期。"""

    def test_weekend_not_probed_nor_cached(self, tmp_path, monkeypatch):
        written: set[str] = set()

        def fake_add(dates):
            written.update(dates)

        # 缓存已混入周末假数据（20260815 周六 / 20260816 周日），且缺 8/17 需探测
        monkeypatch.setattr(
            "daily_review.data.local_cache.load_trade_dates",
            lambda: {"20260813", "20260814", "20260815", "20260816", "20260818", "20260819"},
        )
        monkeypatch.setattr("daily_review.data.local_cache.add_trade_dates", fake_add)
        # 探测：工作日返回非空（是交易日）；周末根本不会被请求
        monkeypatch.setattr(
            em, "_pool_json",
            lambda endpoint, date, pagesize=5: [{"c": "t"}] if not em._weekend(date) else (_ for _ in ()).throw(AssertionError("周末不应探测")),
        )
        dates = em._resolve_trade_dates_by_probe("20260819", n_days=5)
        # 2026-08-16 周日 / 08-15 周六 不在结果中
        assert "20260815" not in dates and "20260816" not in dates
        assert dates[0] == "20260819" and dates[1] == "20260818"  # 由近及远
        # 落盘缓存同样不含周末（周末被 discard 清理）
        assert "20260815" not in written and "20260816" not in written

    def test_start_on_weekend_falls_back_to_friday(self, tmp_path, monkeypatch):
        """start 本身就是周末：跳过当日后继续往前找工作日（周末绝不探测）。"""
        monkeypatch.setattr(
            "daily_review.data.local_cache.load_trade_dates",
            lambda: set(),  # 无缓存 → 全部走探测；周末路径必须被跳过
        )
        probed: list[str] = []

        def fake_pool(endpoint, date, pagesize=5):
            probed.append(date)
            assert not em._weekend(date), f"周末 {date} 不应探测"
            return [{"c": "t"}]  # 工作日探测命中

        monkeypatch.setattr(em, "_pool_json", fake_pool)
        # 2026-08-16 是周日：应跳过 8/16、8/15，从 8/14（周五）开始计入
        dates = em._resolve_trade_dates_by_probe("20260816", n_days=3)
        assert dates == ["20260814", "20260813", "20260812"]
        assert all(not em._weekend(d) for d in dates)