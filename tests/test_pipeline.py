"""管道层离线测试：缓存读回时 code 列零填充；_fetch_opt 容错（成功标志）。"""

from __future__ import annotations

import pandas as pd

from daily_review.pipeline import _cached, _fetch_opt, _zfill_codes


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
