"""premarket（隔夜预案/开盘策略）LLM 调用预算回归测试（离线，mock chat）。

锁定两个调用点的 max_tokens ≥ 4000：推理模型（deepseek-v4-flash）思考先占预算，
预算不足时 content 为空 → 「模型把 max_tokens 全部用于思考」错误（v0.12.1 已修
热点/总览/看板/预案 4 点，v0.20.2 补修隔夜预案/开盘策略 2 点）。
"""

from __future__ import annotations

import json

from daily_review.llm.client import LLMError


def _patch_chat(monkeypatch, answer: str = "消息面汇总：中性偏多。"):
    """Mock premarket.chat，记录每次调用的 max_tokens。"""
    import daily_review.llm.premarket as pm

    calls: list[dict] = []

    def _fake_chat(messages, **kw):
        calls.append({"max_tokens": kw.get("max_tokens"), "messages": messages})
        return answer

    monkeypatch.setattr(pm, "chat", _fake_chat)
    return calls


def _minimal_indicators() -> dict:
    return {
        "ladder": {"trade_date": "20260814", "zt_count": 50, "lianban_count": 12,
                   "max_lb": 5, "max_lb_stock": "600001 测试", "break_rate": 0.2},
        "emotion": {"score": 60, "stage": "修复期", "stage_reason": "涨停增加", "available": True},
        "themes": [],
    }


def test_overnight_plan_max_tokens_budget(monkeypatch, tmp_path):
    """隔夜预案 LLM 调用 max_tokens ≥ 4000（推理模型思考预留）。"""
    from daily_review.llm.premarket import generate_overnight_plan

    calls = _patch_chat(monkeypatch)
    news = [{"title": "t", "content": "c", "show_time": "2026-08-17 08:00:00", "source": "东财"}]
    out = tmp_path / "plan.md"
    md = generate_overnight_plan(_minimal_indicators(), news, "20260817", out_path=out)
    assert out.exists() and "隔夜预案" in md
    assert calls, "chat 未被调用"
    assert calls[0]["max_tokens"] >= 4000, f"max_tokens={calls[0]['max_tokens']}，推理模型思考会占满预算"


def test_open_strategy_max_tokens_budget(monkeypatch, tmp_path):
    """开盘策略 LLM 调用 max_tokens ≥ 4000（推理模型思考预留）。"""
    from daily_review.llm.premarket import generate_open_strategy

    calls = _patch_chat(monkeypatch)
    auction = [{"code": "600001", "name": "测试", "auction_pct": 3.2, "auction_ratio": 2.1}]
    out = tmp_path / "open.md"
    md = generate_open_strategy(_minimal_indicators(), auction, "预案全文", "20260817", out_path=out)
    assert out.exists() and "开盘策略" in md
    assert calls, "chat 未被调用"
    assert calls[0]["max_tokens"] >= 4000, f"max_tokens={calls[0]['max_tokens']}，推理模型思考会占满预算"


def test_overnight_plan_llm_failure_falls_back(monkeypatch, tmp_path):
    """LLM 失败 → 兜底列出原始快讯，文件仍落盘。"""
    import daily_review.llm.premarket as pm
    from daily_review.llm.premarket import generate_overnight_plan

    def _boom(messages, **kw):
        raise LLMError("模拟限流")

    monkeypatch.setattr(pm, "chat", _boom)
    news = [
        {"title": "标题A", "content": "内容A", "show_time": "2026-08-17 08:00:00", "source": "东财"},
        {"title": "标题B", "content": "内容B", "show_time": "2026-08-17 07:00:00", "source": "东财"},
    ]
    out = tmp_path / "plan.md"
    md = generate_overnight_plan(_minimal_indicators(), news, "20260817", out_path=out)
    assert "LLM 生成失败" in md
    assert "标题A" in md and "标题B" in md  # 兜底含原始快讯
    assert out.exists()


# ---------------------------------------------------------------- digest 新鲜度（v0.24 B1）


class TestDigestFreshness:
    """隔夜预案/开盘策略 digest 含数据可用性元信息。"""

    def test_overnight_has_freshness(self):
        from daily_review.llm.premarket import _overnight_digest

        d = _overnight_digest(_minimal_indicators())
        assert "数据可用性" in d
        assert d["数据可用性"]["涨停梯队"] is True
        assert d["数据可用性"]["情绪温度"] is True

    def test_open_strategy_has_freshness(self):
        from daily_review.llm.premarket import _open_strategy_digest

        d = _open_strategy_digest(_minimal_indicators())
        assert "数据可用性" in d
        assert d["数据可用性"]["涨停梯队"] is True
