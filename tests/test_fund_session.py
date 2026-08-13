"""web/fund_agent.py 会话 + 中军测试（v0.19，全离线，mock 数据/LLM/网络）。"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from daily_review.llm.client import LLMError

MANAGER = "fundstyle-deep-value-zhangkun"

ZT_DF = pd.DataFrame({
    "code": ["600519", "000858", "600036", "601318", "000001"],
    "name": ["贵州茅台", "五粮液", "招商银行", "中国平安", "平安银行"],
    "industry": ["白酒", "白酒", "银行", "保险", "银行"],
    "lb_num": [1, 1, 2, 1, 1],
    "first_limit_time": ["093000", "093500", "094000", "095000", "095500"],
})

MARKET_CAPS = {
    "600519": 2_000_000_000_000, "000858": 800_000_000_000,
    "600036": 900_000_000_000, "601318": 700_000_000_000,
    "000001": 200_000_000_000,
}

_KLINE_DF = pd.DataFrame({
    "trade_date": ["2026-08-04", "2026-08-11"],
    "open": [100.0, 105.0], "close": [104.0, 108.0],
    "high": [106.0, 110.0], "low": [98.0, 103.0],
    "volume": [10000, 12000], "pct_change": [4.0, 3.8],
})


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """注入 tmp 目录为 data/prompts 根，返回 tmp_path。"""
    from daily_review.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "data_dir", tmp_path / "data")
    monkeypatch.setattr(s, "prompts_dir", tmp_path / "prompts")
    (tmp_path / "prompts" / "strategies").mkdir(parents=True, exist_ok=True)
    (tmp_path / "prompts" / "strategies" / "战法模板.md").write_text(
        "---\nid: strategy.template\nname: 战法模板\nrole: strategy\nstatus: draft\n---\n\n## 1\n模板",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def app(setup, request):
    """Flask 测试客户端（依赖 setup 已设置 data_dir）。"""
    from daily_review.web.app import create_app

    a = create_app()
    a.config["TESTING"] = True
    return a


def _chat_result(answer: str):
    """构造一个无工具调用的 ChatResult。"""
    from daily_review.llm.client import ChatResult

    return ChatResult(content=answer, tool_calls=[], finish_reason="stop", reasoning_content=None, raw_tool_calls=None)


def _patch_chat(monkeypatch, answer: str = "按该风格，中军趋势偏多。"):
    """Mock fund_agent.chat_tools（v0.20：analyze 改用 chat_tools）。"""
    import daily_review.web.fund_agent as fa

    monkeypatch.setattr(fa, "chat_tools", lambda messages, **kw: _chat_result(answer))


def _patch_data(monkeypatch, *, zt_df=None, caps=None, kline_df=None):
    """mock repo.load_csv + eastmoney_pool.fetch_market_caps + eastmoney.fetch_kline。"""
    from daily_review.data import repo, eastmoney_pool, eastmoney

    if zt_df is not None:
        monkeypatch.setattr(repo, "load_csv", lambda name, *a, **kw: zt_df)
    if caps is not None:
        monkeypatch.setattr(eastmoney_pool, "fetch_market_caps", lambda codes, **kw: caps)
    if kline_df is not None:
        monkeypatch.setattr(eastmoney, "fetch_kline", lambda code, **kw: kline_df)


# ---------------------------------------------------------------- 会话


def test_session_create_load_clear(setup):
    from daily_review.web.fund_agent import _load_session, _save_session, clear_session

    sid = "fundstyle-test"
    s = _load_session(sid)
    assert s["manager_id"] == sid and s["messages"] == []
    s["messages"].append({"role": "user", "content": "hi"})
    _save_session(s)
    path = setup / "data" / "fund_sessions" / f"{sid}.json"
    assert path.exists()
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["messages"][0]["content"] == "hi"
    clear_session(sid)
    assert not path.exists()


# ---------------------------------------------------------------- 中军识别


def test_zhongjun_identify(monkeypatch, setup):
    import daily_review.web.fund_agent as fa

    _patch_data(monkeypatch, zt_df=ZT_DF, caps=MARKET_CAPS)
    session = fa._load_session(MANAGER)
    result = fa._ensure_zhongjun(session, "20260813")
    assert len(result) >= 3  # 白酒/银行/保险 各一个
    # 白酒中军：600519（2万亿）> 000858（8000亿）
    baijiu = [z for z in result if z["theme_name"] == "白酒"]
    assert baijiu and baijiu[0]["code"] == "600519"
    # 银行中军：600036（9000亿）> 000001（2000亿）
    bank = [z for z in result if z["theme_name"] == "银行"]
    assert bank and bank[0]["code"] == "600036"
    # session 已保存
    assert session["meta"]["zhongjun_date"] == "20260813"


def test_zhongjun_no_csv(monkeypatch, setup):
    """无涨停池 CSV → 中军为空，不抛异常。"""
    import daily_review.web.fund_agent as fa
    from daily_review.data import repo

    def boom(name, *a, **kw):
        raise FileNotFoundError("no csv")
    monkeypatch.setattr(repo, "load_csv", boom)

    session = fa._load_session(MANAGER)
    result = fa._ensure_zhongjun(session, "20260813")
    assert result == []


def test_zhongjun_fetch_fail(monkeypatch, setup):
    """市值拉取网络失败 → 中军为空。"""
    import daily_review.web.fund_agent as fa
    from daily_review.data import repo, eastmoney_pool

    monkeypatch.setattr(repo, "load_csv", lambda name, *a, **kw: ZT_DF)
    monkeypatch.setattr(eastmoney_pool, "fetch_market_caps", lambda codes, **kw: (_ for _ in ()).throw(ValueError("网络断")))

    session = fa._load_session(MANAGER)
    result = fa._ensure_zhongjun(session, "20260813")
    assert result == []


def test_zhongjun_cached(monkeypatch, setup):
    """同一 trade_date 第二次调用不重复拉取。"""
    import daily_review.web.fund_agent as fa
    from daily_review.data import repo

    counter = [0]

    def fake_load(*a, **kw):
        counter[0] += 1
        return ZT_DF
    monkeypatch.setattr(repo, "load_csv", fake_load)
    _patch_data(monkeypatch, caps=MARKET_CAPS)

    session = fa._load_session(MANAGER)
    fa._ensure_zhongjun(session, "20260813")
    fa._ensure_zhongjun(session, "20260813")
    assert counter[0] == 1  # 第二次跳过


# ---------------------------------------------------------------- 上下文


def test_analyze_context(monkeypatch, setup):
    """多轮对话 → history 增长。"""
    import daily_review.web.fund_agent as fa

    _patch_chat(monkeypatch)
    _patch_data(monkeypatch, zt_df=ZT_DF, caps=MARKET_CAPS, kline_df=_KLINE_DF)

    r1 = fa.analyze(MANAGER, "中军分析", klt=102, trade_date="20260813")
    assert r1["history_length"] == 1
    assert r1["zhongjun"]  # 有中军

    r2 = fa.analyze(MANAGER, "再分析", klt=102, trade_date="20260813")
    assert r2["history_length"] == 2

    # 第二轮的 user 消息应该包含第一轮内容（在历史中）
    session = fa._load_session(MANAGER)
    assert len(session["messages"]) == 4  # 2 轮 × 2 条


def test_analyze_history_trim(monkeypatch, setup):
    """超过 10 轮后自动裁剪。"""
    import daily_review.web.fund_agent as fa

    _patch_chat(monkeypatch)
    _patch_data(monkeypatch, zt_df=ZT_DF, caps=MARKET_CAPS, kline_df=_KLINE_DF)

    for i in range(12):
        fa.analyze(MANAGER, f"第{i}轮", klt=102, trade_date="20260813")
    r = fa.analyze(MANAGER, "最后一轮", klt=102, trade_date="20260813")
    assert r["history_length"] == 10  # 裁剪后保留最近 10 轮
    session = fa._load_session(MANAGER)
    # 裁剪后保留最近 10 轮 = 20 条
    assert len(session["messages"]) == 20


def test_analyze_llm_error(monkeypatch, setup):
    """LLM 失败 → error 字段，历史保留。"""
    import daily_review.web.fund_agent as fa
    from daily_review.llm.client import LLMError

    _patch_data(monkeypatch, zt_df=ZT_DF, caps=MARKET_CAPS, kline_df=_KLINE_DF)
    monkeypatch.setattr(fa, "chat_tools", lambda *a, **kw: (_ for _ in ()).throw(LLMError("限流")))

    r = fa.analyze(MANAGER, "分析 600519", klt=102, trade_date="20260813")
    assert r["error"]
    assert "LLM 调用失败" in r["answer"]


# ---------------------------------------------------------------- API


def test_api_analyze_zhongjun(monkeypatch, setup):
    """POST analyze 返回 history_length 和 zhongjun。"""
    import daily_review.web.fund_agent as fa
    from daily_review.llm import client as llm_client

    _patch_data(monkeypatch, zt_df=ZT_DF, caps=MARKET_CAPS, kline_df=_KLINE_DF)
    _patch_chat(monkeypatch)

    from daily_review.web.app import create_app
    app = create_app()
    app.config["TESTING"] = True

    r = app.test_client().post("/api/fund/analyze", json={
        "manager_id": MANAGER, "question": "复盘中军", "klt": 102, "trade_date": "20260813"
    })
    assert r.status_code == 200
    d = r.get_json()
    assert "answer_html" in d
    assert d["history_length"] == 1
    assert len(d["zhongjun"]) >= 3


def test_api_clear_session(monkeypatch, setup):
    from daily_review.web.app import create_app

    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()

    # 先发一次分析 → 有 session
    _patch_data(monkeypatch, zt_df=ZT_DF, caps=MARKET_CAPS, kline_df=_KLINE_DF)
    _patch_chat(monkeypatch)
    c.post("/api/fund/analyze", json={"manager_id": MANAGER, "question": "x", "klt": 102, "trade_date": "20260813"})

    # 清空
    r = c.post(f"/api/fund/clear/{MANAGER}")
    assert r.status_code == 200
    assert r.get_json()["ok"]

    # 确认 session 为空
    r2 = c.get(f"/api/fund/session/{MANAGER}")
    assert r2.get_json()["history_length"] == 0


def test_api_session_info(monkeypatch, setup):
    from daily_review.web.app import create_app

    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()

    _patch_data(monkeypatch, zt_df=ZT_DF, caps=MARKET_CAPS, kline_df=_KLINE_DF)
    _patch_chat(monkeypatch)

    # 先分析一轮
    c.post("/api/fund/analyze", json={"manager_id": MANAGER, "question": "分析", "klt": 102, "trade_date": "20260813"})

    r = c.get(f"/api/fund/session/{MANAGER}")
    d = r.get_json()
    assert d["history_length"] == 1
    assert d["zhongjun"]
    assert "updated_at" in d