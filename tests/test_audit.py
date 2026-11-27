"""web/audit.py SQLite 审计日志测试：消息记录、异常事件、错误日志。"""

from __future__ import annotations

from pathlib import Path

from daily_review.web.audit import AuditDB


def _db(tmp_path: Path) -> AuditDB:
    return AuditDB(db_path=tmp_path / "test_audit.db")


# ---------------------------------------------------------------- 消息


class TestMessages:
    def test_log_and_query_message(self, tmp_path):
        db = _db(tmp_path)
        db.log_message("chat_1", "user", "今天涨停多少家")
        db.log_message("chat_1", "assistant", "今日涨停68家")
        msgs = db.recent_messages("chat_1")
        assert len(msgs) == 2
        assert msgs[0]["role"] == "assistant"  # 最新一条
        assert msgs[0]["content"] == "今日涨停68家"

    def test_messages_by_chat_id(self, tmp_path):
        db = _db(tmp_path)
        db.log_message("chat_a", "user", "q1")
        db.log_message("chat_b", "user", "q2")
        msgs_a = db.recent_messages("chat_a")
        msgs_b = db.recent_messages("chat_b")
        assert len(msgs_a) == 1
        assert msgs_a[0]["content"] == "q1"
        assert len(msgs_b) == 1
        assert msgs_b[0]["content"] == "q2"

    def test_empty_chat_returns_empty(self, tmp_path):
        db = _db(tmp_path)
        msgs = db.recent_messages("nonexistent")
        assert msgs == []


# ---------------------------------------------------------------- 异常


class TestAnomalies:
    def test_log_and_query_anomaly(self, tmp_path):
        db = _db(tmp_path)
        db.log_anomaly("炸板潮", "warning", "5只炸板", ["A", "B", "C", "D", "E"])
        db.log_anomaly("龙头异动", "alert", "空间板炸板", ["KING"])
        anoms = db.recent_anomalies()
        assert len(anoms) == 2
        assert anoms[0]["type"] == "龙头异动"
        assert anoms[0]["severity"] == "alert"

    def test_anomaly_without_stocks(self, tmp_path):
        db = _db(tmp_path)
        db.log_anomaly("情绪骤变", "warning", "涨停数骤降50%")
        anoms = db.recent_anomalies()
        assert len(anoms) == 1
        assert anoms[0]["stocks"] is None

    def test_empty_anomalies_returns_empty(self, tmp_path):
        db = _db(tmp_path)
        assert db.recent_anomalies() == []


# ---------------------------------------------------------------- 错误


class TestErrors:
    def test_log_and_query_error(self, tmp_path):
        db = _db(tmp_path)
        db.log_error("gateway", "TimeoutError", "QA 超时")
        db.log_error("daemon", "ConnectionError", "网络异常")
        errs = db.recent_errors()
        assert len(errs) == 2
        assert errs[0]["source"] == "daemon"
        assert errs[0]["error_type"] == "ConnectionError"

    def test_empty_errors_returns_empty(self, tmp_path):
        db = _db(tmp_path)
        assert db.recent_errors() == []


# ---------------------------------------------------------------- 多线程


class TestConcurrency:
    def test_different_threads_safe(self, tmp_path):
        """不同线程使用独立连接，不会冲突。"""
        import threading

        db = _db(tmp_path)
        results: list[int] = []

        def _worker(n: int):
            db.log_message(f"thread_{n}", "user", f"msg_{n}")
            msgs = db.recent_messages(f"thread_{n}")
            results.append(len(msgs))

        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert all(r == 1 for r in results)


# ---------------------------------------------------------------- 初始化幂等


class TestInit:
    def test_init_idempotent(self, tmp_path):
        """多次初始化不会报错（表已存在）。"""
        db_path = tmp_path / "audit.db"
        db1 = AuditDB(db_path=db_path)
        db1.log_message("c", "user", "test")
        db2 = AuditDB(db_path=db_path)  # 再次初始化
        db2.log_message("c", "user", "test2")
        msgs = db2.recent_messages("c")
        assert len(msgs) == 2


# ---------------------------------------------------------------- Trace（v0.35）


class TestTraces:
    def test_log_and_query_trace(self, tmp_path):
        db = _db(tmp_path)
        trace_json = '{"question":"分析今日市场","tool_calls":[{"tool":"query_zt_pool","duration_ms":320}],"total_rounds":1,"total_duration_ms":320}'
        db.log_trace("web", "分析今日市场", trace_json)
        traces = db.recent_traces()
        assert len(traces) == 1
        assert traces[0]["question"] == "分析今日市场"
        assert traces[0]["chat_id"] == "web"
        assert "query_zt_pool" in traces[0]["trace_json"]

    def test_traces_by_chat_id(self, tmp_path):
        db = _db(tmp_path)
        db.log_trace("chat_a", "q1", '{"rounds":1}')
        db.log_trace("chat_b", "q2", '{"rounds":2}')
        traces = db.recent_traces()
        assert len(traces) == 2

    def test_trace_question_truncated(self, tmp_path):
        """长问题截断到 500 字符，不报错。"""
        db = _db(tmp_path)
        long_q = "x" * 1000
        db.log_trace("web", long_q, '{"rounds":1}')
        traces = db.recent_traces()
        assert len(traces) == 1
        assert len(traces[0]["question"]) <= 500

    def test_empty_traces_returns_empty(self, tmp_path):
        db = _db(tmp_path)
        assert db.recent_traces() == []


class TestChatIds:
    def test_list_chat_ids(self, tmp_path):
        db = _db(tmp_path)
        db.log_message("chat_a", "user", "hi")
        db.log_message("chat_b", "user", "hello")
        ids = db.list_chat_ids()
        assert sorted(ids) == ["chat_a", "chat_b"]

    def test_empty_chat_ids(self, tmp_path):
        db = _db(tmp_path)
        assert db.list_chat_ids() == []