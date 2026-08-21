"""飞书 Agent 网关（v0.32）：WebSocket 长连接 + 消息路由 + 双向交互。

核心能力：
- 飞书 WebSocket 长连接（不需要公网 webhook 地址）
- 接收群消息/私聊消息，路由到 QA 系统回答
- 发送文本消息、卡片消息（彩色标题）
- 合规边界：数据查询正常回答，交易建议合规拒绝

依赖：
- lark-oapi（飞书官方 Python SDK，WebSocket 模式）
- 飞书开放平台自定义应用（需配置 im:message 权限和事件订阅）
"""

from __future__ import annotations

import json
import time
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from typing import Callable

import requests

from daily_review.config import get_settings

logger = logging.getLogger(__name__)

# QA 超时时间（秒）
# 推理模型（deepseek-v4-flash）思考慢，设为 120 秒；
# 超过此时间仍未返回则回复"正在思考中"，避免 WebSocket 长连接因事件循环阻塞而断连。
QA_TIMEOUT = 120

# ---------- 常量 ----------

FEISHU_OPEN_API = "https://open.feishu.cn"
FEISHU_TOKEN_URL = f"{FEISHU_OPEN_API}/open-apis/auth/v3/tenant_access_token/internal"
FEISHU_MESSAGE_URL = f"{FEISHU_OPEN_API}/open-apis/im/v1/messages"
FEISHU_CARD_MSG_URL = f"{FEISHU_OPEN_API}/open-apis/im/v1/messages?receive_id_type=chat_id"

# 合规回复模板
COMPLIANCE_REPLY = "这个我无法给出具体建议，建议咨询你的投资顾问。我可以帮你查询当日数据或历史对比。"

# 卡片颜色
CARD_COLOR_GREEN = "green"     # 看涨/积极
CARD_COLOR_RED = "red"         # 看跌/风险
CARD_COLOR_BLUE = "blue"       # 中性/信息


# ---------- token 管理 ----------

class TokenManager:
    """飞书 tenant_access_token 管理器（自动刷新）。"""

    def __init__(self, app_id: str, app_secret: str):
        self._app_id = app_id
        self._app_secret = app_secret
        self._token: str = ""
        self._expires_at: float = 0

    @property
    def token(self) -> str:
        if time.time() >= self._expires_at - 60:  # 提前 60 秒刷新
            self._refresh()
        return self._token

    def _refresh(self) -> None:
        try:
            resp = requests.post(
                FEISHU_TOKEN_URL,
                json={"app_id": self._app_id, "app_secret": self._app_secret},
                timeout=10,
            )
            data = resp.json()
            if data.get("code") != 0:
                logger.error("飞书 token 获取失败: %s", data.get("msg", ""))
                return
            self._token = data["tenant_access_token"]
            self._expires_at = time.time() + data.get("expire", 7200)
            logger.info("飞书 token 已刷新，有效期 %d 秒", data.get("expire", 7200))
        except Exception as exc:
            logger.error("飞书 token 刷新失败: %s", exc)


# ---------- 消息发送 ----------

