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


def get_settings() -> Settings:
    """返回单例配置。"""
    global _settings
    if _settings is None:
        _load_dotenv()
        _settings = Settings()
    return _settings


_settings: Settings | None = None
