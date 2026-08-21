"""web/agent_registry.py 测试：注册/发现/调用 + 跨 Agent 工具 + 多 Agent 会诊（全离线）。"""

from __future__ import annotations

import json

import pytest

from daily_review.llm.client import ChatResult, ToolCall

_TEST_ANSWER = "我是测试 Agent 的回答。"


# ---------------------------------------------------------------- Helpers


def _make_chat_result(content: str, tool_calls: list | None = None) -> ChatResult:
    raw = None
    if tool_calls:
        raw = []
        for tc in tool_calls:
            raw.append(
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                }
            )
    return ChatResult(
        content=content,
        tool_calls=tool_calls or [],
        finish_reason="stop",
        reasoning_content=None,
        raw_tool_calls=raw,
    )


def _patch_chat(monkeypatch, answer: str = _TEST_ANSWER, fail: bool = False):
    """Mock chat() to return a fixed answer (or raise)."""

    def _mock(*args, **kwargs):
        if fail:
            raise Exception("LLM 模拟失败")
        return answer

    monkeypatch.setattr("daily_review.llm.client.chat", _mock)


def _patch_qa_handler(monkeypatch, answer: str = _TEST_ANSWER):
    """替换注册表中 qa_general 的 handler 为固定回答（跳过真实 QA 会话与 LLM）。"""

    def _mock_handler(q, ctx):
        return answer

    from daily_review.web import agent_registry

    with agent_registry._registry_lock:
        agent_registry._registry["qa_general"].handler = _mock_handler


def _patch_chat_tools(monkeypatch, answer: str = _TEST_ANSWER, tool_calls: list | None = None, fail: bool = False):
    """Mock chat_tools() to return a fixed ChatResult."""

    def _mock(*args, **kwargs):
        if fail:
            raise Exception("LLM 模拟失败")
        return _make_chat_result(answer, tool_calls)

    monkeypatch.setattr("daily_review.llm.client.chat_tools", _mock)


# ---------------------------------------------------------------- Agent Registry 基础


class TestAgentRegistry:
    def test_register_and_list(self):
        from daily_review.web.agent_registry import list_agents, register

        register("test_agent", "测试", "测试用 Agent", lambda q, ctx: f"回答：{q}")
        agents = list_agents()
        ids = [a["id"] for a in agents]
        assert "test_agent" in ids

    def test_call_agent(self):
        from daily_review.web.agent_registry import call_agent, register

        register("test_hello", "你好", "问候", lambda q, ctx: f"你好，{q}")
        result = call_agent("test_hello", "世界")
        assert "世界" in result

    def test_call_unknown_agent(self):
        from daily_review.web.agent_registry import call_agent

        result = call_agent("nonexistent", "问题")
        assert "未知" in result

    def test_call_agent_failure(self):
        from daily_review.web.agent_registry import call_agent, register

        def _fail(q, ctx):
            raise ValueError("模拟失败")

        register("test_fail", "失败", "总是失败", _fail)
        result = call_agent("test_fail", "问题")
        assert "失败" in result

    def test_qa_general_registered(self):
        """qa_general 应已在模块导入时自动注册。"""
        from daily_review.web.agent_registry import list_agents

        agents = list_agents()
        ids = [a["id"] for a in agents]
        assert "qa_general" in ids

    def test_list_returns_sorted(self):
        from daily_review.web.agent_registry import list_agents

        agents = list_agents()
        ids = [a["id"] for a in agents]
        assert ids == sorted(ids)


# ---------------------------------------------------------------- 跨 Agent 工具（query_agent）