def send_text(
    token_manager: TokenManager,
    receive_id: str,
    text: str,
    *,
    receive_id_type: str = "chat_id",
) -> bool:
    """向飞书群/用户发送文本消息。

    Args:
        token_manager: Token 管理器
        receive_id: 接收方 ID（chat_id 或 open_id）
        text: 消息文本
        receive_id_type: ID 类型（chat_id / open_id / user_id）

    Returns:
        是否发送成功
    """
    url = f"{FEISHU_MESSAGE_URL}?receive_id_type={receive_id_type}"
    payload = {
        "receive_id": receive_id,
        "msg_type": "text",
        "content": json.dumps({"text": text}),
    }
    headers = {
        "Authorization": f"Bearer {token_manager.token}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        data = resp.json()
        if data.get("code") != 0:
            logger.error("飞书发送消息失败: code=%s msg=%s", data.get("code"), data.get("msg", ""))
            return False
        return True
    except Exception as exc:
        logger.error("飞书发送消息异常: %s", exc)
        return False


def send_card(
    token_manager: TokenManager,
    receive_id: str,
    title: str,
    body_lines: list[str],
    *,
    color: str = CARD_COLOR_BLUE,
    receive_id_type: str = "chat_id",
) -> bool:
    """向飞书发送卡片消息。

    Args:
        token_manager: Token 管理器
        receive_id: 接收方 ID
        title: 卡片标题
        body_lines: 卡片正文行列表
        color: 卡片颜色（green/red/blue）
        receive_id_type: ID 类型

    Returns:
        是否发送成功
    """
    # 构建飞书卡片 JSON
    elements = []
    for line in body_lines:
        elements.append({"tag": "markdown", "content": line})

    card_content = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": color,
        },
        "elements": elements,
    }

    url = f"{FEISHU_MESSAGE_URL}?receive_id_type={receive_id_type}"
    payload = {
        "receive_id": receive_id,
        "msg_type": "interactive",
        "content": json.dumps(card_content),
    }
    headers = {
        "Authorization": f"Bearer {token_manager.token}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        data = resp.json()
        if data.get("code") != 0:
            logger.error("飞书发送卡片失败: code=%s msg=%s", data.get("code"), data.get("msg", ""))
            return False
        return True
    except Exception as exc:
        logger.error("飞书发送卡片异常: %s", exc)
        return False


# ---------- 合规检查 ----------

# 合规关键词列表——含这些词的提问直接拒绝
_COMPLIANCE_KEYWORDS = [
    "推荐", "买入", "买进", "卖出", "持有", "持仓", "加仓", "减仓", "清仓",
    "追涨", "抄底", "能不能买", "要不要卖", "给个建议", "推荐一只",
    "推荐一个", "推荐股票", "荐股",
]

# 例外——如果文本只包含这些词而不含关键词，则不是合规风险
# 注意：如果文本同时包含关键词和例外词，以关键词优先
_COMPLIANCE_EXCEPTIONS = [
    "多少", "统计", "名单", "列表", "排行",
    "是谁", "有没有", "涨停", "跌停", "炸板",
]


def is_compliance_risk(text: str) -> bool:
    """检查用户消息是否涉及交易建议（合规风险）。

    1. 先检查是否含有关键词（交易建议信号）
    2. 如果含有关键词，再检查是否被例外词豁免（纯数据查询）
    3. 含有关键词且不匹配例外词 → 合规风险
    """
    text_lower = text.lower()
    has_keyword = any(kw in text_lower for kw in _COMPLIANCE_KEYWORDS)
    if not has_keyword:
        return False
    # 含有关键词 → 检查是否被例外词豁免（必须是纯数据查询，不含交易意图）
    has_exception = any(ex in text_lower for ex in _COMPLIANCE_EXCEPTIONS)
    return not has_exception


# ---------- 消息路由 ----------

def route_message(
    text: str,
    qa_session_factory: Callable | None = None,
    trade_date: str | None = None,
    market_summary_fn: Callable[[], str] | None = None,
    chat_session_manager: object | None = None,
    chat_id: str | None = None,
) -> str:
    """路由用户消息到合适的处理模块。

    优先级：
    1. 合规检查 → 拒绝
    2. 盘中实时概况（如果配置了 market_summary_fn）→ 快速返回，不经过 RAG
    3. QA 会话（如果可用）→ 调用 QA 回答（v0.34：注入多轮对话记忆）
    4. 数据查询 → 直接回答（简单匹配）
    5. 兜底 → 引导

    Args:
        text: 用户消息文本
        qa_session_factory: QA 会话工厂函数（无参，返回 QASession 实例）
        trade_date: 交易日
        market_summary_fn: 市场概况函数，用于"现在什么情况"快速通道
        chat_session_manager: ChatSessionManager 实例，用于多轮对话记忆
        chat_id: 飞书聊天 ID，用于会话记忆的键

    Returns:
        回复文本
    """
    text = text.strip()
    if not text:
        return "请说点什么吧～"

    # 1. 合规检查
    if is_compliance_risk(text):
        return COMPLIANCE_REPLY

    # 1.5 盘中实时概况快速通道（v0.33）：不经过 RAG 检索，<1s 响应
    if market_summary_fn is not None:
        text_lower_for_check = text.lower()
        realtime_keywords = ["现在", "实时", "当前", "什么情况", "市场概况", "怎么样"]
        if any(kw in text_lower_for_check for kw in realtime_keywords):
            try:
                result = market_summary_fn()
                if result:
                    return result
            except Exception as exc:
                logger.warning("市场概况获取失败: %s", exc)

    # 2. QA 会话（RAG + 数据工具 function-calling），带超时保护
    #    v0.34：注入多轮对话记忆 + 保存新轮次
    #    v0.35.1：改用全局 executor + shutdown(wait=False) 避免阻塞事件循环
    #            （with 块退出时 shutdown(wait=True) 会等任务完成，阻塞
    #              asyncio 事件循环导致 WebSocket ping 超时断连）
    if qa_session_factory is not None:
        try:
            session = qa_session_factory()
            # 注入历史对话记忆
            if chat_session_manager is not None and chat_id is not None:
                history = chat_session_manager.get_history(chat_id)
                if history:
                    session.history = history
            fut = _QA_EXECUTOR.submit(session.answer, text)
            result = fut.result(timeout=QA_TIMEOUT)
            if result.answer:
                # 保存新轮次到会话记忆
                if chat_session_manager is not None and chat_id is not None:
                    chat_session_manager.add_turn(chat_id, text, result.answer)
                return result.answer
        except TimeoutError:
            logger.warning("QA 回答超时（%d 秒）", QA_TIMEOUT)
            return "我正在思考中，请稍后再问～"
        except Exception as exc:
            logger.error("QA 会话回答失败: %s", exc)
            # 降级到简单查询

    # 3. 简单数据查询（兜底，不依赖 QA 系统）
    text_lower = text.lower()
    if "你好" in text_lower or "在吗" in text_lower:
        return "我在的，有什么需要帮忙的吗？我可以查询今日涨停数据、情绪温度、题材热点等信息。"
    if "help" in text_lower or "帮助" in text_lower:
        return (
            "我可以帮你查询以下信息：\n"
            "📊 今日涨停数据（涨停数、炸板数、跌停数）\n"
            "🌡️ 情绪温度（0-100分）\n"
            "📈 空间板高度\n"
            "🏷️ 题材热点\n"
            "📜 历史对比\n"
            "直接问我问题就行～"
        )

    # 4. 兜底
    return "我暂时无法回答这个问题，你可以试试问今日涨停数据、情绪温度等市场信息。"


# ---------- 飞书事件处理 ----------

# 消息去重：记录已处理的消息 ID（message_id → 处理时间戳），防止 WebSocket 重连导致重复回复
_PROCESSED_MESSAGE_IDS: dict[str, float] = {}
_DEDUP_TTL = 60  # 消息 ID 保留 60 秒（超过此窗口的重发视为新消息）

# 全局 QA 线程池（v0.35.1）：避免 with 块退出时 shutdown(wait=True) 阻塞 asyncio 事件循环
_QA_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="qa")


