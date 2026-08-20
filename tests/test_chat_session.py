"""web/chat_session.py 多轮对话记忆测试：读写、裁剪、清空。"""

from __future__ import annotations

from pathlib import Path

from daily_review.web.chat_session import ChatSessionManager


def _manager(tmp_path: Path) -> ChatSessionManager:
    return ChatSessionManager(data_dir=tmp_path)


# ---------------------------------------------------------------- 基础


class TestLoadSave:
    def test_load_new_session_returns_empty(self, tmp_path):
        m = _manager(tmp_path)
        session = m.load("test_chat")
        assert session["chat_id"] == "test_chat"
        assert session["messages"] == []

    def test_save_and_load_roundtrip(self, tmp_path):
        m = _manager(tmp_path)
        m.add_turn("test_chat", "你好", "你好！有什么可以帮你的？")
        session = m.load("test_chat")
        assert len(session["messages"]) == 2
        assert session["messages"][0]["role"] == "user"
        assert session["messages"][0]["content"] == "你好"
        assert session["messages"][1]["role"] == "assistant"
        assert session["messages"][1]["content"] == "你好！有什么可以帮你的？"

    def test_multiple_chats_isolated(self, tmp_path):
        m = _manager(tmp_path)
        m.add_turn("chat_a", "q1", "a1")
        m.add_turn("chat_b", "q2", "a2")
        sa = m.load("chat_a")
        sb = m.load("chat_b")
        assert len(sa["messages"]) == 2
        assert sa["messages"][0]["content"] == "q1"
        assert len(sb["messages"]) == 2
        assert sb["messages"][0]["content"] == "q2"


# ---------------------------------------------------------------- 裁剪


class TestTrim:
    def test_trim_to_max_rounds(self, tmp_path):
        """超过 10 轮后自动裁剪为最近 10 轮。"""
        m = _manager(tmp_path)
        for i in range(12):
            m.add_turn("trim_chat", f"q{i}", f"a{i}")
        # 每轮 2 条消息，10 轮 = 20 条
        session = m.load("trim_chat")
        assert len(session["messages"]) == 20
        # 第一条应该是第 3 轮（q2/a2）
        assert session["messages"][0]["content"] == "q2"

    def test_under_limit_not_trimmed(self, tmp_path):
        """5 轮不裁剪。"""
        m = _manager(tmp_path)
        for i in range(5):
            m.add_turn("light_chat", f"q{i}", f"a{i}")
        session = m.load("light_chat")
        assert len(session["messages"]) == 10


# ---------------------------------------------------------------- 历史注入


class TestHistory:
    def test_get_history_returns_recent(self, tmp_path):
        m = _manager(tmp_path)
        for i in range(5):
            m.add_turn("hist_chat", f"q{i}", f"a{i}")
        history = m.get_history("hist_chat")
        assert len(history) == 10

    def test_get_history_with_max_rounds(self, tmp_path):
        m = _manager(tmp_path)
        for i in range(5):
            m.add_turn("hist_chat2", f"q{i}", f"a{i}")
        history = m.get_history("hist_chat2", max_rounds=2)
        assert len(history) == 4  # 2 轮 = 4 条
        assert history[0]["content"] == "q3"

    def test_get_history_empty(self, tmp_path):
        m = _manager(tmp_path)
        history = m.get_history("empty_chat")
        assert history == []


# ---------------------------------------------------------------- 清空


class TestClear:
    def test_clear_removes_file(self, tmp_path):
        m = _manager(tmp_path)
        m.add_turn("clear_chat", "q", "a")
        assert m.load("clear_chat")["messages"]
        m.clear("clear_chat")
        session = m.load("clear_chat")
        assert session["messages"] == []

    def test_clear_nonexistent_does_not_error(self, tmp_path):
        m = _manager(tmp_path)
        m.clear("nonexistent")  # 不应抛异常


# ---------------------------------------------------------------- 摘要


class TestSummary:
    def test_get_summary(self, tmp_path):
        m = _manager(tmp_path)
        m.add_turn("sum_chat", "q1", "a1")
        m.add_turn("sum_chat", "q2", "a2")
        summary = m.get_summary("sum_chat")
        assert summary["chat_id"] == "sum_chat"
        assert summary["rounds"] == 2
        assert summary["updated_at"] != ""

    def test_get_summary_empty(self, tmp_path):
        m = _manager(tmp_path)
        summary = m.get_summary("empty_sum")
        assert summary["rounds"] == 0