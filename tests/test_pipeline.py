"""管道层离线测试：缓存读回时 code 列零填充；_fetch_opt 容错（成功标志）。"""

from __future__ import annotations

import pandas as pd

from daily_review.pipeline import _fetch_opt, _zfill_codes


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
        def boom(name, trade_date, fetch_fn):
            raise RuntimeError("network down")

        monkeypatch.setattr("daily_review.pipeline._cached", boom)
        df, ok = _fetch_opt("zb_pool", "20260806", lambda: None)
        assert ok is False
        assert list(df.columns) == ["trade_date", "code"]
        assert df.empty

    def test_success_returns_true(self, monkeypatch):
        def stub(name, trade_date, fetch_fn):
            return pd.DataFrame({"trade_date": ["20260806"], "code": ["600000"]})

        monkeypatch.setattr("daily_review.pipeline._cached", stub)
        df, ok = _fetch_opt("zb_pool", "20260806", lambda: None)
        assert ok is True
        assert len(df) == 1
