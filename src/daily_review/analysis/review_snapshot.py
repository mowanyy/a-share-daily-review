"""复盘指标快照（v0.30）——跨消息数值一致性的权威基准。

背景：预案/复盘的「昨日情绪温度」此前每次生成都从 data/{date}/*.csv 重算，
同一历史日期在不同时机/代码版本下会得到不同值（实测 8/11 曾算 59、后算 66），
导致前后两天推送对同一日期给出互相矛盾的数值。

方案：完整复盘（收盘后权威数据）落盘 data/review_snapshots/{date}.json，
后续隔夜预案/开盘策略/复盘引用「昨日/前日」情绪温度时优先读快照（权威值），
快照缺失才回退重算并显式标注来源，禁止 LLM 自行推算。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from daily_review.config import get_settings


def _snapshot_dir() -> Path:
    return get_settings().data_dir / "review_snapshots"


def _snapshot_path(trade_date: str) -> Path:
    return _snapshot_dir() / f"{trade_date}.json"


def save_review_snapshot(indicators: dict, trade_date: str) -> Path | None:
    """把完整复盘的 indicators 落盘为权威快照；保存失败打印告警并返回 None（不中断复盘）。

    仅由「完整复盘」流程（cli/jobs/push 的 review 分支）调用；
    预案/开盘策略不得调用，避免用重算值覆盖权威快照。
    """
    try:
        data = {
            "trade_date": trade_date,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "indicators": indicators,
        }
        path = _snapshot_path(trade_date)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
        print(f"[快照] 已保存: {path}")
        return path
    except Exception as exc:  # noqa: BLE001 —— 快照失败不影响复盘主流程
        print(f"[快照] 保存失败（不影响复盘流程）：{type(exc).__name__}: {exc}")
        return None


def load_review_snapshot(trade_date: str) -> dict | None:
    """读回权威快照的 indicators；缺失/损坏返回 None，绝不抛异常。"""
    try:
        raw = json.loads(_snapshot_path(trade_date).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    indicators = raw.get("indicators")
    return indicators if isinstance(indicators, dict) else None