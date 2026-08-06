"""全局配置加载（占位）。

v0.1 阶段暂无实际配置项；后续将承载：东财请求频率、缓存目录、
LLM API 密钥/模型、炸板信号阈值等。配置来源优先环境变量，其次本地文件。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path

# 项目根目录（本文件位于 src/daily_review/config.py，向上三级为仓库根）
PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Settings:
    """应用配置。字段在后续迭代中按需扩充。"""

    # 路径
    project_root: Path = PROJECT_ROOT
    data_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "data")
    output_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "output")
    prompts_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "prompts")

    # 数据采集（v0.2 启用）
    request_interval: float = 1.0      # 东财请求最小间隔（秒）
    max_retries: int = 3               # 失败重试次数
    cache_enabled: bool = True

    # LLM（v0.4 启用）
    llm_api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", ""))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", ""))


def get_settings() -> Settings:
    """返回单例配置。"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


_settings: Settings | None = None
