"""管道层离线测试：缓存读回时 code 列零填充；_fetch_opt 容错（成功标志）；save_csv 原子写。"""

from __future__ import annotations

import pandas as pd

from daily_review.pipeline import _cached, _fetch_opt, _owns_trade_date, _zfill_codes


class TestZfillCodes:
    def test_leading_zero_preserved(self):
        # CSV 往返会把 002428 → 2428；读回后必须还原为 6 位字符串
        df = pd.DataFrame({"code": [2428, 600397, "000001", "300001"], "name": list("abcd")})
        out = _zfill_codes(df)
        assert out["code"].tolist() == ["002428", "600397", "000001", "300001"]
        # 字符串类型即可（pandas 3.0 默认 StringDtype，旧版为 object），关键是不能再是数值型
        assert not pd.api.types.is_numeric_dtype(out["code"])

    def test_no_code_column_untouched(self):
        df = pd.DataFrame({"a": [1, 2]})
        assert _zfill_codes(df).equals(df)

    def test_float_code_handled(self):
        # pandas 读 CSV 时可能是 float（全空/混合时）
        df = pd.DataFrame({"code": [2428.0, 600397.0]})
        out = _zfill_codes(df)
        assert out["code"].tolist() == ["002428", "600397"]


class TestFetchOpt:
    def test_failure_returns_empty_and_false(self, monkeypatch):
        # _cached 抛异常（网络/解析失败）→ 返回 (空表, False)，绝不向上抛
        def boom(name, trade_date, fetch_fn, **kwargs):
            raise RuntimeError("network down")

        monkeypatch.setattr("daily_review.pipeline._cached", boom)
        df, ok = _fetch_opt("zb_pool", "20260806", lambda: None)
        assert ok is False
        assert list(df.columns) == ["trade_date", "code"]
        assert df.empty

    def test_success_returns_true(self, monkeypatch):
        def stub(name, trade_date, fetch_fn, **kwargs):
            return pd.DataFrame({"trade_date": ["20260806"], "code": ["600000"]})

        monkeypatch.setattr("daily_review.pipeline._cached", stub)
        df, ok = _fetch_opt("zb_pool", "20260806", lambda: None)
        assert ok is True
        assert len(df) == 1


class TestCached:
    def test_use_cache_false_bypasses_existing_csv(self, tmp_path, monkeypatch):
        # 当日收盘后（use_cache=False）必须忽略已有缓存、强制重取（作废盘中快照）
        class _Settings:
            cache_enabled = True

        monkeypatch.setattr("daily_review.pipeline.get_settings", lambda: _Settings())
        cache_file = tmp_path / "zt_pool_20260810.csv"
        cache_file.write_text("trade_date,code\n20260810,2428\n", encoding="utf-8")
        monkeypatch.setattr(
            "daily_review.pipeline.load_csv",
            lambda name, d: pd.read_csv(cache_file, dtype={"code": str}),
        )
        monkeypatch.setattr(
            "daily_review.pipeline.save_csv",
            lambda df, name, d: df.to_csv(cache_file, index=False),
        )
        fetched = []

        def fetch():
            fetched.append(1)
            return pd.DataFrame({"trade_date": ["20260810"], "code": ["600000"]})

        # use_cache=True → 命中已有缓存，fetch 不调用
        df1 = _cached("zt_pool", "20260810", fetch, use_cache=True)
        assert len(fetched) == 0
        assert df1["code"].tolist() == ["002428"]  # 缓存读回做了零填充

        # use_cache=False → 跳过缓存，fetch 调用一次（收盘后重取完整数据）
        df2 = _cached("zt_pool", "20260810", fetch, use_cache=False)
        assert len(fetched) == 1
        assert df2["code"].tolist() == ["600000"]


# ---------------------------------------------------------------- 归属校验（v0.23 A3）


