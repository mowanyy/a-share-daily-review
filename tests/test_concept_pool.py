"""web/concept_pool.py 测试：CSV 公式注入转义（v0.36.3 安全加固）。"""

from __future__ import annotations

import pandas as pd


def _isolate(tmp_path, monkeypatch):
    """把概念池目录指向临时目录，避免污染真实 data/stock_pool。"""
    from daily_review.web import concept_pool as cp
    from daily_review.config import Settings

    settings = Settings(stock_pool_dir=tmp_path / "data" / "stock_pool")
    monkeypatch.setattr(cp, "get_settings", lambda: settings)
    return cp


def test_add_stocks_escapes_formula_cells(tmp_path, monkeypatch):
    """name/note 以 = + - @ 开头 → 前置单引号防 Excel 公式执行；正常值不动。"""
    cp = _isolate(tmp_path, monkeypatch)
    cp.create_pool("测试池")
    result = cp.add_stocks(
        "测试池",
        [
            {"code": "600001", "name": '=HYPERLINK("http://evil.com")', "note": "+SUM(A1:A9)"},
            {"code": "600002", "name": "-2+3", "note": "@cmd"},
            {"code": "600003", "name": "正常股", "note": "正常备注"},
        ],
    )
    assert result["added"] == 3

    df = pd.read_csv(cp.pool_dir() / "测试池.csv", dtype=str)
    rows = {r["code"]: r for _, r in df.iterrows()}
    assert rows["600001"]["name"].startswith("'=")
    assert rows["600001"]["note"].startswith("'+")
    assert rows["600002"]["name"].startswith("'-")
    assert rows["600002"]["note"].startswith("'@")
    assert rows["600003"]["name"] == "正常股"
    assert rows["600003"]["note"] == "正常备注"


def test_csv_safe_unit():
    """_csv_safe 单测：危险前缀加引号，制表符/回车也拦截，空/正常值原样。"""
    from daily_review.web.concept_pool import _csv_safe

    assert _csv_safe("=1+1") == "'=1+1"
    assert _csv_safe("+1") == "'+1"
    assert _csv_safe("-1") == "'-1"
    assert _csv_safe("@x") == "'@x"
    assert _csv_safe("\t9") == "'\t9"
    assert _csv_safe("正常") == "正常"
    assert _csv_safe("") == ""
    assert _csv_safe(None) == ""
    assert _csv_safe("'=已转义") == "'=已转义"  # 已加引号的不重复加
