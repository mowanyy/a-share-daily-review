"""全局配置加载。

配置来源优先环境变量，其次项目根目录的 `.env`（不入库）。承载：
东财请求频率/重试、缓存目录、LLM API 密钥/模型/地址等。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path

# 项目根目录（本文件位于 src/daily_review/config.py，向上三级为仓库根）
PROJECT_ROOT = Path(__file__).resolve().parents[2]

_ENV_FILE = PROJECT_ROOT / ".env"


def _load_dotenv() -> None:
    """极简 .env 读取：仅填充环境变量中尚未设置的 KEY（不覆盖已存在的）。"""
    if not _ENV_FILE.exists():
        return
    for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("\"'")
        if key and not os.getenv(key):
            os.environ[key] = value


@dataclass
class Settings:
    """应用配置。"""

    # 路径
    project_root: Path = PROJECT_ROOT
    data_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "data")
    output_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "output")
    prompts_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "prompts")

    # 数据采集（v0.2 启用）
    request_interval: float = 1.0      # 东财请求最小间隔（秒）
    max_retries: int = 3               # 失败重试次数
    cache_enabled: bool = True

    # 静态缓存目录（v0.14：跨日全局数据，如行业映射/交易日历/概念成分）
    cache_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "cache")
    # 供选股数据切分目录（v0.14：按日期切分的股票池）
    stock_pool_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "stock_pool")

    # LLM（v0.3 启用，DeepSeek，OpenAI 兼容协议）
    llm_base_url: str = field(default_factory=lambda: os.getenv("LLM_BASE_URL", "https://api.deepseek.com"))
    llm_api_key: str = field(
        default_factory=lambda: os.getenv("DEEPSEEK_API_KEY") or os.getenv("LLM_API_KEY", "")
    )
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "deepseek-chat"))
    # 多模型协作：热点信息模型（模型 B）独立提炼当日热点，注入主分析师撰写。
    # 空 → 回落 llm_model；可设 deepseek-reasoner 等（走 chat，非 chat_tools）。
    hotspot_model: str = field(default_factory=lambda: os.getenv("HOTSPOT_MODEL", ""))

    # 兜底提供商（v0.12.1）：主后端（如商汤 SenseNova，限流/慢）429/5xx/网络失败时，
    # 自动用兜底 key/接口/模型重试一次（如官方 DeepSeek）。空 fallback key = 不兜底。
    llm_fallback_api_key: str = field(default_factory=lambda: os.getenv("DEEPSEEK_FALLBACK_API_KEY", ""))
    llm_fallback_base_url: str = field(default_factory=lambda: os.getenv("LLM_FALLBACK_BASE_URL", "https://api.deepseek.com"))
    llm_fallback_model: str = field(default_factory=lambda: os.getenv("LLM_FALLBACK_MODEL", "deepseek-chat"))

    # 飞书群机器人推送（v0.21）：定时报告推送到飞书自定义机器人 webhook。
    # FEISHU_WEBHOOK_URL 必填；FEISHU_SECRET 可选（机器人设了「加签」时填）。
    feishu_webhook_url: str = field(default_factory=lambda: os.getenv("FEISHU_WEBHOOK_URL", ""))
    feishu_secret: str = field(default_factory=lambda: os.getenv("FEISHU_SECRET", ""))

    # 飞书开放平台应用（v0.32 Agent 化）：WebSocket 长连接，双向交互。
    # 用于飞书群@机器人问答、卡片消息推送。需要先在 open.feishu.cn 创建企业自建应用。
    # FEISHU_APP_ID / FEISHU_APP_SECRET 必填；FEISHU_HOME_CHANNEL 为默认推送群 chat_id；
    # FEISHU_ALLOWED_CHAT_IDS 为逗号分隔的允许机器人响应的群 chat_id 列表（空=全部响应）。
    feishu_app_id: str = field(default_factory=lambda: os.getenv("FEISHU_APP_ID", ""))
    feishu_app_secret: str = field(default_factory=lambda: os.getenv("FEISHU_APP_SECRET", ""))
    feishu_home_channel: str = field(default_factory=lambda: os.getenv("FEISHU_HOME_CHANNEL", ""))
    feishu_allowed_chat_ids: list[str] = field(default_factory=lambda: [
        c.strip() for c in os.getenv("FEISHU_ALLOWED_CHAT_IDS", "").split(",") if c.strip()
    ])

    # 盘中监控 Daemon（v0.33）：定时间隔（秒），用于 MarketDaemon 轮询涨停池
    poll_interval: int = field(default_factory=lambda: int(os.getenv("POLL_INTERVAL", "300")))


def get_settings() -> Settings:
    """返回单例配置。"""
    global _settings
    if _settings is None:
        _load_dotenv()
        _settings = Settings()
    return _settings


_settings: Settings | None = None
