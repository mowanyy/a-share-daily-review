"""盘中监控 Daemon（v0.33）：常驻 WebSocket 网关 + 定时轮询 + 异常推送。

在 FeishuGateway 的基础上，新增：
- 每 N 秒拉取涨停池/炸板池，与基准 diff
- 异常检测（炸板潮/题材爆发/龙头异动/情绪骤变）
- 异常时主动推飞书卡片消息
- 盘中实时概况（"现在什么情况"快速通道）

用法（CLI）：
    python -m daily_review agent --daemon [--poll-interval 300]
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Callable

from daily_review.analysis.intraday import (
    load_snapshots,
    snapshot,
    take_baseline,
)
from daily_review.config import get_settings
from daily_review.data import eastmoney_pool
from daily_review.web.feishu_gateway import FeishuGateway
from daily_review.web.monitor import Anomaly, detect_anomalies

logger = logging.getLogger(__name__)

_SHANGHAI = "Asia/Shanghai"


class MarketDaemon:
    """盘中监控 Daemon：WebSocket 长连接 + 定时轮询 + 异常推送。

    启动后同时运行：
    1. FeishuGateway 的 WebSocket 监听（被动响应群消息）
    2. 定时轮询线程（主动拉取数据 + 异常检测 + 推送）
    """

    def __init__(
        self,
        gateway: FeishuGateway,
        *,
        trade_date: str,
        home_channel: str = "",
        poll_interval: int = 300,
        qa_session_factory: Callable | None = None,
        audit_db: object | None = None,
    ):
        self._gateway = gateway
        self._trade_date = trade_date
        self._home_channel = home_channel
        self._poll_interval = max(poll_interval, 60)  # 最小 60 秒
        self._qa_session_factory = qa_session_factory
        self._audit_db = audit_db

        self._running = False
        self._poll_thread: threading.Thread | None = None
        self._baseline: dict | None = None
        self._prev_delta: dict | None = None
        self._space_board: dict | None = None
        self._industry_map: dict[str, str] | None = None

    # ---------------------------------------------------------------- 公共接口

    def start(self) -> None:
        """启动轮询线程 + WebSocket 网关（阻塞，直到 Ctrl+C）。"""
        self._running = True
        logger.info(
            "MarketDaemon 启动: 交易日=%s, 轮询间隔=%ds",
            self._trade_date,
            self._poll_interval,
        )

        # 启动轮询线程
        self._poll_thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name="market-daemon-poll",
        )
        self._poll_thread.start()

        # 启动 WebSocket 网关（阻塞）
        self._gateway.start()

    def stop(self) -> None:
        """停止 Daemon。"""
        self._running = False
        self._gateway.stop()
        logger.info("MarketDaemon 已停止")

    def get_market_summary(self) -> str:
        """获取当前市场概况（用于"现在什么情况"快速通道）。

        返回格式化的摘要字符串，不经过 RAG 检索，<1s 响应。
        """
        try:
            zt = eastmoney_pool.fetch_zt_pool(self._trade_date)
            zb = eastmoney_pool.fetch_zb_pool(self._trade_date)
            records = load_snapshots(self._trade_date)
        except Exception as exc:
            logger.warning("获取市场概况数据失败: %s", exc)
            return "📊 数据获取中，请稍后再试"

        zt_count = len(zt) if zt is not None else 0
        zb_count = len(zb) if zb is not None else 0

        # 统计累计变化
        cum_new: set[str] = set()
        cum_broken: set[str] = set()
        cum_re_sealed: set[str] = set()
        for r in records:
            cum_new.update(r.get("new_zt", []))
            cum_broken.update(r.get("broken", []))
            cum_re_sealed.update(r.get("re_sealed", []))

        # 空间板
        space_line = ""
        if zt is not None and not zt.empty:
            max_lb = int(zt["lb_num"].max())
            top = zt[zt["lb_num"] == max_lb]
            names = [f"{r['name']}({r['code']})" for _, r in top.iterrows()]
            space_line = f"空间板: {max_lb}连板 ({', '.join(names)})\n"

        # 基准信息
        base_line = ""
        if self._baseline:
            base_zt = self._baseline.get("zt_count", 0)
            base_zb = self._baseline.get("zb_count", 0)
            base_line = f"基准: {base_zt}涨停 / {base_zb}炸板\n"

        now_str = datetime.now().strftime("%H:%M")
        return (
            f"📊 盘中实时概况（{now_str}）\n"
            f"{base_line}"
            f"当前: 涨停 {zt_count} 家 | 炸板 {zb_count} 家\n"
            f"{space_line}"
            f"盘中变化: +{len(cum_new)} 新涨停 / -{len(cum_broken)} 炸板 / ↩{len(cum_re_sealed)} 回封"
        )

    # ---------------------------------------------------------------- 轮询

    def _poll_loop(self) -> None:
        """定时轮询主循环。"""
        logger.info("轮询线程已启动")

        # 首次轮询：建立基准
        try:
            self._first_poll()
        except Exception as exc:
            logger.error("首次轮询失败: %s", exc, exc_info=True)

        # 后续轮询
        while self._running:
            try:
                self._poll_once()
            except Exception as exc:
                logger.error("轮询异常: %s", exc, exc_info=True)
            time.sleep(self._poll_interval)

    def _first_poll(self) -> None:
        """首次轮询：建立基准快照 + 识别空间板 + 加载行业映射。"""
        logger.info("建立盘中基准快照...")

        # 1. 取基准（强制刷新）
        self._baseline = take_baseline(self._trade_date, force=True)
        logger.info(
            "基准已建立: %d 涨停, %d 炸板",
            self._baseline.get("zt_count", 0),
            self._baseline.get("zb_count", 0),
        )

        # 2. 识别空间板（最高连板股）
        self._space_board = self._find_space_board()
        if self._space_board:
            logger.info(
                "空间板: %s（%d 连板）",
                self._space_board.get("code", ""),
                self._space_board.get("lb_num", 0),
            )

        # 3. 加载行业映射（用于题材爆发检测）
        try:
            self._industry_map = eastmoney_pool.fetch_stock_industry_map()
            logger.info("行业映射已加载: %d 只股票", len(self._industry_map))
        except Exception as exc:
            logger.warning("行业映射加载失败，题材爆发检测将跳过: %s", exc)
            self._industry_map = None

        # 首次轮询不检测异常（无对比基准）

    def _poll_once(self) -> None:
        """单次轮询：拉取快照 → 检测异常 → 推送。"""
        delta = snapshot(self._trade_date)
        if not delta:
            logger.warning("轮询快照为空，跳过")
            return

        anomalies = detect_anomalies(
            baseline=self._baseline or {},
            current_delta=delta,
            prev_delta=self._prev_delta,
            space_board=self._space_board,
            industry_map=self._industry_map,
        )

        for anomaly in anomalies:
            self._push_anomaly(anomaly)

        self._prev_delta = delta

    # ---------------------------------------------------------------- 辅助

    def _find_space_board(self) -> dict | None:
        """从涨停池中找出空间板（最高连板股）。

        返回 {"code": str, "lb_num": int}，无涨停池时返回 None。
        """
        try:
            zt = eastmoney_pool.fetch_zt_pool(self._trade_date)
            if zt is None or zt.empty:
                return None
            max_lb = int(zt["lb_num"].max())
            if max_lb <= 0:
                return None
            top = zt[zt["lb_num"] == max_lb].iloc[0]
            return {"code": str(top["code"]), "lb_num": int(top["lb_num"])}
        except Exception as exc:
            logger.warning("识别空间板失败: %s", exc)
            return None

    def _push_anomaly(self, anomaly: Anomaly) -> None:
        """通过飞书推送异常卡片消息。"""
        channel = self._home_channel or get_settings().feishu_home_channel
        if not channel:
            logger.warning("未配置推送群（FEISHU_HOME_CHANNEL），跳过推送: %s", anomaly.type)
            return

        # 构建卡片正文
        body_lines = [anomaly.message]
        if anomaly.stocks:
            body_lines.append(f"涉及股票: {', '.join(anomaly.stocks[:10])}")
            if len(anomaly.stocks) > 10:
                body_lines.append(f"... 等 {len(anomaly.stocks)} 只")

        success = self._gateway.send_card_message(
            receive_id=channel,
            title=f"🚨 {anomaly.type}",
            body_lines=body_lines,
            color=anomaly.card_color,
        )
        if success:
            logger.info("已推送异常: %s（%s）", anomaly.type, anomaly.severity)
        else:
            logger.error("异常推送失败: %s", anomaly.type)

        # 审计日志
        if self._audit_db is not None:
            self._audit_db.log_anomaly(
                anomaly.type, anomaly.severity, anomaly.message, anomaly.stocks,
            )