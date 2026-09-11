"""飞书 Agent 网关测试（v0.32）：合规检查、消息路由、Token 管理。"""

from __future__ import annotations

import json
from types import SimpleNamespace

from daily_review.web.feishu_gateway import (
    COMPLIANCE_REPLY,
    TokenManager,
    _handle_p2_im_message_receive,
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


# ---------- 盘中实时查询快速通道（v0.33） ----------


def test_realtime_query_uses_market_summary_fn():
    """"现在什么情况"应走快速通道返回市场概况。"""
    reply = route_message(
        "现在什么情况",
        market_summary_fn=lambda: "📊 盘中实时概况（10:30）\n涨停 35 家 | 炸板 8 家",
    )
    assert "盘中实时概况" in reply
    assert "35" in reply


def test_realtime_query_keywords():
    """多个关键词均应触发快速通道。"""
    for kw in ["现在", "实时", "当前", "市场概况", "怎么样"]:
        reply = route_message(
            kw,
            market_summary_fn=lambda: f"快速通道响应: {kw}",
        )
        assert "快速通道响应" in reply


def test_realtime_query_fallback_when_empty():
    """快速通道返回空字符串时应降级。"""
    reply = route_message(
        "现在什么情况",
        market_summary_fn=lambda: "",
    )
    # 降级到兜底回复
    assert "暂时无法" in reply


def test_realtime_query_fallback_on_exception():
    """快速通道异常时应降级，不崩溃。"""
    def _broken_fn():
        raise RuntimeError("模拟异常")

    reply = route_message(
        "现在什么情况",
        market_summary_fn=_broken_fn,
    )
    # 异常后降级到兜底
    assert "暂时无法" in reply


def test_realtime_query_without_fn_uses_normal_route():
    """未配置 market_summary_fn 时走正常路由（不触发快速通道）。"""
    reply = route_message("现在什么情况")
    # 无 market_summary_fn 时，"现在什么情况"不是合规风险，也不是问候/帮助
    # 应走兜底
    assert "暂时无法" in reply


def test_realtime_query_does_not_affect_compliance():
    """合规风险不受快速通道影响。"""
    reply = route_message(
        "推荐一只股票",
        market_summary_fn=lambda: "📊 盘中实时概况",
    )
    # 合规优先，不应走快速通道
    assert "无法给出" in reply


# ---------- 多轮对话记忆注入（v0.34） ----------


def _make_mock_session_manager(tmp_path):
    """创建 ChatSessionManager 用于测试。"""
    from daily_review.web.chat_session import ChatSessionManager
    return ChatSessionManager(data_dir=tmp_path)


def test_session_memory_injects_history(tmp_path):
    """chat_session_manager 应注入历史到 QA 会话。"""
    from daily_review.web.chat_session import ChatSessionManager
    m = ChatSessionManager(data_dir=tmp_path)
    m.add_turn("test_chat", "昨天涨停多少家", "昨天涨停68家")

    class MockSessionWithHistory:
        def __init__(self):
            self.history = []

        def answer(self, text):
            # 验证历史已注入
            has_history = len(self.history) > 0
            from dataclasses import dataclass, field
            @dataclass
            class Result:
                answer: str = ""
                sources: list = field(default_factory=list)
                tool_rounds: int = 0
                error: str = ""
            return Result(
                answer=f"历史注入={'是' if has_history else '否'}"
            )

    def factory():
        return MockSessionWithHistory()

    reply = route_message(
        "今天涨停多少家",
        qa_session_factory=factory,
        chat_session_manager=m,
        chat_id="test_chat",
    )
    assert "历史注入=是" in reply


def test_session_memory_saves_turn(tmp_path):
    """chat_session_manager 应在 QA 回答后保存新轮次。"""
    from daily_review.web.chat_session import ChatSessionManager
    m = ChatSessionManager(data_dir=tmp_path)

    class MockSession:
        def __init__(self):
            self.history = []

        def answer(self, text):
            from dataclasses import dataclass, field
            @dataclass
            class Result:
                answer: str = ""
                sources: list = field(default_factory=list)
                tool_rounds: int = 0
                error: str = ""
            return Result(answer="今日涨停72家")

    def factory():
        return MockSession()

    route_message(
        "今天涨停多少家",
        qa_session_factory=factory,
        chat_session_manager=m,
        chat_id="save_chat",
    )
    session = m.load("save_chat")
    assert len(session["messages"]) == 2
    assert session["messages"][0]["content"] == "今天涨停多少家"
    assert session["messages"][1]["content"] == "今日涨停72家"


def test_session_memory_without_chat_id_skips(tmp_path):
    """不传 chat_id 时不触发会话记忆。"""
    from daily_review.web.chat_session import ChatSessionManager
    m = ChatSessionManager(data_dir=tmp_path)

    class MockSession:
        def __init__(self):
            self.history = []

        def answer(self, text):
            from dataclasses import dataclass, field
            @dataclass
            class Result:
                answer: str = ""
                sources: list = field(default_factory=list)
                tool_rounds: int = 0
                error: str = ""
            return Result(answer="OK")

    def factory():
        return MockSession()

    route_message(
        "test",
        qa_session_factory=factory,
        chat_session_manager=m,  # 有 manager 但无 chat_id
    )
    # 不应抛异常，不应保存
    session = m.load("unknown")
    assert session["messages"] == []


def test_session_memory_without_manager_unchanged():
    """不传 chat_session_manager 时行为不变。"""

    class MockSession:
        def __init__(self):
            self.history = []

        def answer(self, text):
            from dataclasses import dataclass, field
            @dataclass
            class Result:
                answer: str = ""
                sources: list = field(default_factory=list)
                tool_rounds: int = 0
                error: str = ""
            return Result(answer="正常回答")

    def factory():
        return MockSession()

    reply = route_message(
        "test",
        qa_session_factory=factory,
        # 不传 chat_session_manager
    )
    assert reply == "正常回答"

# ---------- 消息事件 handler 回归测试（v0.35.4） ----------


def _make_message_event(
    message_id: str = "mid_1",
    msg_type: str = "text",
    text: str = "今天涨停多少家",
    chat_id: str = "oc_test_chat",
) -> SimpleNamespace:
    """构造伪造的 P2ImMessageReceiveV1 事件对象（不依赖 lark_oapi 模型）。"""
    content = json.dumps({"text": text})
    msg = SimpleNamespace(
        message_id=message_id,
        message_type=msg_type,
        content=content,
        chat_id=chat_id,
    )
    return SimpleNamespace(event=SimpleNamespace(message=msg))


def _make_handler(token_manager=None):
    """创建不含 QA 工厂的 handler（走「简单数据查询/兜底」路由，零外部依赖）。"""
    tm = token_manager or TokenManager("test_id", "test_secret")
    return tm, _handle_p2_im_message_receive(tm)


def test_handler_text_message_replies(monkeypatch):
    """收到 text 消息 → 不抛异常 → send_text 收到回复（v0.35.1 回归：此前 L371 引用未赋值的
    text 抛 UnboundLocalError，handler 被 except 吞掉永不回复）。"""
    # 把 send_text 换成记录调用；route_message 保持真实（无 QA 工厂时走简单匹配/兜底）
    sent: list[tuple] = []

    def fake_send(tm, receive_id, text):
        sent.append((receive_id, text))
        return True

    import daily_review.web.feishu_gateway as gw

    monkeypatch.setattr(gw, "send_text", fake_send)
    tm, handler = _make_handler()
    handler(_make_message_event(message_id="mid_reg1"))
    assert len(sent) == 1
    assert sent[0][0] == "oc_test_chat"
    assert "涨停" in sent[0][1] or "无法回答" in sent[0][1]


def test_handler_cleans_at_prefix(monkeypatch):
    """@_user_xxx 前缀应在路由前被清洗掉。"""
    captured: dict = {}

    def fake_route(text, *args, **kwargs):
        captured["text"] = text
        return "ok"

    def fake_send(tm, receive_id, text):
        return True

    import daily_review.web.feishu_gateway as gw

    monkeypatch.setattr(gw, "route_message", fake_route)
    monkeypatch.setattr(gw, "send_text", fake_send)
    tm, handler = _make_handler()
    handler(_make_message_event(message_id="mid_reg2", text="@_user_101 今天涨停多少家"))
    assert captured.get("text") == "今天涨停多少家"


def test_handler_exception_sends_friendly_reply(monkeypatch):
    """route_message 抛异常 → 不回抛，且向群里回一条友好提示（v0.35.4）。"""
    def broken_route(*args, **kwargs):
        raise RuntimeError("模拟路由异常")

    sent: list[str] = []

    def fake_send(tm, receive_id, text):
        sent.append(text)
        return True

    import daily_review.web.feishu_gateway as gw

    monkeypatch.setattr(gw, "route_message", broken_route)
    monkeypatch.setattr(gw, "send_text", fake_send)
    tm, handler = _make_handler()
    handler(_make_message_event(message_id="mid_reg3"))
    assert len(sent) == 1
    assert "稍后再试" in sent[0]


# ---------- QA 异步补发（v0.35.6） ----------


class _ManualFuture:
    """手动控制的 Future：由测试决定何时完成，模拟 QA 慢/快两种路径。"""

    def __init__(self):
        self._callbacks: list = []
        self._result = None
        self._pending = True

    def add_done_callback(self, cb):
        if not self._pending:
            cb(self)
        else:
            self._callbacks.append(cb)

    def result(self, timeout=None):
        if self._pending:
            from concurrent.futures import TimeoutError as FTEO
            raise FTEO("模拟 QA 未在窗口内完成")
        return self._result

    def complete(self, result):
        self._pending = False
        self._result = result
        for cb in self._callbacks:
            cb(self)


class _ManualExecutor:
    """替换 _QA_EXECUTOR：submit 不真正执行，返回手动 Future。"""

    def __init__(self):
        self.futures: list = []

    def submit(self, fn, *args, **kwargs):
        f = _ManualFuture()
        self.futures.append(f)
        return f


def _make_result(answer):
    from dataclasses import dataclass, field

    @dataclass
    class Result:
        answer: str = ""
        sources: list = field(default_factory=list)
        tool_rounds: int = 0
        error: str = ""

    return Result(answer=answer)


def test_async_reply_slow_path(monkeypatch):
    """QA 超过短等待：立即回"数据整理中"，任务完成后异步补发真答案（不丢答案）。"""
    import daily_review.web.feishu_gateway as gw

    executor = _ManualExecutor()
    monkeypatch.setattr(gw, "_QA_EXECUTOR", executor)

    sent: list[str] = []

    def factory():
        return type("S", (), {"answer": lambda self, t: None, "history": []})()

    reply = gw.route_message(
        "今天涨停多少家",
        qa_session_factory=factory,
        async_reply_fn=sent.append,
    )
    assert "数据整理中" in reply
    assert len(executor.futures) == 1
    # 任务完成后：异步补发真答案
    executor.futures[0].complete(_make_result("QA 回答：今日涨停 82 家"))
    assert sent == ["QA 回答：今日涨停 82 家"]


def test_async_reply_fast_path(monkeypatch):
    """QA 在短等待内完成：直接返回答案，不触发异步补发。"""
    import daily_review.web.feishu_gateway as gw

    executor = _ManualExecutor()
    monkeypatch.setattr(gw, "_QA_EXECUTOR", executor)

    sent: list[str] = []
    # 覆盖 route_message 内部 fut.result(timeout=QA_FAST_WAIT) 抛 TimeoutError 前的路径：
    # 手动 executor 的 submit 不执行，这里直接让 future 在 result 前完成
    class _InstantExecutor(_ManualExecutor):
        def submit(self, fn, *args, **kwargs):
            f = super().submit(fn, *args, **kwargs)
            f.complete(_make_result("QA 回答：今日涨停 82 家"))
            return f

    monkeypatch.setattr(gw, "_QA_EXECUTOR", _InstantExecutor())

    def factory():
        return type("S", (), {"answer": lambda self, t: None, "history": []})()

    reply = gw.route_message(
        "今天涨停多少家",
        qa_session_factory=factory,
        async_reply_fn=sent.append,
    )
    assert "82 家" in reply
    assert sent == []  # 未触发异步补发


def test_async_reply_writes_session_memory(monkeypatch, tmp_path):
    """异步补发也应写会话记忆（与同步路径一致）。"""
    import daily_review.web.feishu_gateway as gw
    from daily_review.web.chat_session import ChatSessionManager

    m = ChatSessionManager(data_dir=tmp_path)
    executor = _ManualExecutor()
    monkeypatch.setattr(gw, "_QA_EXECUTOR", executor)

    sent: list[str] = []

    def factory():
        return type("S", (), {"answer": lambda self, t: None, "history": []})()

    gw.route_message(
        "今天涨停多少家",
        qa_session_factory=factory,
        chat_session_manager=m,
        chat_id="async_chat",
        async_reply_fn=sent.append,
    )
    executor.futures[0].complete(_make_result("QA 回答：今日涨停 82 家"))
    session = m.load("async_chat")
    assert len(session["messages"]) == 2
    assert session["messages"][0]["content"] == "今天涨停多少家"
    assert session["messages"][1]["content"] == "QA 回答：今日涨停 82 家"
