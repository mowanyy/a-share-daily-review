"""东财池子解析离线测试：纯函数 + mock 网络层（不联网）。"""

from __future__ import annotations

from daily_review.data import eastmoney_pool as em


class TestScalarHelpers:
    def test_time_of(self):
        assert em._time_of(92500) == "09:25"
        assert em._time_of(130123) == "13:01"
        assert em._time_of(None) == ""
        assert em._time_of("") == ""

    def test_num(self):
        assert em._num(123.45) == 123.45
        assert em._num("1.2") == 1.2
        assert em._num(None) is None
        assert em._num("abc") is None


class TestZTPoolParse:
    @staticmethod
    def _mock_pool(monkeypatch, items):
        def fake_pool_json(endpoint, date, pagesize=300):
            assert endpoint == "getTopicZTPool"
            return items
        monkeypatch.setattr(em, "_pool_json", fake_pool_json)

    def test_zt_columns_and_values(self, monkeypatch):
        self._mock_pool(monkeypatch, [
            {"c": "600000", "n": "浦发银行", "lbc": 2, "fbt": 92500, "lbt": 93000,
             "zbc": 1, "fund": 123456789, "hs": 5.5, "amount": 1.2e9, "hybk": "银行"},
        ])
        df = em.fetch_zt_pool("20260806")
        assert list(df.columns) == em.ZT_COLUMNS
        r = df.iloc[0]
        assert r["code"] == "600000" and r["name"] == "浦发银行"
        assert r["lb_num"] == 2
        assert r["first_limit_time"] == "09:25"
        assert r["last_limit_time"] == "09:30"
        assert r["open_times"] == 1
        assert r["seal_amount"] == 123456789.0
        assert r["turnover"] == 5.5
        assert r["industry"] == "银行"

    def test_zt_empty(self, monkeypatch):
        self._mock_pool(monkeypatch, [])
        df = em.fetch_zt_pool("20260806")
        assert df.empty
        assert list(df.columns) == em.ZT_COLUMNS


class TestZBPoolParse:
    def test_zb_columns(self, monkeypatch):
        def fake_pool_json(endpoint, date, pagesize=300):
            assert endpoint == "getTopicZBPool"
            return [{"c": "600001", "n": "甲", "zbc": 3, "fbt": 93000, "zdp": 4.5, "hybk": "通信"}]
        monkeypatch.setattr(em, "_pool_json", fake_pool_json)
        df = em.fetch_zb_pool("20260806")
        assert list(df.columns) == em.ZB_COLUMNS
        r = df.iloc[0]
        assert r["code"] == "600001" and r["break_times"] == 3
        assert r["first_seal_time"] == "09:30" and r["up_pct"] == 4.5


class TestRecentTradeDates:
    @staticmethod
    def _mock_cache(monkeypatch):
        """隔离真实交易日历缓存（避免上次 update-data 实测数据污染断言）。"""
        import daily_review.data.local_cache as lc

        monkeypatch.setattr(lc, "load_trade_dates", lambda: set())
        monkeypatch.setattr(lc, "add_trade_dates", lambda dates: None)

    def test_probe_skips_empty_days(self, monkeypatch):
        # 8/8(六)、8/7(五) 空 → 应回退到 8/6(四)
        self._mock_cache(monkeypatch)
        def fake_pool_json(endpoint, date, pagesize=300):
            return [] if date in ("20260808", "20260807") else [{"c": "1", "n": "x"}]
        monkeypatch.setattr(em, "_pool_json", fake_pool_json)
        dates = em.resolve_recent_trade_dates("20260808", n_days=1)
        assert dates == ["20260806"]

    def test_probe_returns_multiple(self, monkeypatch):
        # 交易日 8/4,8/5,8/6 有数据；8/7,8/8 空
        self._mock_cache(monkeypatch)
        valid = {"20260806", "20260805", "20260804"}
        def fake_pool_json(endpoint, date, pagesize=300):
            return [{"c": "1", "n": "x"}] if date in valid else []
        monkeypatch.setattr(em, "_pool_json", fake_pool_json)
        dates = em.resolve_recent_trade_dates("20260808", n_days=2)
        assert dates == ["20260806", "20260805"]


class TestConceptBoardsParse:
    @staticmethod
    def _mock_clist(monkeypatch, diff):
        def fake_clist(params):
            assert params["fs"] == "m:90+t:2"
            assert "f128" in params["fields"] and "f140" in params["fields"] and "f136" in params["fields"]
            return {"rc": 0, "data": {"diff": diff}}
        monkeypatch.setattr(em, "_clist_json", fake_clist)

    def test_leader_fields_when_returned(self, monkeypatch):
        self._mock_clist(monkeypatch, [
            {"f12": "BK1320", "f14": "逆变器", "f3": 3.1, "f62": 1.04e9,
             "f128": "德业股份", "f140": "605117", "f136": 6.58},
        ])
        df = em.fetch_concept_boards()
        assert list(df.columns) == em.CONCEPT_BOARD_COLUMNS
        r = df.iloc[0]
        assert r["board_code"] == "BK1320" and r["board_name"] == "逆变器"
        assert r["pct"] == 3.1 and r["main_net_inflow"] == 1.04e9
        assert r["leader_code"] == "605117" and r["leader_name"] == "德业股份"
        assert r["leader_pct"] == 6.58

    def test_leader_fields_empty_fallback_to_4_cols(self, monkeypatch):
        # 接口未返回领涨字段（f128/f140/f136 为空）→ 自动回退 4 列，下游契约稳定
        self._mock_clist(monkeypatch, [
            {"f12": "BK1001", "f14": "样本", "f3": 2.0, "f62": 3e7, "f128": "", "f140": "", "f136": None},
        ])
        df = em.fetch_concept_boards()
        assert list(df.columns) == ["board_code", "board_name", "pct", "main_net_inflow"]

    def test_empty_diff(self, monkeypatch):
        self._mock_clist(monkeypatch, [])
        df = em.fetch_concept_boards()
        assert df.empty
        assert list(df.columns) == em.CONCEPT_BOARD_COLUMNS
