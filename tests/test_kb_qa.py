"""问答会话测试：system prompt / 消息组装 / LLMError 降级 / history 生效。"""

from __future__ import annotations

import pytest

from daily_review.kb import qa as qa_mod
from daily_review.kb.qa import QASession, build_system_prompt, render_sources
from daily_review.llm.client import LLMError


def test_build_system_prompt_has_rag_rules():
    sp = build_system_prompt()
    assert "知识库（RAG）" in sp or "知识片段" in sp
    assert "绝不编造" in sp


def test_user_message_grounding_injected(index):
    session = QASession(index, use_embedding=False)
    hits = index.search("炸板率", top_k=2)
    messages = session._build_messages("什么是炸板率？", hits)
    assert messages[0]["role"] == "system"
    user = messages[-1]
    assert user["role"] == "user"
    assert "【检索到的知识片段】" in user["content"]
    assert "[来源" in user["content"]
    assert "什么是炸板率？" in user["content"]


def test_empty_hits_marked(index):
    session = QASession(index, use_embedding=False)
    messages = session._build_messages("完全不存在的关键词xyz", [])
    assert "未检索到知识库相关内容" in messages[-1]["content"]


def test_llm_error_graceful_degrade(monkeypatch, index):
    session = QASession(index, use_embedding=False)

    def boom(*a, **k):
        raise LLMError("网络请求失败: test")

    monkeypatch.setattr(qa_mod, "chat_tools", boom)
    result = session.answer("什么是炸板率？")
    assert "LLM 调用失败" in result.answer
    assert result.error
    assert result.sources  # 仍返回检索出处
    assert session.history == []  # 失败不写入历史


def test_history_accumulates_and_reused(monkeypatch, index):
    from daily_review.llm.client import ChatResult

    session = QASession(index, use_embedding=False)

    def fake(messages, **k):
        assert "【检索到的知识片段】" in messages[-1]["content"]
        return ChatResult(content="回答一")

    monkeypatch.setattr(qa_mod, "chat_tools", fake)
    r1 = session.answer("什么是首板？")
    assert r1.answer == "回答一"
    assert len(session.history) == 2

    # 第二轮消息里应带上第一轮历史（user 消息原样）
    hits = index.search("连板", top_k=1)
    messages = session._build_messages("连板呢？", hits)
    user_msgs = [m for m in messages if m["role"] == "user"]
    assert user_msgs[0]["content"] == "什么是首板？"


def test_render_sources_format(index):
    hits = index.search("炸板率", top_k=2)
    out = render_sources(hits)
    assert out.startswith("- [")
    assert "术语表.md" in out
