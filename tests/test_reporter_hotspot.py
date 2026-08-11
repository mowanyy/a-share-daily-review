"""reporter 热点简报（多模型协作）：模型 B 独立提炼 → 注入 一总览/四题材/七预案（离线，不发真实 LLM）。"""

from __future__ import annotations

from daily_review.llm.client import LLMError
from daily_review.llm.reporter import (
    _hotspot_brief,
    _hotspot_fallback_text,
    _hotspot_hint,
    _hotspot_payload,
    _plan_user,
    generate_report,
)
from daily_review.prompts import Prompt

from tests.test_reporter_payload import _indicators

CONCEPT_BOARDS = [
    {"board_name": "逆变器", "pct": 3.1, "main_net_inflow": 1.040439456e9,
     "leader_code": "605117", "leader_name": "德业股份", "leader_pct": 6.58},
    {"board_name": "线下药店", "pct": 5.44, "main_net_inflow": 3.28016192e8,
     "leader_code": "603883", "leader_name": "老百姓", "leader_pct": 9.97},
]

STRATEGY = Prompt(
    id="strategy.user-test",
    name="低吸战法",
    role="strategy",
    status="active",
    version="1.0.0",
    applies_to="修复期",
    body="## 1. 概述\n赚晋级预期差\n\n## 4. 买入规则\n竞价高开 3-6%",
)


def _indicators_with_hotspot():
    ind = _indicators()
    ind["concept_boards"] = CONCEPT_BOARDS
    return ind


def _fake_chat(record):
    def _chat(messages, **kw):
        record.append(messages)
        user = messages[-1].get("content", "")
        if "次日预案" in user:
            return "## 七、次日预案\n计划：做二进三。"
        return "章节内容\n"
    return _chat


# ---------------------------------------------------------------- 载荷与简报函数


class TestHotspotPayload:
    def test_shape(self):
        p = _hotspot_payload(_indicators_with_hotspot())
        assert p["概念板块涨幅榜"] == CONCEPT_BOARDS
        assert p["当日题材(已核算)"][0]["theme_name"] == "机器人"
        assert p["当日题材(已核算)"][0]["leader"] == "龙头"
        assert p["trade_date"] == "20260806"

    def test_no_concept_boards_empty(self):
        p = _hotspot_payload(_indicators())
        assert p["概念板块涨幅榜"] == []


class TestHotspotBrief:
    def test_calls_chat_with_default_model_none(self, monkeypatch):
        from daily_review.llm import reporter
        from daily_review.config import get_settings

        s = get_settings()
        monkeypatch.setattr(s, "hotspot_model", "")  # 空 → 回落主模型（model=None）
        rec = {}

        def fake_chat(messages, **kw):
            rec["kw"] = kw
            return "热点摘要\n"

        monkeypatch.setattr(reporter, "chat", fake_chat)
        assert _hotspot_brief(_indicators_with_hotspot(), "k") == "热点摘要"
        assert rec["kw"].get("model") is None
        assert rec["kw"]["max_tokens"] == 500

    def test_calls_chat_with_hotspot_model(self, monkeypatch):
        from daily_review.llm import reporter
        from daily_review.config import get_settings

        s = get_settings()
        monkeypatch.setattr(s, "hotspot_model", "deepseek-reasoner")
        rec = {}

        def fake_chat(messages, **kw):
            rec["kw"] = kw
            return "热点摘要\n"

        monkeypatch.setattr(reporter, "chat", fake_chat)
        _hotspot_brief(_indicators_with_hotspot(), "k")
        assert rec["kw"]["model"] == "deepseek-reasoner"

    def test_llm_error_returns_empty(self, monkeypatch):
        from daily_review.llm import reporter

        def boom(messages, **kw):
            raise LLMError("api down")

        monkeypatch.setattr(reporter, "chat", boom)
        assert _hotspot_brief(_indicators_with_hotspot(), "k") == ""

    def test_no_concept_boards_no_call(self, monkeypatch):
        from daily_review.llm import reporter

        called = {"n": 0}

        def fake_chat(messages, **kw):
            called["n"] += 1
            return "x"

        monkeypatch.setattr(reporter, "chat", fake_chat)
        assert _hotspot_brief(_indicators(), "k") == ""
        assert called["n"] == 0

    def test_no_prompt_returns_empty(self, monkeypatch):
        from daily_review.llm import reporter

        monkeypatch.setattr(reporter, "get_prompt", lambda pid: None)
        monkeypatch.setattr(reporter, "chat", lambda *a, **k: "x")
        assert _hotspot_brief(_indicators_with_hotspot(), "k") == ""


class TestHotspotFallback:
    def test_renders_topn(self):
        text = _hotspot_fallback_text(_indicators_with_hotspot())
        assert "当日概念板块涨幅靠前" in text
        assert "逆变器" in text and "德业股份" in text
        assert "涨幅" in text and "主力净流入" in text

    def test_empty_when_no_rows(self):
        assert _hotspot_fallback_text(_indicators()) == ""