class TestQueryAgentTool:
    def test_query_agent_tool_returns_answer(self, monkeypatch):
        """QA 的 query_agent 工具调用基金经理 → 返回基金经理的回答。"""
        from daily_review.kb.tools import DataToolContext, execute_tool, get_tool_schemas

        # 验证 schema 中存在 query_agent
        schemas = get_tool_schemas()
        names = [s["function"]["name"] for s in schemas]
        assert "query_agent" in names

        # 验证 handler 存在
        ctx = DataToolContext(default_date="20260806")
        result, ms = execute_tool("query_agent", {"agent_id": "qa_general", "question": "测试"}, ctx)
        data = json.loads(result)
        assert "agent_id" in data
        assert "answer" in data
        assert ms > 0

    def test_query_agent_tool_missing_agent_id(self, monkeypatch):
        from daily_review.kb.tools import DataToolContext, execute_tool

        ctx = DataToolContext(default_date="20260806")
        result, ms = execute_tool("query_agent", {"question": "测试"}, ctx)
        data = json.loads(result)
        assert "error" in data

    def test_query_agent_tool_missing_question(self, monkeypatch):
        from daily_review.kb.tools import DataToolContext, execute_tool

        ctx = DataToolContext(default_date="20260806")
        result, ms = execute_tool("query_agent", {"agent_id": "qa_general"}, ctx)
        data = json.loads(result)
        assert "error" in data


# ---------------------------------------------------------------- 基金经理 query_qa 工具


class TestFundQueryQATool:
    def test_query_qa_tool_schema_in_fund(self, monkeypatch, tmp_path):
        """基金经理 analyze 应支持 query_qa 工具（通过 chat_tools 调用）。"""
        # 模拟基金经理档案（写入 tmp_path，避免污染真实 skills 目录）
        from daily_review.config import get_settings

        s = get_settings()
        skills_dir = tmp_path / "skills" / "fund-styles"
        skills_dir.mkdir(parents=True, exist_ok=True)
        (skills_dir / "test-manager.md").write_text(
            "---\nname: test-manager\ndescription: 测试基金经理\n---\n\n## 风格\n测试风格",
            encoding="utf-8",
        )
        monkeypatch.setattr(s, "project_root", tmp_path)
        monkeypatch.setattr(s, "data_dir", tmp_path / "data")

        # 模拟 chat_tools 返回 query_qa 工具调用
        tc = ToolCall(id="call_1", name="query_qa", arguments={"question": "今日市场情绪？"}, raw={})
        mock_result = _make_chat_result("初步分析", [tc])

        called = []

        def _mock_chat_tools(*args, **kwargs):
            if not called:
                called.append(1)
                return mock_result
            # 第二轮：返回最终回答
            return _make_chat_result("最终综合回答", None)

        monkeypatch.setattr("daily_review.web.fund_agent.chat_tools", _mock_chat_tools)

        # 模拟 query_qa 工具执行（避免真实调用 QA Agent / LLM）
        monkeypatch.setattr(
            "daily_review.web.fund_agent._execute_query_qa",
            lambda args: json.dumps({"answer": "QA Agent 提供的市场概况"}),
        )

        # 模拟 kline fetch 和 中军识别
        monkeypatch.setattr("daily_review.data.eastmoney.fetch_kline", lambda *a, **kw: _make_kline_df())
        monkeypatch.setattr("daily_review.data.repo.load_csv", lambda *a, **kw: _make_zt_df())
        monkeypatch.setattr("daily_review.data.eastmoney_pool.fetch_market_caps", lambda *a, **kw: {"600519": 2e12})

        from daily_review.web.fund_agent import analyze

        result = analyze("test-manager", "分析 600519", klt=102, trade_date="20260806")
        assert "最终综合回答" in result["answer"] or "综合回答" in result["answer"]
        assert len(called) == 1  # 确实验证工具被调用了

    def test_query_qa_compacts_error(self, monkeypatch):
        """query_qa 工具在调用失败时应返回错误 JSON。"""
        from daily_review.web.fund_agent import _execute_query_qa

        result = _execute_query_qa({"question": ""})
        data = json.loads(result)
        assert "error" in data


# ---------------------------------------------------------------- 多 Agent 会诊 API


