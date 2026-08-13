"""Agent 注册中心（v0.20）：统一注册/发现/调用所有 Agent。

支持跨 Agent 通信：
- QA Agent 通过 `query_agent` 工具调用其他 Agent
- 基金经理 Agent 通过 `query_qa` 工具调用 QA Agent
- 多 Agent 会诊 Orchestrator 并行调用多个 Agent 后综合观点

所有 Agent 注册在模块导入时自动完成（handler 内惰性 import 防循环依赖）。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable

_registry: dict[str, "AgentInfo"] = {}
_registry_lock = threading.Lock()


@dataclass
class AgentInfo:
    """Agent 注册信息。"""

    id: str  # 唯一标识，如 "qa_general", "fund_张坤"
    name: str  # 显示名
    description: str  # 能力描述（供 tool schema 用）
    handler: Callable[[str, dict], str]  # (question, context) -> answer


def register(id: str, name: str, description: str, handler: Callable[[str, dict], str]) -> None:
    """注册一个 Agent。"""
    with _registry_lock:
        _registry[id] = AgentInfo(id=id, name=name, description=description, handler=handler)


def list_agents() -> list[dict]:
    """列出所有已注册 Agent。"""
    with _registry_lock:
        return [
            {"id": a.id, "name": a.name, "description": a.description}
            for a in sorted(_registry.values(), key=lambda x: x.id)
        ]


def call_agent(agent_id: str, question: str, context: dict | None = None) -> str:
    """调用一个 Agent，返回文本回答。未知 Agent 或失败返回错误说明。"""
    with _registry_lock:
        agent = _registry.get(agent_id)
    if agent is None:
        available = ", ".join(_registry)
        return f"（未知 agent：{agent_id}，可用：{available}）"
    try:
        return agent.handler(question, context or {})
    except Exception as exc:
        return f"（调用 {agent.name} 失败：{type(exc).__name__}: {exc}）"


# ---------------------------------------------------------------- 共享 KnowledgeIndex

_index_lock = threading.Lock()
_shared_index = None


def _get_shared_index():
    """懒加载共享 KnowledgeIndex（与 routes.py 的 _get_index 等价，防循环依赖复制一份）。"""
    global _shared_index
    if _shared_index is not None:
        return _shared_index
    with _index_lock:
        if _shared_index is not None:
            return _shared_index
        from daily_review.config import get_settings
        from daily_review.kb.index import KnowledgeIndex

        try:
            _shared_index = KnowledgeIndex(root=get_settings().data_dir)
            _shared_index.build()
        except Exception:
            _shared_index = KnowledgeIndex(root=get_settings().data_dir)
    return _shared_index


# ---------------------------------------------------------------- Handler 工厂


def _qa_handler(question: str, context: dict) -> str:
    """QA Agent handler：知识问答 + 数据工具。"""
    from daily_review.kb.qa import QASession

    index = _get_shared_index()
    trade_date = context.get("trade_date", "")
    session = QASession(index, trade_date=trade_date or None, use_embedding=True)
    result = session.answer(question)
    return result.answer


def _make_fund_handler(manager_id: str) -> Callable[[str, dict], str]:
    """创建基金经理 handler 闭包。"""

    def handler(question: str, context: dict) -> str:
        from daily_review.web.fund_agent import analyze

        klt = context.get("klt", 102)
        trade_date = context.get("trade_date")
        result = analyze(manager_id, question, klt=klt, trade_date=trade_date)
        return result["answer"]

    return handler


def _hotspot_handler(question: str, context: dict) -> str:
    """热点简报 handler：提炼当日热点主线。"""
    # 从 context 取 indicators/boards，无数据时返回说明
    indicators = context.get("indicators")
    boards = context.get("boards")
    if not indicators or not boards:
        return "（热点简报需要 indicators 和 boards 数据，当前 context 中未提供）"
    from daily_review.llm.reporter import _hotspot_brief

    return _hotspot_brief(indicators, boards)


# ---------------------------------------------------------------- 自动注册


def _register_all() -> None:
    """自动注册所有已知 Agent。在模块导入时调用。"""
    # 1. QA Agent
    register(
        "qa_general",
        "知识问答",
        "回答 A 股短线知识问题，可查询涨停/炸板/题材/资金流等实时数据",
        _qa_handler,
    )

    # 2. 基金经理 Agent
    try:
        from daily_review.web.fund_agent import list_managers

        for m in list_managers():
            mid = m["id"]
            register(
                f"fund_{mid}",
                m["name"],
                f"基金经理风格分析：{m['description']}",
                _make_fund_handler(mid),
            )
    except Exception:
        pass  # 没有基金经理档案时静默跳过

    # 3. 热点简报 Agent
    register(
        "hotspot_brief",
        "热点简报",
        "提炼当日热点主线（2-4 条），分析题材运行周期",
        _hotspot_handler,
    )


_register_all()