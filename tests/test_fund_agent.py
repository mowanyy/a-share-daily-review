"""web/fund_agent.py 测试：基金经理分析 agent + Web API（全离线，mock LLM 与 K 线）。"""

from __future__ import annotations

import pandas as pd
import pytest

from daily_review.llm.client import LLMError

_KLINE_DF = pd.DataFrame(
    {
        "trade_date": ["2026-08-04", "2026-08-11"],
        "open": [100.0, 105.0],
        "close": [104.0, 108.0],
        "high": [106.0, 110.0],
        "low": [98.0, 103.0],
        "volume": [10000, 12000],
        "pct_change": [4.0, 3.8],
    }
)

MANAGER = "fundstyle-deep-value-zhangkun"


@pytest.fixture
def app():
    from daily_review.web.app import create_app

    app = create_app()
    app.config["TESTING"] = True
    return app


def _patch_llm(monkeypatch, answer: str = "按该风格，周K 趋势向上。"):
    from daily_review.llm import client as llm_client

    captured: dict = {}

    def fake_chat(messages, **kw):
        captured["messages"] = messages
        return answer

    monkeypatch.setattr(llm_client, "chat", fake_chat)
    return captured


def _patch_kline(monkeypatch, df=None, boom: Exception | None = None):
    from daily_review.data import eastmoney

    def fake_kline(code, **kw):
        if boom is not None:
            raise boom
        return df if df is not None else _KLINE_DF

    monkeypatch.setattr(eastmoney, "fetch_kline", fake_kline)


# ---------------------------------------------------------------- list_managers


def test_list_managers():
    from daily_review.web.fund_agent import list_managers

    mgrs = list_managers()
    assert len(mgrs) == 4
    ids = {m["id"] for m in mgrs}
    assert MANAGER in ids
    for m in mgrs:
        assert m["name"] and m["description"] and m["file"].endswith(".md")


def test_get_manager_unknown():
    from daily_review.web.fund_agent import get_manager

    assert get_manager("fundstyle-nope") is None


# ---------------------------------------------------------------- analyze


def test_analyze_injects_kline(monkeypatch):
    import daily_review.web.fund_agent as fa

    _patch_kline(monkeypatch)
    captured = _patch_llm(monkeypatch)

    result = fa.analyze(MANAGER, "分析 600519 的估值分位", klt=102)
    assert result["answer"] == "按该风格，周K 趋势向上。"
    assert result["error"] == ""
    system = captured["messages"][0]["content"]
    assert system.startswith("你是本系统『基金经理分析")
    assert "时间周期与触发时点" in system          # 档案正文注入
    assert "本次所看周期：周K（klt=102）" in system
    user = captured["messages"][-1]["content"]
    assert "600519" in user and "2026-08-11" in user  # 周K 数据注入
    assert any("600519" in n and "已注入" in n for n in result["data_notes"])


def test_analyze_monthly_kline(monkeypatch):
    import daily_review.web.fund_agent as fa

    _patch_kline(monkeypatch)
    captured = _patch_llm(monkeypatch)

    fa.analyze(MANAGER, "月初，用月K 看 000858", klt=103)
    assert "本次所看周期：月K（klt=103）" in captured["messages"][0]["content"]
    assert "（klt=103）" in captured["messages"][-1]["content"] or "月K" in captured["messages"][-1]["content"]


def test_analyze_kline_failure_notes_missing(monkeypatch):
    import daily_review.web.fund_agent as fa

    _patch_kline(monkeypatch, boom=RuntimeError("网络断"))
    captured = _patch_llm(monkeypatch)

    result = fa.analyze(MANAGER, "看 600519 周线", klt=102)
    assert any("600519" in n and "拉取失败" in n for n in result["data_notes"])
    assert "拉取失败" in captured["messages"][-1]["content"]  # agent 被告知数据不足


def test_analyze_no_code_skips_fetch(monkeypatch):
    import daily_review.web.fund_agent as fa

    from daily_review.data import eastmoney

    called: list[str] = []

    def fake_kline(code, **kw):
        called.append(code)
        return _KLINE_DF

    monkeypatch.setattr(eastmoney, "fetch_kline", fake_kline)
    captured = _patch_llm(monkeypatch)

    result = fa.analyze(MANAGER, "以深度价值风格看当前消费板块", klt=102)
    assert called == []                                   # 无代码不联网
    assert result["data_notes"] == []
    assert "未注入 K 线数据" in captured["messages"][-1]["content"]


def test_analyze_llm_error(monkeypatch):
    import daily_review.web.fund_agent as fa
    from daily_review.llm import client as llm_client

    def boom(*a, **kw):
        raise LLMError("限流")

    monkeypatch.setattr(llm_client, "chat", boom)
    result = fa.analyze(MANAGER, "600519 怎么样", klt=102)
    assert result["error"]                                  # 非空
    assert "LLM 调用失败" in result["answer"]


def test_analyze_unknown_manager(monkeypatch):
    import daily_review.web.fund_agent as fa
    from daily_review.web.fund_agent import ManagerNotFound

    with pytest.raises(ManagerNotFound):
        fa.analyze("fundstyle-nope", "600519", klt=102)


def test_analyze_invalid_klt():
    import daily_review.web.fund_agent as fa

    with pytest.raises(ValueError):
        fa.analyze(MANAGER, "600519", klt=101)  # 仅允许周/月K


def test_analyze_empty_question():
    import daily_review.web.fund_agent as fa

    with pytest.raises(ValueError):
        fa.analyze(MANAGER, "   ", klt=102)


# ---------------------------------------------------------------- Web API


def test_api_fund_managers(app):
    r = app.test_client().get("/api/fund/managers")
    assert r.status_code == 200
    assert len(r.get_json()["managers"]) == 4


def test_api_fund_analyze(app, monkeypatch):
    from daily_review.data import eastmoney
    from daily_review.llm import client as llm_client

    monkeypatch.setattr(eastmoney, "fetch_kline", lambda code, **kw: _KLINE_DF)
    monkeypatch.setattr(llm_client, "chat", lambda messages, **kw: "**结论**：周K 买入区间。")

    r = app.test_client().post(
        "/api/fund/analyze",
        json={"manager_id": MANAGER, "question": "600519 买点", "klt": 102},
    )
    assert r.status_code == 200
    d = r.get_json()
    assert "answer" in d and "answer_html" in d and "error" in d and "data_notes" in d
    assert "<strong>结论</strong>" in d["answer_html"]
    assert d["data_notes"] and "600519" in d["data_notes"][0]


def test_api_fund_analyze_400(app):
    c = app.test_client()
    assert c.post("/api/fund/analyze", json={"manager_id": "", "question": "x"}).status_code == 400
    assert c.post("/api/fund/analyze", json={"manager_id": MANAGER, "question": "  "}).status_code == 400
    assert (
        c.post("/api/fund/analyze", json={"manager_id": MANAGER, "question": "x", "klt": "abc"}).status_code
        == 400
    )


def test_api_fund_analyze_404(app):
    r = app.test_client().post(
        "/api/fund/analyze", json={"manager_id": "fundstyle-nope", "question": "600519", "klt": 102}
    )
    assert r.status_code == 404