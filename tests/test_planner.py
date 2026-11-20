"""规划器测试：复杂问题才生成计划，简单问题跳过，边界条件处理。"""

from __future__ import annotations

from daily_review.kb.planner import (
    PlanStep,
    Plan,
    _is_complex_question,
    generate_plan,
    reflect,
)


class TestComplexityHeuristic:
    """启发式判断：什么算复杂问题，什么算简单问题。"""

    def test_simple_definition(self):
        """含"什么是"的问题 → 简单。"""
        assert not _is_complex_question("什么是炸板率？")
        assert not _is_complex_question("什么是连板？")

    def test_simple_data_query(self):
        """数据查询类短问题 → 简单。"""
        assert not _is_complex_question("今日涨停数多少？")
        assert not _is_complex_question("600519 今天行情")

    def test_complex_analysis(self):
        """含"分析"的问题 → 复杂。"""
        assert _is_complex_question("分析今日市场情绪")
        assert _is_complex_question("分析一下今天的连板梯队")

    def test_complex_comparison(self):
        """含"对比"的问题 → 复杂。"""
        assert _is_complex_question("对比这两天题材变化")

    def test_complex_overview(self):
        """含"怎么样"、"全面"的问题 → 复杂。"""
        assert _is_complex_question("今天市场怎么样")
        assert _is_complex_question("全面点评今日涨停生态")

    def test_long_question(self):
        """长问题（>50 字符）→ 复杂。"""
        long_q = "帮我看看今天涨停的股票里，有哪些是连板超过2板的，各属于什么题材？另外再看看炸板股的资金流向和龙虎榜数据"
        assert len(long_q) > 50
        assert _is_complex_question(long_q)

    def test_short_question(self):
        """短问题（≤50 字符）且无复杂关键词 → 简单。"""
        assert not _is_complex_question("帮我查一下")  # 无关键词，短
        assert not _is_complex_question("今天涨停数")  # 无关键词，短


class TestGeneratePlan:
    """generate_plan 函数测试（mock LLM 调用）。"""

    def test_simple_question_returns_none(self, monkeypatch):
        """简单问题 → 不调用 LLM，直接返回 None。"""
        # 即使 mock 也使简单问题跳过
        def mock_chat(*args, **kwargs):
            return '{"plan": [{"step": 1, "action": "x", "tool": "query_zt_pool", "expected": "涨停数据"}]}'

        monkeypatch.setattr("daily_review.kb.planner.chat", mock_chat)
        # 简单问题应该不调用 chat，直接返回 None
        result = generate_plan("什么是炸板率？", [])
        assert result is None

    def test_no_tools_question(self, monkeypatch):
        """无法用工具回答的问题 → 返回 None。"""
        def mock_chat(*args, **kwargs):
            return '{"plan": null, "reasoning": "该问题无法通过现有数据工具回答"}'

        monkeypatch.setattr("daily_review.kb.planner.chat", mock_chat)
        result = generate_plan("分析一下明天的天气", [])
        assert result is None

    def test_valid_plan_returned(self, monkeypatch):
        """复杂问题 → 返回 Plan 对象。"""
        def mock_chat(*args, **kwargs):
            return (
                '{"plan": ['
                '{"step": 1, "action": "查询今日涨停池", "tool": "query_zt_pool", "expected": "涨停家数、最高板"},'
                '{"step": 2, "action": "查询连板统计", "tool": "query_ladder_stats", "expected": "晋级率"}'
                '], "reasoning": "先获取整体数据，再分析晋级情况"}'
            )

        monkeypatch.setattr("daily_review.kb.planner.chat", mock_chat)
        tool_schemas = [
            {"function": {"name": "query_zt_pool", "description": "涨停池"}},
            {"function": {"name": "query_ladder_stats", "description": "连板统计"}},
        ]
        result = generate_plan("分析今日市场情绪", tool_schemas)
        assert result is not None
        assert isinstance(result, Plan)
        assert len(result.steps) == 2
        assert result.steps[0].tool == "query_zt_pool"
        assert result.steps[1].tool == "query_ladder_stats"
        assert result.reasoning

    def test_plan_to_dict(self, monkeypatch):
        """Plan.to_dict() 输出正确格式。"""
        plan = Plan(
            steps=[
                PlanStep(step=1, action="查询涨停池", tool="query_zt_pool", expected="涨停数据"),
            ],
            reasoning="先看整体",
        )
        d = plan.to_dict()
        assert d["reasoning"] == "先看整体"
        assert len(d["steps"]) == 1
        assert d["steps"][0]["tool"] == "query_zt_pool"
        assert d["steps"][0]["step"] == 1

    def test_llm_error_returns_none(self, monkeypatch):
        """LLM 调用失败 → 返回 None（不中断主流程）。"""
        def mock_chat(*args, **kwargs):
            from daily_review.llm.client import LLMError
            raise LLMError("模拟失败")

        monkeypatch.setattr("daily_review.kb.planner.chat", mock_chat)
        result = generate_plan("分析今日市场情绪", [])
        assert result is None

    def test_llm_bad_json_returns_none(self, monkeypatch):
        """LLM 返回非法 JSON → 返回 None。"""
        def mock_chat(*args, **kwargs):
            return "这不是 JSON"

        monkeypatch.setattr("daily_review.kb.planner.chat", mock_chat)
        result = generate_plan("分析今日市场情绪", [])
        assert result is None


class TestReflect:
    """回顾功能测试。"""

    def test_no_plan_no_reflect(self):
        """无计划 → 不需要回顾。"""
        assert reflect("test", None, []) is None

    def test_all_tools_executed(self):
        """所有计划中的工具都已执行 → 不需要回顾。"""
        plan = Plan(
            steps=[
                PlanStep(step=1, action="查涨停池", tool="query_zt_pool", expected=""),
            ],
            reasoning="测试",
        )
        results = [{"tool": "query_zt_pool", "result_summary": "涨停60家"}]
        assert reflect("test", plan, results) is None

    def test_missing_tool_returns_hint(self):
        """有计划中的工具未执行 → 返回补充提示。"""
        plan = Plan(
            steps=[
                PlanStep(step=1, action="查涨停池", tool="query_zt_pool", expected=""),
                PlanStep(step=2, action="查连板统计", tool="query_ladder_stats", expected=""),
            ],
            reasoning="测试",
        )
        results = [{"tool": "query_zt_pool", "result_summary": "涨停60家"}]
        hint = reflect("test", plan, results)
        assert hint is not None
        assert "query_ladder_stats" in hint
        assert "1 个步骤" in hint