class TestOwnsTradeDate:
    def test_matching_rows_ok(self):
        df = pd.DataFrame({"trade_date": ["20260806", "20260806"]})
        assert _owns_trade_date(df, "20260806") is True

    def test_mismatch_rows_rejected(self):
        df = pd.DataFrame({"trade_date": ["20260805", "20260805"]})
        assert _owns_trade_date(df, "20260806") is False

    def test_partial_mismatch_rejected(self):
        df = pd.DataFrame({"trade_date": ["20260806", "20260805"]})
        assert _owns_trade_date(df, "20260806") is False

    def test_empty_df_ok(self):
        assert _owns_trade_date(pd.DataFrame(), "20260806") is True

    def test_no_trade_date_column_ok(self):
        df = pd.DataFrame({"code": ["600000"]})
        assert _owns_trade_date(df, "20260806") is True


class TestCachedOwnership:
    def test_mismatched_cache_discarded_and_refetched(self, tmp_path, monkeypatch):
        """缓存文件内容日期 != 请求日期 → _cached 丢弃坏缓存，走 fetch_fn 重拉。"""
        import daily_review.pipeline as pl
        from daily_review.config import Settings

        settings = Settings(data_dir=tmp_path)
        monkeypatch.setattr("daily_review.pipeline.get_settings", lambda: settings)
        pl.save_csv(
            pd.DataFrame({"trade_date": ["20260805"], "code": ["600000"]}),
            "zt_pool", "20260806",
        )  # 预置归属不符的坏缓存
        fetched: list[int] = []

        def fetch():
            fetched.append(1)
            return pd.DataFrame({"trade_date": ["20260806"], "code": ["600000"]})

        out = _cached("zt_pool", "20260806", fetch)
        assert len(fetched) == 1, "坏缓存被丢弃，必须重新采集"
        assert out["trade_date"].tolist() == ["20260806"]

    def test_matching_cache_returned_without_fetch(self, tmp_path, monkeypatch):
        import daily_review.pipeline as pl
        from daily_review.config import Settings

        settings = Settings(data_dir=tmp_path)
        monkeypatch.setattr("daily_review.pipeline.get_settings", lambda: settings)
        pl.save_csv(
            pd.DataFrame({"trade_date": ["20260806"], "code": ["600000"]}),
            "zt_pool", "20260806",
        )
        fetched: list[int] = []

        def fetch():
            fetched.append(1)
            return pd.DataFrame()

        out = _cached("zt_pool", "20260806", fetch)
        assert fetched == [], "正确归属的缓存应直接命中，不触发采集"
        assert len(out) == 1


class TestRepoSaveCsvAtomic:
    """repo.save_csv 原子写：先写同目录临时文件再 os.replace——Web 并发 collect 不产生 torn CSV。"""

    def _settings(self, tmp_path, monkeypatch):
        class _Settings:
            data_dir = tmp_path / "data"

        monkeypatch.setattr("daily_review.data.repo.get_settings", lambda: _Settings())

    def test_writes_readable_csv_no_tmp_left(self, tmp_path, monkeypatch):
        from daily_review.data import repo

        self._settings(tmp_path, monkeypatch)
        df = pd.DataFrame({"trade_date": ["20260806"], "code": ["600000"], "up_pct": [9.87]})
        path = repo.save_csv(df, "zt_pool", "20260806")
        assert path.name == "zt_pool.csv"
        back = pd.read_csv(path, dtype={"code": str})
        assert back["code"].tolist() == ["600000"]
        assert float(back["up_pct"].iloc[0]) == 9.87
        # 无残留临时文件
        assert [p.name for p in path.parent.iterdir()] == ["zt_pool.csv"]

    def test_repeated_writes_overwrite_cleanly(self, tmp_path, monkeypatch):
        from daily_review.data import repo

        self._settings(tmp_path, monkeypatch)
        for i in range(3):
            repo.save_csv(pd.DataFrame({"code": [f"{i:06d}"]}), "zb_pool", "20260806")
        back = pd.read_csv(tmp_path / "data" / "20260806" / "zb_pool.csv", dtype={"code": str})
        assert back["code"].tolist() == ["000002"]


