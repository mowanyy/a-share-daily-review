"""飞书群聊会话记忆（v0.34）：复用 fund_sessions JSON 持久化模式。

每个 chat_id 一个 JSON 文件，存储最近 10 轮对话历史。
启动时注入到 QASession，使 Agent 能感知前文，实现多轮连贯对话。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from daily_review.config import get_settings

# 保留最近对话轮数
_MAX_ROUNDS = 10


class ChatSessionManager:
    """群聊会话管理器：每个 chat_id 独立文件，自动裁剪。

    存储：data/chat_sessions/{chat_id}.json
    格式：
        {
            "chat_id": "oc_xxx",
            "messages": [
                {"role": "user", "content": "...", "timestamp": "..."},
                {"role": "assistant", "content": "...", "timestamp": "..."},
                ...
            ],
            "updated_at": "..."
        }
    """

    def __init__(self, data_dir: Path | None = None):
        if data_dir is None:
            data_dir = get_settings().data_dir
        self._sessions_dir = data_dir / "chat_sessions"
        self._sessions_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, chat_id: str) -> Path:
        return self._sessions_dir / f"{chat_id}.json"

    # ---------------------------------------------------------------- 公共接口

    def load(self, chat_id: str) -> dict:
        """加载或创建 chat_id 的会话。"""
        path = self._path(chat_id)
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        return {"chat_id": chat_id, "messages": [], "updated_at": ""}

    def save(self, session: dict) -> None:
        """保存会话（原子写 + 自动裁剪）。"""
        session["updated_at"] = datetime.now().isoformat(timespec="seconds")
        # 裁剪到最近 N 轮（每轮 2 条消息）
        max_msgs = _MAX_ROUNDS * 2
        if len(session["messages"]) > max_msgs:
            session["messages"] = session["messages"][-max_msgs:]
        # 原子写
        path = self._path(session["chat_id"])
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(session, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)

    def add_turn(self, chat_id: str, question: str, answer: str) -> None:
        """添加一轮问答并保存。"""
        now = datetime.now().isoformat(timespec="seconds")
        session = self.load(chat_id)
        session["messages"].append({"role": "user", "content": question, "timestamp": now})
        session["messages"].append({"role": "assistant", "content": answer, "timestamp": now})
        self.save(session)

    def clear(self, chat_id: str) -> None:
        """清空会话（删除文件）。"""
        self._path(chat_id).unlink(missing_ok=True)

    def get_history(self, chat_id: str, max_rounds: int = _MAX_ROUNDS) -> list[dict]:
        """获取最近 N 轮对话历史（用于注入 QASession）。"""
        session = self.load(chat_id)
        messages = session.get("messages", [])
        return messages[-(max_rounds * 2):]

    def get_summary(self, chat_id: str) -> dict:
        """获取会话摘要（不含完整消息内容，用于 API 展示）。"""
        session = self.load(chat_id)
        rounds = len(session.get("messages", [])) // 2
        return {
            "chat_id": chat_id,
            "rounds": rounds,
            "updated_at": session.get("updated_at", ""),
        }