class TestConsultAPI:
    def test_consult_api_returns_synthesis(self, app, monkeypatch):
        """POST /api/agents/consult 应返回各 Agent 回答 + 综合结论。"""
        _patch_qa_handler(monkeypatch)
        _patch_chat(monkeypatch)  # 合成 LLM 调用
        c = app.test_client()
        r = c.post(
            "/api/agents/consult",
            json={"question": "市场如何？", "agent_ids": ["qa_general"]},
        )
        assert r.status_code == 200, r.get_data(as_text=True)
        data = r.get_json()
        assert "responses" in data
        assert "synthesis" in data
        assert "qa_general" in data["responses"]
        assert _TEST_ANSWER in data["responses"]["qa_general"]["answer"]

    def test_consult_api_missing_question(self, app):
        c = app.test_client()
        r = c.post("/api/agents/consult", json={"agent_ids": ["qa_general"]})
        assert r.status_code == 400

    def test_consult_api_missing_agents(self, app):
        c = app.test_client()
        r = c.post("/api/agents/consult", json={"question": "市场如何？", "agent_ids": []})
        assert r.status_code == 400

    def test_consult_api_invalid_agent(self, app):
        c = app.test_client()
        r = c.post(
            "/api/agents/consult",
            json={"question": "市场如何？", "agent_ids": ["nonexistent"]},
        )
        assert r.status_code == 400
        data = r.get_json()
        assert "未知" in data["error"]

    def test_consult_api_multiple_agents(self, app, monkeypatch):
        """多个不同 Agent 应各自返回回答。"""
        _patch_qa_handler(monkeypatch)
        _patch_chat(monkeypatch)  # 合成 LLM 调用
        # 替换 hotspot_brief 的 handler（避免真实热点调用）
        from daily_review.web import agent_registry

        with agent_registry._registry_lock:
            agent_registry._registry["hotspot_brief"].handler = lambda q, ctx: "热点回答"
        c = app.test_client()
        r = c.post(
            "/api/agents/consult",
            json={"question": "市场如何？", "agent_ids": ["qa_general", "hotspot_brief"]},
        )
        assert r.status_code == 200
        data = r.get_json()
        assert len(data["responses"]) == 2
        assert "qa_general" in data["responses"] and "hotspot_brief" in data["responses"]

    def test_agents_list_endpoint(self, app):
        """GET /api/agents/list 应返回所有注册 Agent。"""
        from daily_review.web.agent_registry import list_agents

        c = app.test_client()
        r = c.get("/api/agents/list")
        assert r.status_code == 200
        data = r.get_json()
        assert "agents" in data
        assert len(data["agents"]) == len(list_agents())


# ---------------------------------------------------------------- Fixtures


def _make_kline_df():
    import pandas as pd

    return pd.DataFrame(
        {
            "trade_date": ["2026-08-04", "2026-08-11"],
            "open": [100.0, 105.0],
            "close": [104.0, 108.0],
            "high": [106.0, 110.0],
            "low": [98.0, 103.0],
            "volume": [10000, 12000],
            "pct_change": [4.0, 3.8],
        }
    )


def _make_zt_df():
    import pandas as pd

    return pd.DataFrame(
        {
            "code": ["600519", "000858"],
            "name": ["贵州茅台", "五粮液"],
            "industry": ["白酒", "白酒"],
            "lb_num": [1, 1],
            "first_limit_time": ["093000", "093500"],
        }
    )


@pytest.fixture
def app(tmp_path, monkeypatch):
    """注入 tmp 目录，创建 Flask 测试客户端。"""
    from daily_review.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "data_dir", tmp_path / "data")
    monkeypatch.setattr(s, "prompts_dir", tmp_path / "prompts")
    pdir = tmp_path / "prompts"
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "strategies").mkdir(parents=True, exist_ok=True)
    (pdir / "strategies" / "战法模板.md").write_text(
        "---\nid: strategy.template\nname: 战法模板\nrole: strategy\nstatus: draft\n---\n\n## 1\n模板",
        encoding="utf-8",
    )

    # 注入 skills/fund-styles 目录
    skills_dir = tmp_path / "skills" / "fund-styles"
    skills_dir.mkdir(parents=True, exist_ok=True)
    (skills_dir / "test-manager.md").write_text(
        "---\nname: test-manager\ndescription: 测试用\n---\n\n## 风格\n测试",
        encoding="utf-8",
    )
    monkeypatch.setattr(s, "project_root", tmp_path)

    # 重新注册（清除旧注册，用新路径）
    from daily_review.web import agent_registry

    with agent_registry._registry_lock:
        agent_registry._registry.clear()
    agent_registry._register_all()

    from daily_review.web.app import create_app

    app = create_app()
    app.config["TESTING"] = True
    return app