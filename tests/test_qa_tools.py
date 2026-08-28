"""数据工具测试：schema 契约一致 / 分发 / 两轮工具循环 / 异常兜底 / memo。"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from daily_review.kb import qa as qa_mod
from daily_review.kb import tools as tools_mod
from daily_review.kb.qa import QASession
from daily_review.kb.tools import (
    TOOL_NAMES,
    DataToolContext,
    execute_tool,
    get_tool_schemas,
)
from daily_review.llm.client import ChatResult, ToolCall

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = PROJECT_ROOT / "prompts" / "tools" / "数据工具schema.md"


@pytest.fixture(autouse=True)
def _clear_tools_process_cache():
    """v0.35.5：进程级采集缓存池跨测试隔离（前/后清空，防 mock 结果残留污染）。"""
    tools_mod.clear_process_cache()
    yield
    tools_mod.clear_process_cache()


def _tc(name, args_str, cid="call_1"):
    raw = {
        "id": cid,
        "type": "function",
        "function": {"name": name, "arguments": args_str},
    }
    return ToolCall(id=cid, name=name, arguments=_js(args_str), raw=raw)


def _js(s):
    import json

    return json.loads(s)


def test_schemas_match_schema_doc_contract():
    """get_tool_schemas 的 function name 必须与 schema.md 契约一一对应（防漂移）。"""
    text = SCHEMA_PATH.read_text(encoding="utf-8")
    doc_names = re.findall(r"### \d+\. `(\w+)`", text)
    assert len(doc_names) == 13
    code_names = [s["function"]["name"] for s in get_tool_schemas()]
    assert code_names == doc_names
    assert code_names == TOOL_NAMES


def test_schemas_well_formed():
    for s in get_tool_schemas():
        fn = s["function"]
        assert fn["name"] in TOOL_NAMES
        assert fn["parameters"]["type"] == "object"
        assert fn["description"]


def test_execute_tool_unknown():
    ctx = DataToolContext(default_date="20260806")
    out, ms = execute_tool("not_a_tool", {}, ctx)
    assert "error" in out and "未知工具" in out
    assert ms > 0  # 耗时 > 0ms


def test_execute_tool_validation_without_network():
    """参数缺失在采集前被拦截，不触发网络。"""
    ctx = DataToolContext(default_date="20260806")
    out1, _ = execute_tool("query_moneyflow", {}, ctx)
    assert "error" in out1
    out2, _ = execute_tool("query_themes_timeline", {}, ctx)
    assert "error" in out2


def test_default_trade_date_probe(monkeypatch):
    monkeypatch.setattr(tools_mod, "default_trade_date", lambda: "20260806")
    ctx = DataToolContext()
    assert ctx.default_date == "20260806"


def test_collect_memoized(monkeypatch):
    calls = {"n": 0}

    def fake_collect(date):
        calls["n"] += 1
        return {"trade_date": date}

    monkeypatch.setattr(tools_mod, "collect", fake_collect)
    ctx = DataToolContext(default_date="20260806")
    ctx.collected("20260806")
    ctx.collected("20260806")
    assert calls["n"] == 1


def test_two_round_tool_loop(monkeypatch, index):
    """round1 请求工具 → 回放 tool_calls + role=tool → round2 返回文本。"""
    rounds = {"n": 0}
    tool_payload = '{"trade_date": "20260806", "zt_count": 20, "lianban_count": 5}'

    def fake_execute(name, args, ctx):
        return (tool_payload, 1.5)  # v0.35：返回 (json_str, duration_ms)

    monkeypatch.setattr(qa_mod, "execute_tool", fake_execute)

    def fake_chat_tools(messages, **k):
        rounds["n"] += 1
        if rounds["n"] == 1:
            return ChatResult(
                content="",
                tool_calls=[_tc("query_ladder_stats", '{"trade_date": "20260806"}')],
                finish_reason="tool_calls",
                raw_tool_calls=[_tc("query_ladder_stats", '{"trade_date": "20260806"}').raw],
            )
        # 第二轮：tool 结果已注入、assistant tool_calls 已回放
        assert messages[-1]["role"] == "tool"
        assert messages[-1]["tool_call_id"] == "call_1"
        assert messages[-1]["content"] == tool_payload
        assert any(m["role"] == "assistant" and m.get("tool_calls") for m in messages)
        return ChatResult(content="今日涨停 20 家，连板 5 家")

    monkeypatch.setattr(qa_mod, "chat_tools", fake_chat_tools)
    session = QASession(index, use_embedding=False)
    result = session.answer("今天连板统计如何？")
    assert result.tool_rounds == 2
    assert "涨停 20 家" in result.answer


def test_tool_loop_stops_at_max_rounds(monkeypatch, index):
    def fake_execute(name, args, ctx):
        return ('{"ok": 1}', 0.5)  # v0.35：返回 (json_str, duration_ms)

    monkeypatch.setattr(qa_mod, "execute_tool", fake_execute)

    def fake_chat_tools(messages, **k):
        tc = _tc("query_ladder_stats", "{}")
        return ChatResult(
            content="", tool_calls=[tc], finish_reason="tool_calls", raw_tool_calls=[tc.raw]
        )

    monkeypatch.setattr(qa_mod, "chat_tools", fake_chat_tools)
    session = QASession(index, use_embedding=False)
    result = session.answer("循环问题")
    assert result.tool_rounds == qa_mod.MAX_TOOL_ROUNDS
    assert "上限" in result.answer


# ---------- 进程级采集缓存池回归（v0.35.5） ----------


def test_process_cache_reuse_across_instances(monkeypatch):
    """不同 DataToolContext 实例（模拟多条消息）同一日期只采集一次。"""
    calls = {"n": 0}

    def fake_collect(date):
        calls["n"] += 1
        return {"trade_date": date, "zt": ["a", "b"]}

    monkeypatch.setattr(tools_mod, "collect", fake_collect)
    ctx1 = DataToolContext(default_date="20260806")
    ctx2 = DataToolContext(default_date="20260806")  # 另一次问答的新 ctx
    ctx1.collected("20260806")
    ctx2.collected("20260806")
    ctx1.collected("20260806")
    assert calls["n"] == 1, "同日期跨实例应只采集一次"


def test_process_cache_indicators_shared(monkeypatch):
    """indicators 也走共享池：跨实例同日期 compute 只跑一次。"""
    collect_calls = {"n": 0}
    compute_calls = {"n": 0}

    def fake_collect(date):
        collect_calls["n"] += 1
        return {"trade_date": date}

    def fake_compute(collected):
        compute_calls["n"] += 1
        return {"ladder": {"zt_count": 42}}

    monkeypatch.setattr(tools_mod, "collect", fake_collect)
    monkeypatch.setattr(tools_mod, "compute", fake_compute)
    ctx1 = DataToolContext(default_date="20260806")
    ctx2 = DataToolContext(default_date="20260806")
    assert ctx1.indicators("20260806")["ladder"]["zt_count"] == 42
    assert ctx2.indicators("20260806")["ladder"]["zt_count"] == 42
    assert collect_calls["n"] == 1
    assert compute_calls["n"] == 1


def test_process_cache_today_ttl_expiry(monkeypatch):
    """当日数据 TTL 过期后应重新采集（历史日期永久不过期）。"""
    calls = {"n": 0}

    def fake_collect(date):
        calls["n"] += 1
        return {"trade_date": date}

    monkeypatch.setattr(tools_mod, "collect", fake_collect)
    today = tools_mod._today_str()
    ctx = DataToolContext(default_date=today)
    ctx.collected(today)
    # 模拟缓存已过期：把时间戳改旧
    with tools_mod._POOL_LOCK:
        ts, data = tools_mod._COLLECT_CACHE[today]
        tools_mod._COLLECT_CACHE[today] = (ts - tools_mod._TODAY_TTL - 1, data)
    ctx.collected(today)
    assert calls["n"] == 2, "当日 TTL 过期应重新采集"


def test_process_cache_history_never_expires(monkeypatch):
    """历史日期（<今天）定稿数据不受 TTL 影响：过期时间戳仍命中。"""
    calls = {"n": 0}

    def fake_collect(date):
        calls["n"] += 1
        return {"trade_date": date}

    monkeypatch.setattr(tools_mod, "collect", fake_collect)
    hist = "20260806"
    ctx = DataToolContext(default_date=hist)
    ctx.collected(hist)
    with tools_mod._POOL_LOCK:
        ts, data = tools_mod._COLLECT_CACHE[hist]
        tools_mod._COLLECT_CACHE[hist] = (ts - 10 * 24 * 3600, data)  # 改旧 10 天
    ctx.collected(hist)
    assert calls["n"] == 1, "历史日期永久复用，不应重新采集"


def test_clear_process_cache_resets_pool(monkeypatch):
    """clear_process_cache 后同日期应重新采集。"""
    calls = {"n": 0}

    def fake_collect(date):
        calls["n"] += 1
        return {"trade_date": date}

    monkeypatch.setattr(tools_mod, "collect", fake_collect)
    ctx = DataToolContext(default_date="20260806")
    ctx.collected("20260806")
    tools_mod.clear_process_cache()
    ctx.collected("20260806")
    assert calls["n"] == 2
