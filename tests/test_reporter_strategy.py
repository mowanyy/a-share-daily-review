"""reporter 战法注入测试：strategy 给定 / 缺省两种路径 + LLM 兜底 + 截断。"""

from __future__ import annotations

from daily_review.llm.client import LLMError
from daily_review.llm.reporter import _cap_text, generate_report
from daily_review.prompts import Prompt

from tests.test_reporter_payload import _indicators

STRATEGY = Prompt(
    id="strategy.user-test",
    name="低吸战法",
    role="strategy",
    status="active",
    version="1.0.0",
    applies_to="修复期",
    body="## 1. 概述\n赚晋级预期差\n\n## 4. 买入规则\n竞价高开 3-6%",
)


def _fake_chat(record):
    def _chat(messages, **kw):
        record.append(messages)
        user = messages[-1].get("content", "")
        if "次日预案" in user:
            return "## 七、次日预案\n计划：做二进三。"
        return "章节内容\n"
    return _chat


def test_strategy_none_backward_compatible(tmp_path, monkeypatch):
    """缺省 strategy → plan system 不含战法正文/名称，与原行为一致。"""
    from daily_review.llm import reporter

    record = []
    monkeypatch.setattr(reporter, "chat", _fake_chat(record))
    md = generate_report(
        _indicators(), "20260806", api_key="k", out_path=tmp_path / "a.md"
    )
    plan_messages = record[-1]
    sys_content = plan_messages[0]["content"]
    assert "低吸战法" not in sys_content
    assert "已指定战法" not in sys_content
    assert "通用预案" in sys_content
    assert "## 七、次日预案" in md


def test_strategy_injected_into_plan(tmp_path, monkeypatch):
    from daily_review.llm import reporter

    record = []
    monkeypatch.setattr(reporter, "chat", _fake_chat(record))
    md = generate_report(
        _indicators(), "20260806", api_key="k", out_path=tmp_path / "a.md", strategy=STRATEGY
    )
    plan_messages = record[-1]
    sys_content = plan_messages[0]["content"]
    assert "低吸战法" in sys_content
    assert STRATEGY.body in sys_content          # 战法正文注入 system
    assert "只执行该战法" in sys_content          # plan_rule
    assert "【用户指定的战法" in sys_content
    user_content = plan_messages[1]["content"]
    assert "请按战法「低吸战法」的触发条件" in user_content
    assert "晋级/断板两情形" in user_content
    assert "## 七、次日预案" in md


def test_non_strategy_role_ignored(tmp_path, monkeypatch):
    from daily_review.llm import reporter

    record = []
    monkeypatch.setattr(reporter, "chat", _fake_chat(record))
    bad = Prompt(id="strategy.x", name="非战法", role="qa", status="draft", body="xxx")
    generate_report(_indicators(), "20260806", api_key="k", out_path=tmp_path / "a.md", strategy=bad)
    plan_messages = record[-1]
    assert "非战法" not in plan_messages[0]["content"]
    assert "通用预案" in plan_messages[0]["content"]


def test_llm_error_fallback_keeps_strategy_rule(tmp_path, monkeypatch):
    from daily_review.llm import reporter

    def boom(messages, **kw):
        raise LLMError("api down")

    monkeypatch.setattr(reporter, "chat", boom)
    md = generate_report(
        _indicators(), "20260806", api_key="k", out_path=tmp_path / "a.md", strategy=STRATEGY
    )
    assert "预案生成失败" in md
    assert "只执行该战法" in md   # 兜底文本含 strategy 版 plan_rule


def test_cap_text_truncates():
    long = "x" * 100
    capped = _cap_text(long, limit=50)
    assert len(capped) < 100
    assert "截断" in capped
    assert _cap_text("short") == "short"