def _pool_df(date: str, codes: list[str], *, lb: int = 1) -> pd.DataFrame:
    return pd.DataFrame({
        "trade_date": [date] * len(codes),
        "code": codes,
        "name": [f"N{c}" for c in codes],
        "lb_num": [lb] * len(codes),
        "first_limit_time": ["09:30:00"] * len(codes),
        "open_times": [0] * len(codes),
        "seal_amount": [1e8] * len(codes),
        "industry": ["电子"] * len(codes),
    })


class TestCollectDashboard:
    def test_skips_moneyflow_lhb_concept(self, monkeypatch):
        """轻量采集不得触发资金流 / 龙虎榜 / 概念 / 行业映射。"""
        import daily_review.pipeline as pipe
        from daily_review.pipeline import collect_dashboard

        calls: list[str] = []

        def track(name):
            def _inner(*a, **k):
                calls.append(name)
                raise AssertionError(f"看板轻量路径不应调用 {name}")
            return _inner

        monkeypatch.setattr(pipe.em, "fetch_moneyflow", track("fetch_moneyflow"))
        monkeypatch.setattr(pipe.em, "fetch_stock_industry_map", track("fetch_stock_industry_map"))
        monkeypatch.setattr(pipe.em, "fetch_concept_boards", track("fetch_concept_boards"))
        monkeypatch.setattr(pipe.eastmoney_lhb, "fetch_lhb_daily", track("fetch_lhb_daily"))
        monkeypatch.setattr(pipe.eastmoney_lhb, "fetch_lhb_seats", track("fetch_lhb_seats"))

        monkeypatch.setattr(pipe.em, "resolve_recent_trade_dates",
                            lambda start, n_days=5: ["20260806", "20260805", "20260804"][:n_days])

        def fake_cached(name, trade_date, fetch_fn, *, use_cache=True):
            if name == "zt_pool":
                return _pool_df(trade_date, ["000001"], lb=3 if trade_date == "20260806" else 2)
            return pd.DataFrame(columns=["trade_date", "code"])

        def fake_fetch_opt(name, trade_date, fetch_fn, *, use_cache=True):
            if name == "zb_pool":
                return _pool_df(trade_date, ["600001"]), True
            return _pool_df(trade_date, []), True

        monkeypatch.setattr(pipe, "_cached", fake_cached)
        monkeypatch.setattr(pipe, "_fetch_opt", fake_fetch_opt)

        out = collect_dashboard("20260806", n_days=3)
        assert calls == []
        assert out["trade_date"] == "20260806"
        assert len(out["zt"]) == 1
        assert [h["date"] for h in out["hist_days"]] == ["20260804", "20260805"]  # 旧→新
        assert "moneyflow" not in out
        assert "lhb_daily" not in out
        assert "concept_map" not in out


class TestComputeDashboard:
    def test_emotion_and_kpi_drive_trend(self, monkeypatch):
        from daily_review.dashboard import _assemble_payload, build_trend, render_html
        from daily_review.pipeline import compute_dashboard

        collected = {
            "trade_date": "20260806",
            "zt": _pool_df("20260806", ["000001", "000002"], lb=5).assign(
                lb_num=[5, 1]
            ),
            "zb": _pool_df("20260806", ["600001"]),
            "dt": _pool_df("20260806", []),
            "hist_days": [
                {
                    "date": "20260805",
                    "zt": _pool_df("20260805", ["000001"], lb=4),
                    "zb": _pool_df("20260805", []),
                    "dt": _pool_df("20260805", []),
                    "zb_ok": True, "dt_ok": True,
                },
            ],
            "zb_ok": True,
            "dt_ok": True,
            "is_intraday": False,
            "timeline_dates": ["20260806", "20260805"],
        }
        indicators = compute_dashboard(collected)
        assert "emotion" in indicators
        assert indicators["ladder"]["zt_count"] == 2
        assert indicators["ladder"]["lianban_count"] == 1
        assert indicators["ladder"]["max_lb"] == 5
        assert "themes" not in indicators
        assert "break" not in indicators
        assert "lhb" not in indicators

        trend = build_trend(collected, indicators, n_days=2)
        payload = _assemble_payload(indicators, trend, collected)
        html = render_html(payload)
        assert "const DATA =" in html
        assert "数据看板" in html
