"""问答会话：RAG 知识片段注入 + DeepSeek function-calling 工具循环。

- 知识问答接地到检索片段（带 [来源] 标签），数据问题走工具取真实数据
- 工具循环：≤ MAX_TOOL_ROUNDS 轮；assistant 的 tool_calls 必须**原样回放**
  （含 V4 的 reasoning_content），再追加 role=tool 结果（DeepSeek 硬性要求）
- 失败降级：LLMError → 回答含错误信息，但仍附检索出处
"""

from __future__ import annotations

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


@dataclass
class QAResult:
    """一次问答的完整结果。"""

    answer: str
    sources: list[SearchHit] = field(default_factory=list)
    tool_rounds: int = 0
    error: str = ""


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
    """一次交互会话：共享索引、交易日、历史上下文与数据工具缓存。"""

    def __init__(
        self,
        index: KnowledgeIndex | None,
        *,
        trade_date: str | None = None,
        top_k: int = 5,
        use_embedding: bool = True,
        api_key: str | None = None,
    ):
        self.index = index
        self.trade_date = trade_date
        self.top_k = top_k
        self.use_embedding = use_embedding
        self.api_key = api_key
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

    # ---------- 工具循环 ----------

    def _run_loop(self, messages: list[dict]) -> tuple[str, int]:
        """≤ MAX_TOOL_ROUNDS 轮：工具调用 → 回放 → role=tool 结果 → 再问，直至文本回答。"""
        ctx = DataToolContext(default_date=self.trade_date)
        rounds = 0
        while rounds < MAX_TOOL_ROUNDS:
            rounds += 1
            res = chat_tools(
                messages,
                tools=get_tool_schemas(),
                tool_choice="auto",
                api_key=self.api_key,
            )
            if not res.tool_calls:
                return res.content or "（模型未返回内容）", rounds
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
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": execute_tool(tc.name, tc.arguments, ctx),
                    }
                )
        return f"（已达 {MAX_TOOL_ROUNDS} 轮工具调用上限，未收敛为最终回答）", rounds

    # ---------- 主入口 ----------

    def answer(self, question: str) -> QAResult:
        hits = self.index.search(question, top_k=self.top_k) if self.index else []
        messages = self._build_messages(question, hits)
        try:
            answer_text, rounds = self._run_loop(messages)
            error = ""
        except LLMError as exc:
            answer_text = f"（LLM 调用失败：{exc}）"
            error = str(exc)
            rounds = 0
        if not error:
            self.history.append({"role": "user", "content": question})
            self.history.append({"role": "assistant", "content": answer_text})
        return QAResult(answer=answer_text, sources=hits, tool_rounds=rounds, error=error)