class TestHotspotHint:
    def test_wording(self):
        hint = _hotspot_hint("简报正文", "LLM 提炼")
        assert "【另一模型提炼的当日热点（LLM 提炼）】" in hint
        assert "引用并校验" in hint and "不得编造" in hint
        assert "热点信息模型" in hint and "非凭空生成" in hint

    def test_program_source_wording_not_model_refined(self):
        """程序核算兜底：来源措辞自适应，不得误标为「热点信息模型提炼」。"""
        hint = _hotspot_hint("TopN 文本", "程序按概念板块核算")
        assert "【程序按概念板块核算的当日热点】" in hint
        assert "程序按概念板块涨幅/主力净流入核算" in hint
        assert "未经过模型提炼" in hint
        assert "热点信息模型" not in hint      # 兜底文本从未经过模型提炼
        assert "引用并校验" in hint and "不得编造" in hint


# ---------------------------------------------------------------- generate_report 注入


class TestGenerateReportInjection:
    def test_injects_into_three_chapters(self, tmp_path, monkeypatch):
        from daily_review.llm import reporter

        record = []
        monkeypatch.setattr(reporter, "chat", _fake_chat(record))
        generate_report(_indicators_with_hotspot(), "20260806",
                        api_key="k", out_path=tmp_path / "a.md")

        # 第一次调用必须是热点简报（模型 B）
        assert "请按「任务」提炼当日 2-4 条热点主线简报" in record[0][-1]["content"]
        # 一总览 / 四题材 / 七预案 三章节 user 都注入
        by_kind = {}
        for messages in record:
            user = messages[-1].get("content", "")
            if "一、总览" in user:
                by_kind.setdefault("overview", user)
            if "四、题材运行周期与归类" in user:
                by_kind.setdefault("theme", user)
            if "次日预案" in user:
                by_kind.setdefault("plan", user)
        assert "另一模型提炼的当日热点" in by_kind["overview"]
        assert "另一模型提炼的当日热点" in by_kind["theme"]
        assert "另一模型提炼的当日热点" in by_kind["plan"]
        # 情绪温度模块（非题材）不注入
        emo = [m for m in record if "输出「二、情绪温度」章节" in m[-1].get("content", "")]
        assert emo and "另一模型提炼的当日热点" not in emo[0][-1]["content"]

    def test_degrades_to_topn_on_hotspot_error(self, tmp_path, monkeypatch):
        from daily_review.llm import reporter

        calls = {"n": 0}
        record = []

        def chat(messages, **kw):
            record.append(messages)
            calls["n"] += 1
            if calls["n"] == 1:
                raise LLMError("hotspot down")  # 热点模型不可用
            user = messages[-1].get("content", "")
            if "次日预案" in user:
                return "## 七、次日预案\n计划：做二进三。"
            return "章节内容\n"

        monkeypatch.setattr(reporter, "chat", chat)
        md = generate_report(_indicators_with_hotspot(), "20260806",
                             api_key="k", out_path=tmp_path / "a.md")
        assert calls["n"] == 8  # 1 热点 + 5 模块 + 总览 + 预案
        # 报告仍完整七章
        assert "## 一、总览" in md and "## 七、次日预案" in md
        # 热点 LLM 失败 → 降级为确定性 Top-N 注入（标签改为「程序核算」，不再冒充模型提炼）
        injected = [
            m[-1]["content"] for m in record
            if ("一、总览" in m[-1]["content"] or "次日预案" in m[-1]["content"])
            and "当日热点" in m[-1]["content"]
        ]
        assert injected, "热点降级未注入到总览/预案"
        assert "程序按概念板块核算" in injected[0]
        assert "程序按概念板块核算的当日热点" in injected[0]
        assert "概念板块涨幅靠前" in injected[0]
        assert "热点信息模型" not in injected[0]   # 兜底文本不得误标为模型提炼

    def test_no_injection_when_no_concept_boards(self, tmp_path, monkeypatch):
        from daily_review.llm import reporter

        record = []
        monkeypatch.setattr(reporter, "chat", _fake_chat(record))
        generate_report(_indicators(), "20260806", api_key="k", out_path=tmp_path / "a.md")
        # 无 concept_boards → 不触发热点调用（调用数保持 7：5 模块 + 总览 + 预案）
        assert len(record) == 7
        for messages in record:
            user = messages[-1].get("content", "")
            assert "另一模型提炼的当日热点" not in user


class TestPlanUserHotspot:
    def test_keeps_strategy_instruction_with_hotspot(self):
        body = _plan_user("20260806", _indicators_with_hotspot(), STRATEGY,
                          emo_hint="", hotspot="热点块")
        assert "请按战法「低吸战法」的触发条件" in body
        assert "晋级/断板两情形" in body
        assert "热点块" in body

    def test_no_hotspot_preserves_existing(self):
        # hotspot="" 时与旧实现逐字节一致（不含热点措辞）
        body = _plan_user("20260806", _indicators(), None, emo_hint="")
        assert "热点" not in body
        assert "只输出正文，不要标题、不要编造数字。" in body
