"""Agent 规划器（v0.35）：Plan → Execute → Reflect 闭环。

在 QA 工具循环之前，先显式生成执行计划，让 LLM 对复杂问题
先规划再执行，避免「边想边做」遗漏关键步骤。

规划器是可选模块：简单问题跳过规划，复杂问题才消耗 LLM 调用。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from daily_review.llm.client import LLMError, chat

logger = logging.getLogger(__name__)

# 复杂问题启发式关键词——含这些词的问题触发规划
_COMPLEXITY_KEYWORDS = [
    "分析", "对比", "综合", "全面", "怎么样", "如何看待",
    "什么情况", "怎么回事", "总结", "评估", "判断",
    "review", "analyze", "overview", "summary",
]

# 简单问题关键词——含这些词的问题跳过规划（即使较长）
_SIMPLE_KEYWORDS = [
    "什么是", "定义", "解释", "意思",
    "what is", "define", "explain",
]

# 复杂问题最小长度阈值（字符数）
_COMPLEXITY_MIN_LENGTH = 50


@dataclass
class PlanStep:
    """执行计划中的一步。"""

    step: int
    action: str  # 步骤描述，如"查询今日涨停池"
    tool: str | None  # 对应工具名，如"query_zt_pool"，None 表示分析步骤
    expected: str  # 预期获取的信息


@dataclass
class Plan:
    """完整的执行计划。"""

    steps: list[PlanStep]
    reasoning: str  # 规划理由

    def to_dict(self) -> dict:
        return {
            "steps": [{"step": s.step, "action": s.action, "tool": s.tool, "expected": s.expected} for s in self.steps],
            "reasoning": self.reasoning,
        }


def _is_complex_question(question: str) -> bool:
    """判断问题是否需要规划。

    启发式规则：
    - 含复杂关键词 → 复杂
    - 含简单关键词 → 简单
    - 长度 > 阈值 → 复杂
    """
    q = question.strip().lower()
    # 简单关键词优先匹配
    if any(kw in q for kw in _SIMPLE_KEYWORDS):
        return False
    # 复杂关键词匹配
    if any(kw in q for kw in _COMPLEXITY_KEYWORDS):
        return True
    # 长度阈值
    return len(q) > _COMPLEXITY_MIN_LENGTH


def _build_plan_prompt(question: str, tool_schemas: list[dict]) -> str:
    """构建规划器 LLM 调用用的 system + user 消息。"""
    tools_desc = []
    for t in tool_schemas:
        fn = t.get("function", t)
        name = fn.get("name", "?")
        desc = fn.get("description", "")
        params = fn.get("parameters", {}).get("properties", {})
        param_names = ", ".join(params.keys()) if params else "无参数"
        tools_desc.append(f"- {name}：{desc}（参数：{param_names}）")

    system = (
        "你是一个 A 股复盘分析规划师。你要为问答 Agent 制定执行计划。\n"
        "输出必须是一个纯 JSON 对象（无 markdown 包裹，无多余文字）：\n"
        "{\n"
        '  "plan": [\n'
        '    {"step": 1, "action": "步骤描述", "tool": "工具名或null", "expected": "预期信息"}\n'
        "  ],\n"
        '  "reasoning": "规划理由"\n'
        "}\n\n"
        "规则：\n"
        "1. 简单问题（查单只股票、单一指标、定义说明）→ 返回 {\"plan\": null, \"reasoning\": \"简单问题，无需规划\"}\n"
        "2. 复杂问题才生成计划，步骤不超过 5 步\n"
        "3. tool 字段必须精确匹配下方列表中的工具名，不能编造\n"
        "4. 如果问题无法用任何工具回答，返回 {\"plan\": null, \"reasoning\": \"该问题无法通过现有数据工具回答\"}\n"
    )
    user = f"## 可用工具\n\n{chr(10).join(tools_desc)}\n\n## 用户问题\n\n{question}"
    return system + "\n\n" + user


def generate_plan(question: str, tool_schemas: list[dict]) -> Plan | None:
    """生成执行计划。

    启发式判断跳过简单问题；复杂问题调用 LLM 生成计划。
    失败时返回 None（降级为无规划，不影响主流程）。

    Returns:
        Plan 对象（复杂问题），或 None（简单问题/失败）
    """
    if not _is_complex_question(question):
        return None

    prompt = _build_plan_prompt(question, tool_schemas)
    try:
        raw = chat(
            [{"role": "system", "content": "你是 A 股复盘分析规划师。"},
             {"role": "user", "content": prompt}],
            temperature=0.3,  # 低温度保证输出稳定
            max_tokens=800,
        )
        # 解析 JSON
        data = json.loads(raw)
        if data.get("plan") is None:
            return None  # LLM 判定为简单问题
        steps_data = data["plan"]
        if not isinstance(steps_data, list) or len(steps_data) == 0:
            return None
        steps = []
        for s in steps_data:
            steps.append(PlanStep(
                step=int(s.get("step", len(steps) + 1)),
                action=str(s.get("action", "")),
                tool=str(s.get("tool")) if s.get("tool") else None,
                expected=str(s.get("expected", "")),
            ))
        return Plan(steps=steps, reasoning=str(data.get("reasoning", "")))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        logger.warning("规划器解析失败: %s — raw=%s", exc, raw[:200] if 'raw' in dir() else '?')
        return None
    except LLMError as exc:
        logger.warning("规划器 LLM 调用失败: %s", exc)
        return None


def reflect(question: str, plan: Plan | None, results: list[dict]) -> str | None:
    """回顾执行结果，判断是否需要补充。

    在工具循环结束后调用。如果计划已完整执行，返回 None；
    如果发现需要补充查询，返回补充说明文本。

    Args:
        question: 原始问题
        plan: 执行计划（可能为 None）
        results: 工具执行结果列表，每项 {"tool": str, "result_summary": str}

    Returns:
        补充说明文本，或 None（计划已完成）
    """
    if plan is None:
        return None

    # 简单规则：如果所有步骤都执行了，视为完成
    planned_tools = [s.tool for s in plan.steps if s.tool]
    executed_tools = [r["tool"] for r in results]
    missing = [t for t in planned_tools if t not in executed_tools]
    if not missing:
        return None

    return f"（规划器提示：计划中 {len(missing)} 个步骤未执行：{', '.join(missing)}）"