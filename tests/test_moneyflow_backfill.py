"""fetch_moneyflow 缺失回退测试：当日 clist 只返回主力净流入 Top-100（东财每页固定 100 行），
对 clist 拿不到的请求代码逐个回退单股 fflow 补齐，避免「部分票没数据」（离线，不发真实请求）。"""

from __future__ import annotations

import datetime as _real_dt

import pandas as pd

import daily_review.data.eastmoney_pool as em


def _today() -> str:
    return _real_dt.datetime.now().strftime("%Y%m%d")


def _clist_df(codes, names=None):
    names = names or {}
    return pd.DataFrame([
        {"trade_date": _today(), "code": c, "name": names.get(c, c),
         "main_net_inflow": 1e9, "super_net_inflow": 8e8, "big_net_inflow": 2e8}
        for c in codes
    ])


def _fflow_df(codes, names=None):
    names = names or {}
    return pd.DataFrame([
        {"trade_date": _today(), "code": c, "name": names.get(c, c),
         "main_net_inflow": 5e8, "super_net_inflow": 4e8, "big_net_inflow": 1e8}
        for c in codes
    ])


def _empty_moneyflow_df():
    return pd.DataFrame(
        columns=["trade_date", "code", "name", "main_net_inflow", "super_net_inflow", "big_net_inflow"])


class TestMoneyflowBackfill:
    def test_partial_clist_backfills_missing(self, monkeypatch):
        """clist 只返回 Top-100 里的 3 只 → 缺失的 2 只走 fflow 补齐，最终 5 只全有。"""
        today = _today()
        clist_hit = ["000001", "000002", "600000"]
        all_codes = ["000001", "000002", "600000", "000039", "301201"]
        name_map = {c: "N" + c for c in all_codes}
        monkeypatch.setattr(em, "fetch_moneyflow_clist",
                            lambda codes, d: _clist_df(clist_hit, name_map))
        captured = {}

        def fake_fflow(codes, d, nm):
            captured["codes"] = list(codes)
            return _fflow_df(codes, nm)

        monkeypatch.setattr(em, "_fetch_moneyflow_fflow", fake_fflow)
        df = em.fetch_moneyflow(all_codes, today, name_map)
        assert set(df["code"]) == set(all_codes)            # 3 + 2 = 5 全部补齐
        assert captured["codes"] == ["000039", "301201"]    # 只补缺失的，不重复请求已命中的

    def test_full_clist_no_fflow_call(self, monkeypatch):
        """clist 全部命中 → 不再额外请求 fflow。"""
        today = _today()
        all_codes = ["000001", "600000"]
        name_map = {c: "N" + c for c in all_codes}
        monkeypatch.setattr(em, "fetch_moneyflow_clist",
                            lambda codes, d: _clist_df(all_codes, name_map))
        called = {"n": 0}

        def fake_fflow(*a, **k):
            called["n"] += 1
            return _empty_moneyflow_df()

        monkeypatch.setattr(em, "_fetch_moneyflow_fflow", fake_fflow)
        df = em.fetch_moneyflow(all_codes, today, name_map)
        assert called["n"] == 0
        assert set(df["code"]) == set(all_codes)

    def test_empty_clist_falls_to_fflow_all(self, monkeypatch):
        """clist 返回空表 → 全部代码回退 fflow。"""
        today = _today()
        all_codes = ["000039", "301201"]
        name_map = {c: "N" + c for c in all_codes}
        monkeypatch.setattr(em, "fetch_moneyflow_clist",
                            lambda codes, d: _empty_moneyflow_df())
        captured = {}

        def fake_fflow(codes, d, nm):
            captured["codes"] = list(codes)
            return _fflow_df(codes, nm)

        monkeypatch.setattr(em, "_fetch_moneyflow_fflow", fake_fflow)
        df = em.fetch_moneyflow(all_codes, today, name_map)
        assert set(df["code"]) == set(all_codes)
        assert captured["codes"] == all_codes

    def test_clist_raises_falls_to_fflow_all(self, monkeypatch):
        """clist 抛异常（接口故障）→ 全部代码回退 fflow。"""
        today = _today()

        def boom(codes, d):
            raise RuntimeError("clist down")

        monkeypatch.setattr(em, "fetch_moneyflow_clist", boom)
        monkeypatch.setattr(em, "_fetch_moneyflow_fflow",
                            lambda codes, d, nm: _fflow_df(codes, nm))
        df = em.fetch_moneyflow(["000039"], today, name_map={})
        assert list(df["code"]) == ["000039"]

    def test_historical_date_goes_fflow_only(self, monkeypatch):
        """历史日期 → 不走 clist（clist 只对当日有效），直接单股 fflow。"""
        clist_called = {"n": 0}
        monkeypatch.setattr(em, "fetch_moneyflow_clist",
                            lambda codes, d: clist_called.__setitem__("n", clist_called["n"] + 1))
        monkeypatch.setattr(em, "_fetch_moneyflow_fflow",
                            lambda codes, d, nm: _fflow_df(codes, nm))
        df = em.fetch_moneyflow(["000039"], "20260806", name_map={})
        assert clist_called["n"] == 0
        assert list(df["code"]) == ["000039"]
