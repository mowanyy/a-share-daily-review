"""问答会话：RAG 知识片段注入 + DeepSeek function-calling 工具循环。

v0.35 新增：
- Agent 规划器：复杂问题先规划再执行（Plan → Execute → Reflect）
- 工具调用 Trace：记录每次工具调用的名称、参数、耗时、结果摘要
- QAResult 扩展 trace 字段，供审计日志 UI 展示
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from daily_review.kb.index import KnowledgeIndex, SearchHit
from daily_review.kb.tools import DataToolContext, execute_tool, get_tool_schemas
from daily_review.llm.client import LLMError, chat_tools
from daily_review.prompts import get_prompt

MAX_TOOL_ROUNDS = 5
HISTORY_ROUNDS = 10
HIT_TEXT_CAP = 300

_DEFAULT_SYSTEM = "你是 A 股超短连板复盘数据的交互问答助手。基于本地知识库与数据工具作答，不编造。"

RAG_SYSTEM_APPEND = """

## 知识库（RAG）使用方式

- 用户消息末尾的【检索到的知识片段】来自本地短线知识库（prompts/、docs/、knowledge/），
  回答短线知识问题优先引用这些片段，可标注 [来源]。
- 知识片段是检索命中的原文，引述时忠于原文，不扩大其含义。
- 数据类问题（涨停/炸板/题材/资金流/晋级率等）调用数据工具获取真实数据作答，
  不要用知识片段里的旧数字代替当日数据。
- 知识库没有相关内容且工具也查不到时，如实说「知识库暂无该内容 / 当日无该数据」，绝不编造。
- 术语以 prompts/glossary/术语表.md 为准。
"""

# 规划器注入的 system prompt 附加段
PLANNER_SYSTEM_APPEND = """

## 执行计划