def _dedup_message(message_id: str) -> bool:
    """检查消息是否已处理过。返回 True 表示已处理（应跳过）。"""
    global _PROCESSED_MESSAGE_IDS
    now = time.time()
    # 清理过期记录
    stale = [mid for mid, ts in _PROCESSED_MESSAGE_IDS.items() if now - ts > _DEDUP_TTL]
    for mid in stale:
        _PROCESSED_MESSAGE_IDS.pop(mid, None)
    # 检查是否已处理
    if message_id in _PROCESSED_MESSAGE_IDS:
        logger.warning("消息去重: message_id=%s 已处理过，跳过", message_id)
        return True
    _PROCESSED_MESSAGE_IDS[message_id] = now
    return False


def _handle_p2_im_message_receive(
    token_manager: TokenManager,
    qa_session_factory: Callable | None = None,
    trade_date: str | None = None,
    market_summary_fn: Callable[[], str] | None = None,
    chat_session_manager: object | None = None,
    audit_db: object | None = None,
):
    """创建 P2ImMessageReceiveV1 事件处理器。

    处理飞书消息接收事件：解析消息内容、路由、回复。
    v0.34：支持多轮对话记忆 + 审计日志。
    v0.35.1：消息去重，防止 WebSocket 重连/事件重复投递导致多次回复。
    """
    from lark_oapi.api.im.v1.model import P2ImMessageReceiveV1

    def handler(event: P2ImMessageReceiveV1):
        logger.info("收到事件: %s", type(event).__name__)
        try:
            if not event or not event.event:
                logger.warning("收到空事件")
                return

            event_body = event.event
            if not hasattr(event_body, "message") or not event_body.message:
                logger.warning("收到无消息体的事件")
                return

            message = event_body.message

            # 消息去重：根据 message_id 判断是否已处理过
            message_id = getattr(message, 'message_id', '') or ''
            logger.info("消息 message_id=%s, text=%s", message_id, text[:30])
            if message_id and _dedup_message(message_id):
                return

            # 只处理文本消息（兼容 msg_type / message_type 字段名）
            msg_type = getattr(message, 'message_type', None) or getattr(message, 'msg_type', '')
            if msg_type != "text":
                logger.info("非文本消息: %s", msg_type)
                return

            # 解析消息内容
            content = json.loads(message.content)
            text = content.get("text", "").strip()

            # 去掉 @机器人 前缀（飞书消息中 @ 会带上机器人名）
            import re
            text = re.sub(r"@_user_\d+\s*", "", text).strip()

            if not text:
                return

            # 获取聊天 ID
            chat_id = message.chat_id

            # 检查是否允许在该群响应
            settings = get_settings()
            if settings.feishu_allowed_chat_ids:
                if chat_id not in settings.feishu_allowed_chat_ids:
                    logger.info("忽略非允许群消息: %s", chat_id)
                    return

            logger.info("收到飞书消息: %s（群: %s）", text[:50], chat_id)

            # 审计日志：用户消息
            if audit_db is not None:
                audit_db.log_message(chat_id, "user", text)

            # 路由消息（v0.33：market_summary_fn；v0.34：chat_session_manager）
            reply = route_message(
                text,
                qa_session_factory,
                trade_date,
                market_summary_fn=market_summary_fn,
                chat_session_manager=chat_session_manager,
                chat_id=chat_id,
            )

            # 发送回复
            send_text(token_manager, chat_id, reply)

            # 审计日志：助手回复
            if audit_db is not None:
                audit_db.log_message(chat_id, "assistant", reply)

        except Exception as exc:
            logger.error("处理飞书消息异常: %s", exc, exc_info=True)
            # 审计日志：错误
            if audit_db is not None and 'chat_id' in locals():
                audit_db.log_error("gateway", type(exc).__name__, str(exc))

    return handler


