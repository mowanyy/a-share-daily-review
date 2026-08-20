"""飞书 Agent 网关测试（v0.32）：合规检查、消息路由、Token 管理。"""

from __future__ import annotations

from daily_review.web.feishu_gateway import (
    COMPLIANCE_REPLY,
    TokenManager,
    is_compliance_risk,
    route_message,
)


# ---------- 合规检查 ----------

def test_compliance_risk_trading_advice():
    """交易建议类问题应被判定为合规风险。"""
    assert is_compliance_risk("推荐一只股票")
    assert is_compliance_risk("能不能买这个股票")
    assert is_compliance_risk("帮我推荐一个")
    assert is_compliance_risk("给个建议，买什么")
    assert is_compliance_risk("要不要卖")


def test_compliance_risk_data_query():
    """数据查询类问题不应被判定为合规风险。"""
    assert not is_compliance_risk("今天涨停多少家")
    assert not is_compliance_risk("涨停名单有哪些")
    assert not is_compliance_risk("情绪温度是多少")
    assert not is_compliance_risk("空间板是谁")
    assert not is_compliance_risk("有没有炸板")


def test_compliance_risk_normal_questions():
    """正常问题不应被判定为合规风险。"""
    assert not is_compliance_risk("你好")
    assert not is_compliance_risk("在吗")
    assert not is_compliance_risk("今天什么情况")


def test_compliance_risk_edge_cases():
    """边界情况：空字符串、纯数字等。"""
    assert not is_compliance_risk("")
    assert not is_compliance_risk("123")
    assert not is_compliance_risk("大盘")


# ---------- 消息路由 ----------

def test_route_compliance_rejection():
    """合规风险问题应返回拒绝回复。"""
    reply = route_message("推荐一只股票")
    assert "无法给出" in reply


def test_route_greeting():
    """问候语应返回友好回复。"""
    reply = route_message("你好")
    assert "我在的" in reply


def test_route_help():
    """帮助请求应返回帮助信息。"""
    reply = route_message("help")
    assert "帮助" in reply or "查询" in reply


def test_route_empty():
    """空消息应返回提示。"""
    reply = route_message("")
    assert "请说" in reply


def test_route_fallback():
    """无法识别的问题应返回兜底回复。"""
    reply = route_message("asdfghjkl")
    assert "暂时无法" in reply


def test_route_with_qa_factory():
    """带 QA 工厂的路由应优先使用 QA 回答。"""

    # mock QA 会话
    class MockSession:
        def answer(self, text):
            from dataclasses import dataclass, field

            @dataclass
            class Result:
                answer: str = ""
                sources: list = field(default_factory=list)
                tool_rounds: int = 0
                error: str = ""

            return Result(answer="QA 回答：今日涨停68家")

    def factory():
        return MockSession()

    reply = route_message("今天涨停多少家", qa_session_factory=factory)
    assert "QA 回答" in reply


# ---------- Token 管理 ----------

def test_token_manager_initial_state():
    """TokenManager 初始状态应无 token。"""
    tm = TokenManager("test_id", "test_secret")
    assert tm._token == ""
    assert tm._expires_at == 0


def test_token_manager_no_network():
    """无网络时获取 token 应不报错（日志记录失败）。"""
    tm = TokenManager("invalid_id", "invalid_secret")
    token = tm.token  # 触发 _refresh，但会失败
    assert token == ""  # 失败时保持空 token