你已制定以下执行计划，请按计划顺序执行各步骤。
在每一步执行后，观察结果并决定是否继续下一步。
如果某步结果出乎意料，可以调整后续步骤。
"""


@dataclass
class ToolCallRecord:
    """一次工具调用的记录。"""

    round: int
    tool_name: str
    arguments: dict
    result_summary: str  # 截断至 200 字符
    duration_ms: float


@dataclass
class QATrace:
    """一次问答的完整追踪信息。"""

    question: str
    plan: dict | None  # 规划步骤（如有）
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    total_rounds: int = 0
    total_duration_ms: float = 0.0
    retrieval_sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "question": self.question,
            "plan": self.plan,
            "tool_calls": [
                {
                    "round": tc.round,
                    "tool": tc.tool_name,
                    "args": tc.arguments,
                    "duration_ms": round(tc.duration_ms, 1),
                    "result_summary": tc.result_summary,
                }
                for tc in self.tool_calls
            ],
            "total_rounds": self.total_rounds,
            "total_duration_ms": round(self.total_duration_ms, 1),
            "retrieval_sources": self.retrieval_sources,
        }


@dataclass
class QAResult:
    """一次问答的完整结果。"""

    answer: str
    sources: list[SearchHit] = field(default_factory=list)
    tool_rounds: int = 0
    error: str = ""
    trace: QATrace | None = None


def _truncate_result(raw: str, max_len: int = 200) -> str:
    """截断工具结果到指定长度，用于 trace 摘要。"""
    if len(raw) <= max_len:
        return raw
    return raw[:max_len] + f"…（共{len(raw)}字符）"


def build_system_prompt() -> str:
    """问答助手系统提示：prompt 文件正文 + RAG 使用纪律。

    prompt 文件正文已含「知识库（RAG）使用方式」节时不重复注入（防冗余）。
    """
    p = get_prompt("system.assistant")
    base = p.body if p and p.body else _DEFAULT_SYSTEM
    if "知识库（RAG）" not in base:
        base += RAG_SYSTEM_APPEND
    return base


def render_sources(hits: list[SearchHit], limit: int = 5) -> str:
    """把检索命中渲染成「- [来源] 片段」列表，供 CLI 展示。"""
    if not hits:
        return "（未检索到知识库相关内容）"
    lines = []
    for h in hits[:limit]:
        where = f"{h.source_rel} · {h.section}"
        if h.date:
            where += f" · {h.date}"
        lines.append(f"- [{where}] {h.text[:120]}")
    return "\n".join(lines)


class QASession:
    """一次交互会话：共享索引、交易日、历史上下文与数据工具缓存。

    v0.35：支持 use_planner（规划器）与 trace 追踪。
    """

    def __init__(
        self,
        index: KnowledgeIndex | None,
        *,
        trade_date: str | None = None,
        top_k: int = 5,
        use_embedding: bool = True,
        api_key: str | None = None,
        use_planner: bool = True,
    ):
        self.index = index
        self.trade_date = trade_date
        self.top_k = top_k
        self.use_embedding = use_embedding
        self.api_key = api_key
        self.use_planner = use_planner
        self.history: list[dict] = []

    # ---------- 消息组装 ----------

    def _rag_grounding(self, hits: list[SearchHit]) -> str:
        if not hits:
            return ""
        parts = ["【检索到的知识片段】（供参考，回答可优先引用并标注 [来源]）"]
        for h in hits[: self.top_k]:
            src = f"[来源: {h.source_rel} · {h.section}]"
            if h.date:
                src += f" · {h.date}"
            parts.append(f"- {src}\n  {h.text[:HIT_TEXT_CAP]}")
        return "\n".join(parts)

    def _build_messages(self, question: str, hits: list[SearchHit]) -> list[dict]:
        messages = [{"role": "system", "content": build_system_prompt()}]
        messages.extend(self.history[-HISTORY_ROUNDS:])
        user_content = question
        rag = self._rag_grounding(hits)
        user_content += "\n\n" + (rag or "（未检索到知识库相关内容，请基于工具数据作答或说明能力边界）")
        messages.append({"role": "user", "content": user_content})
        return messages

    # ---------- 规划器 ----------

    def _generate_plan(self, question: str) -> dict | None:
        """调用规划器生成执行计划。失败/简单问题返回 None。"""
        if not self.use_planner:
            return None
        try:
            from daily_review.kb.planner import generate_plan

            plan = generate_plan(question, get_tool_schemas())
            if plan is None:
                return None
            plan_dict = plan.to_dict()
            return plan_dict
        except Exception:
            return None

    def _inject_plan(self, messages: list[dict], plan_dict: dict | None) -> None:
        """将计划注入到 system prompt 中。"""
        if plan_dict is None:
            return
        steps = plan_dict.get("steps", [])
        if not steps:
            return
        plan_text = "\n".join(
            f"  Step {s['step']}: {s['action']}（工具：{s['tool'] or '分析'}）"
            for s in steps
        )
        annotation = f"\n\n## 执行计划\n\n{plan_text}\n\n请按上述计划顺序执行。"
        for msg in messages:
            if msg["role"] == "system":
                msg["content"] += annotation
                break

    # ---------- 工具循环 ----------

    def _run_loop(self, messages: list[dict]) -> tuple[str, int, list[ToolCallRecord]]:
        """≤ MAX_TOOL_ROUNDS 轮：工具调用 → 回放 → role=tool 结果 → 再问，直至文本回答。

        Returns:
            (answer_text, rounds, tool_call_records)
        """
        ctx = DataToolContext(default_date=self.trade_date)
        tool_records: list[ToolCallRecord] = []
        rounds = 0
        while rounds < MAX_TOOL_ROUNDS:
            rounds += 1
            t0 = time.perf_counter()
            res = chat_tools(
                messages,
                tools=get_tool_schemas(),
                tool_choice="auto",
                api_key=self.api_key,
            )
            duration_ms = (time.perf_counter() - t0) * 1000
            if not res.tool_calls:
                return res.content or "（模型未返回内容）", rounds, tool_records
            # 原样回放 assistant 的 tool_calls（含 reasoning_content），再追加 tool 结果
            assistant_msg: dict = {
                "role": "assistant",
                "content": res.content,
                "tool_calls": res.raw_tool_calls or [],
            }
            if res.reasoning_content:
                assistant_msg["reasoning_content"] = res.reasoning_content
            messages.append(assistant_msg)
            for tc in res.tool_calls:
                tool_t0 = time.perf_counter()
                result, tool_duration = execute_tool(tc.name, tc.arguments, ctx)
                tool_duration_ms = (time.perf_counter() - tool_t0) * 1000 + tool_duration
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    }
                )
                # 记录 trace
                tool_records.append(
                    ToolCallRecord(
                        round=rounds,
                        tool_name=tc.name,
                        arguments=tc.arguments,
                        result_summary=_truncate_result(result),
                        duration_ms=round(tool_duration_ms, 1),
                    )
                )
        return f"（已达 {MAX_TOOL_ROUNDS} 轮工具调用上限，未收敛为最终回答）", rounds, tool_records

    # ---------- 主入口 ----------

    def answer(self, question: str) -> QAResult:
        t0 = time.perf_counter()
        hits = self.index.search(question, top_k=self.top_k) if self.index else []
        messages = self._build_messages(question, hits)

        # 规划器
        plan_dict = self._generate_plan(question)
        self._inject_plan(messages, plan_dict)

        try:
            answer_text, rounds, tool_records = self._run_loop(messages)
            error = ""
        except LLMError as exc:
            answer_text = f"（LLM 调用失败：{exc}）"
            error = str(exc)
            rounds = 0
            tool_records = []

        if not error:
            self.history.append({"role": "user", "content": question})
            self.history.append({"role": "assistant", "content": answer_text})

        total_duration_ms = (time.perf_counter() - t0) * 1000

        # 构建 trace
        trace = QATrace(
            question=question,
            plan=plan_dict,
            tool_calls=tool_records,
            total_rounds=rounds,
            total_duration_ms=round(total_duration_ms, 1),
            retrieval_sources=[f"{h.source_rel} · {h.section}" for h in hits[:self.top_k]],
        )

        return QAResult(
            answer=answer_text,
            sources=hits,
            tool_rounds=rounds,
            error=error,
            trace=trace,
        )