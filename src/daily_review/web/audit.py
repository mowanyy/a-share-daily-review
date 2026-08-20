"""SQLite 审计日志（v0.34）：消息记录、异常事件、错误日志。

使用 Python 标准库 sqlite3，零额外依赖。
存储：data/audit.db

三张表：
- messages:  所有飞书来回消息（chat_id, role, content, created_at）
- anomalies: 盘中检测到的异常事件（type, severity, message, stocks）
- errors:    Agent 运行中的错误（source, error_type, message）
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from daily_review.config import get_settings

# 每个线程的独立连接缓存（keyed by db_path）
_local = threading.local()


class AuditDB:
    """SQLite 审计日志。

    线程安全：每个线程使用独立连接（通过 db_path 区分）。
    """

    def __init__(self, db_path: Path | None = None):
        if db_path is None:
            db_path = get_settings().data_dir / "audit.db"
        self._db_path = db_path.resolve()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """获取当前线程的连接（按 db_path 缓存，autocommit 模式）。"""
        key = str(self._db_path)
        if not hasattr(_local, 'conns'):
            _local.conns = {}
        if key not in _local.conns:
            conn = sqlite3.connect(str(self._db_path))
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            _local.conns[key] = conn
        return _local.conns[key]

    def _init_db(self) -> None:
        """建表（幂等）。"""
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
                );
                CREATE TABLE IF NOT EXISTS anomalies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    message TEXT NOT NULL,
                    stocks TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
                );
                CREATE TABLE IF NOT EXISTS errors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    error_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
                );
                CREATE INDEX IF NOT EXISTS idx_messages_chat_id ON messages(chat_id);
                CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages(created_at);
                CREATE INDEX IF NOT EXISTS idx_anomalies_created_at ON anomalies(created_at);
                CREATE INDEX IF NOT EXISTS idx_errors_created_at ON errors(created_at);
            """)
        finally:
            conn.close()

    # ---------------------------------------------------------------- 日志方法

    def log_message(self, chat_id: str, role: str, content: str) -> None:
        """记录一条飞书消息。"""
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)",
            (chat_id, role, content[:2000]),  # 截断长消息防溢出
        )
        conn.commit()

    def log_anomaly(
        self,
        type_: str,
        severity: str,
        message: str,
        stocks: list[str] | None = None,
    ) -> None:
        """记录一条盘中异常事件。"""
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO anomalies (type, severity, message, stocks) VALUES (?, ?, ?, ?)",
            (type_, severity, message, json.dumps(stocks, ensure_ascii=False) if stocks else None),
        )
        conn.commit()

    def log_error(self, source: str, error_type: str, message: str) -> None:
        """记录一条错误事件。"""
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO errors (source, error_type, message) VALUES (?, ?, ?)",
            (source, error_type, message[:2000]),
        )
        conn.commit()

    # ---------------------------------------------------------------- 查询

    def recent_messages(self, chat_id: str, limit: int = 20) -> list[dict]:
        """查询最近消息记录。"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT id, role, content, created_at FROM messages "
            "WHERE chat_id=? ORDER BY id DESC LIMIT ?",
            (chat_id, limit),
        ).fetchall()
        return [
            {"id": r[0], "role": r[1], "content": r[2], "created_at": r[3]}
            for r in rows
        ]

    def recent_anomalies(self, limit: int = 50) -> list[dict]:
        """查询最近异常事件。"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT id, type, severity, message, stocks, created_at "
            "FROM anomalies ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "id": r[0], "type": r[1], "severity": r[2],
                "message": r[3], "stocks": r[4], "created_at": r[5],
            }
            for r in rows
        ]

    def recent_errors(self, limit: int = 50) -> list[dict]:
        """查询最近错误。"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT id, source, error_type, message, created_at "
            "FROM errors ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {"id": r[0], "source": r[1], "error_type": r[2], "message": r[3], "created_at": r[4]}
            for r in rows
        ]