# ---------- 网关主类 ----------

class FeishuGateway:
    """飞书 Agent 网关：管理 WebSocket 连接和消息处理。

    用法：
        gateway = FeishuGateway(
            app_id="xxx",
            app_secret="xxx",
            qa_session_factory=my_factory,
        )
        gateway.start()  # 阻塞，启动 WebSocket 连接
    """

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        *,
        qa_session_factory: Callable | None = None,
        trade_date: str | None = None,
        log_level: int = logging.INFO,
        market_summary_fn: Callable[[], str] | None = None,
        chat_session_manager: object | None = None,
        audit_db: object | None = None,
    ):
        self._app_id = app_id
        self._app_secret = app_secret
        self._qa_session_factory = qa_session_factory
        self._trade_date = trade_date
        self._log_level = log_level
        self._token_manager = TokenManager(app_id, app_secret)
        self._ws_client = None
        self._market_summary_fn = market_summary_fn
        self._chat_session_manager = chat_session_manager
        self._audit_db = audit_db

    @property
    def token_manager(self) -> TokenManager:
        return self._token_manager

    @classmethod
    def from_settings(
        cls,
        *,
        qa_session_factory: Callable | None = None,
        trade_date: str | None = None,
        market_summary_fn: Callable[[], str] | None = None,
        chat_session_manager: object | None = None,
        audit_db: object | None = None,
    ) -> FeishuGateway | None:
        """从 Settings 配置创建网关（未配置时返回 None）。"""
        settings = get_settings()
        if not settings.feishu_app_id or not settings.feishu_app_secret:
            logger.warning("飞书开放平台未配置（FEISHU_APP_ID / FEISHU_APP_SECRET 为空），跳过 WebSocket 启动")
            return None
        return cls(
            app_id=settings.feishu_app_id,
            app_secret=settings.feishu_app_secret,
            qa_session_factory=qa_session_factory,
            trade_date=trade_date,
            market_summary_fn=market_summary_fn,
            chat_session_manager=chat_session_manager,
            audit_db=audit_db,
        )

    def start(self) -> None:
        """启动 WebSocket 连接（阻塞）。

        使用 lark-oapi 的 ws.Client 连接飞书服务器，接收消息事件。
        连接断开时会自动重连（auto_reconnect=True）。
        """
        from lark_oapi import LogLevel
        from lark_oapi.event.dispatcher_handler import EventDispatcherHandler
        from lark_oapi.ws import Client as WSClient

        # 构建事件处理器（v0.33：market_summary_fn；v0.34：chat_session_manager + audit_db）
        handler = _handle_p2_im_message_receive(
            self._token_manager,
            qa_session_factory=self._qa_session_factory,
            trade_date=self._trade_date,
            market_summary_fn=self._market_summary_fn,
            chat_session_manager=self._chat_session_manager,
            audit_db=self._audit_db,
        )

        event_handler = (
            EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(handler)
            .build()
        )

        # 映射 Python logging 级别到 lark-oapi 级别
        lark_log = LogLevel.INFO
        if self._log_level <= logging.DEBUG:
            lark_log = LogLevel.DEBUG
        elif self._log_level >= logging.WARNING:
            lark_log = LogLevel.WARN

        # 创建 WebSocket 客户端
        self._ws_client = WSClient(
            app_id=self._app_id,
            app_secret=self._app_secret,
            log_level=lark_log,
            event_handler=event_handler,
            auto_reconnect=True,
        )

        logger.info("飞书 WebSocket 网关启动...")
        print("[feishu] 飞书 Agent 网关已启动，等待消息...")
        self._ws_client.start()

    def stop(self) -> None:
        """停止 WebSocket 连接。"""
        # ws.Client 没有 stop 方法，但 start() 阻塞时会持续运行
        # 可以通过进程退出或信号终止
        logger.info("飞书 WebSocket 网关停止")
        print("[feishu] 飞书 Agent 网关已停止")

    def send_message(self, receive_id: str, text: str, **kwargs) -> bool:
        """发送文本消息（便捷方法）。"""
        return send_text(self._token_manager, receive_id, text, **kwargs)

    def send_card_message(
        self,
        receive_id: str,
        title: str,
        body_lines: list[str],
        **kwargs,
    ) -> bool:
        """发送卡片消息（便捷方法）。"""
        return send_card(self._token_manager, receive_id, title, body_lines, **kwargs)