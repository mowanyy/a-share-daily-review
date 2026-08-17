"""隔夜预案 + 开盘策略生成（v0.13）。

盘后复盘（17:00 后）→ 隔夜预案（9:00 前）→ 开盘策略（9:25-9:30）

数据流：
  隔夜预案：collect(prev_date) → compute() → indicators + fetch_overnight_news() → LLM → 隔夜预案.md
  开盘策略：indicators + fetch_auction_data() + 隔夜预案文本 → LLM → 开盘策略.md
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from daily_review.config import get_settings
from daily_review.llm.client import LLMError, chat
from daily_review.prompts import get_prompt

_WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


# ---------------------------------------------------------------- JSON 序列化（与 reporter.py 对齐，避免 import 私有函数）

def _to_jsonable(obj):
    """递归转 JSON 可序列化结构。"""
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    if obj is None:
        return None
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, int):
        return obj
    try:
        return _to_jsonable(obj.item())
    except (AttributeError, ValueError):
        return str(obj)


def _compact_json(obj) -> str:
    """紧凑 JSON（保留中文）。"""
    return json.dumps(_to_jsonable(obj), ensure_ascii=False, separators=(",", ":"))


def _weekday_cn(ymd: str) -> str:
    from datetime import datetime
    return _WEEKDAYS[datetime.strptime(ymd, "%Y%m%d").weekday()]


def _fmt_money(v) -> str:
    """金额 → 亿/万 文本。"""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "缺"
    v = float(v)
    if abs(v) >= 1e8:
        return f"{v / 1e8:+.2f}亿"
    if abs(v) >= 1e4:
        return f"{v / 1e4:+.0f}万"
    return f"{v:+.0f}"


# ---------------------------------------------------------------- 隔夜预案

def _overnight_digest(indicators: dict) -> dict:
    """隔夜预案所需的紧凑摘要（聚焦题材与情绪，不含龙虎榜等较细数据）。"""
    ladder = indicators.get("ladder", {})
    emo = indicators.get("emotion") or {}
    themes = indicators.get("themes", [])[:5]
    return {
        "trade_date": ladder.get("trade_date", ""),
        "核心数据": {
            "zt_count": ladder.get("zt_count", 0),
            "lianban_count": ladder.get("lianban_count", 0),
            "max_lb": ladder.get("max_lb", 0),
            "max_lb_stock": ladder.get("max_lb_stock", ""),
            "break_rate": ladder.get("break_rate", 0.0),
        },
        "情绪温度": {
            "score": emo.get("score"),
            "stage": emo.get("stage"),
            "stage_reason": emo.get("stage_reason"),
        },
        "主要题材": [
            {
                "name": t.get("theme_name", ""),
                "member_count": t.get("member_count", 0),
                "max_lb": t.get("max_lb", 0),
                "stage": t.get("stage", ""),
                "leader": (t.get("leader") or {}).get("name", ""),
                "is_main": t.get("is_main", False),
            }
            for t in themes
        ],
        "空间板高度序列": ladder.get("height_series", []),
    }


def _overnight_user(indicators: dict, news_list: list[dict], trade_date: str) -> str:
    """隔夜预案 user 消息。"""
    news_text = json.dumps(
        [
            {
                "title": n.get("title", ""),
                "content": n.get("content", ""),
                "show_time": n.get("show_time", ""),
                "source": n.get("source", ""),
            }
            for n in (news_list or [])
        ],
        ensure_ascii=False,
        indent=1,
    )
    return (
        f"今日日期：{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}（{_weekday_cn(trade_date)}）\n"
        f"昨日复盘核心数据（JSON）：\n```json\n{_compact_json(_overnight_digest(indicators))}\n```\n\n"
        f"隔夜消息（东财7x24快讯，昨日17:00至今早9:00，共{len(news_list)}条）：\n"
        f"```json\n{news_text}\n```\n\n"
        "请输出「隔夜预案」：消息面汇总 → 消息-题材联动分析 → 今日关注方向。"
        "只输出正文，不要标题、不要编造数字。"
    )


def _overnight_fallback(news_list: list[dict]) -> str:
    """隔夜预案 LLM 失败时的确定性兜底：直接列出重要消息。"""
    if not news_list:
        return "（无隔夜消息数据）"
    lines = []
    for n in news_list[:15]:
        t = n.get("show_time", "")
        title = n.get("title", "")
        content = n.get("content", "")
        lines.append(f"- [{t}] {title}：{content[:80]}")
    return "隔夜消息（LLM 生成失败，以下为原始快讯）：\n" + "\n".join(lines)


def generate_overnight_plan(
    indicators: dict,
    news_list: list[dict],
    trade_date: str,
    *,
    api_key: str | None = None,
    out_path: str | Path | None = None,
) -> str:
    """生成隔夜预案并落盘，返回 Markdown 文本。

    indicators: 管道 compute() 产出（昨日复盘指标）。
    news_list: fetch_overnight_news() 产出的隔夜消息列表。
    trade_date: 今日日期 YYYYMMDD（预案适用的交易日）。
    out_path 缺省：output/{trade_date}_隔夜预案.md。
    """
    settings = get_settings()
    api_key = api_key or settings.llm_api_key

    p = get_prompt("module.overnight")
    if p is None:
        raise ValueError("模块 prompt module.overnight 未找到（prompts/modules/隔夜预案.md）")

    title = f"# 📊 {trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]} 隔夜预案（{_weekday_cn(trade_date)}）"

    body = "（隔夜预案生成失败）"
    messages = [
        {"role": "system", "content": p.body},
        {"role": "user", "content": _overnight_user(indicators, news_list, trade_date)},
    ]
    try:
        body = chat(messages, api_key=api_key, max_tokens=4000)  # 推理模型预留 reasoning 预算
    except LLMError as exc:
        body = f"（隔夜预案 LLM 生成失败：{exc}）\n\n{_overnight_fallback(news_list)}"

    final = f"{title}\n\n{body}"
    out_path = out_path or (settings.output_dir / f"{trade_date}_隔夜预案.md")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(final, encoding="utf-8")
    return final


# ---------------------------------------------------------------- 开盘策略

def _open_strategy_digest(indicators: dict) -> dict:
    """开盘策略所需的紧凑摘要（聚焦个股与情绪）。"""
    ladder = indicators.get("ladder", {})
    emo = indicators.get("emotion") or {}
    return {
        "trade_date": ladder.get("trade_date", ""),
        "核心数据": {
            "zt_count": ladder.get("zt_count", 0),
            "lianban_count": ladder.get("lianban_count", 0),
            "max_lb": ladder.get("max_lb", 0),
            "max_lb_stock": ladder.get("max_lb_stock", ""),
        },
        "情绪温度": {
            "score": emo.get("score"),
            "stage": emo.get("stage"),
        },
        "梯队分组(已核算)": [
            {
                "height": f"{layer['height']}板",
                "count": layer["count"],
                "stocks": layer.get("stocks", []),
            }
            for layer in (ladder.get("ladder") or [])
        ],
    }


def _open_strategy_user(
    indicators: dict,
    auction_data: list[dict],
    plan_text: str,
    trade_date: str,
) -> str:
    """开盘策略 user 消息。"""
    return (
        f"今日日期：{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}（{_weekday_cn(trade_date)}）\n"
        f"昨日复盘核心数据（JSON）：\n```json\n{_compact_json(_open_strategy_digest(indicators))}\n```\n\n"
        f"隔夜预案全文：\n{plan_text}\n\n"
        f"竞价数据（昨日涨停股+预案关注股，共{len(auction_data)}只）：\n"
        f"```json\n{_compact_json(auction_data)}\n```\n\n"
        "请输出「开盘策略」：1 段竞价总览 → 有机会的个股清单 → 开盘执行提示。"
        "只输出正文，不要标题、不要编造数字、不要列无数据支撑的个股。"
    )


def _open_strategy_fallback(auction_data: list[dict]) -> str:
    """开盘策略 LLM 失败时的确定性兜底：竞价数据表。"""
    if not auction_data:
        return "（无竞价数据）"
    rows = []
    for r in auction_data:
        pct = r.get("auction_pct")
        ratio = r.get("auction_ratio")
        rows.append(
            f"- {r.get('code', '')} {r.get('name', '')}："
            f"竞价{'高开' if pct and pct > 0 else '低开'}{abs(pct or 0):.2f}% "
            f"/ 量比{'×' if ratio else ''}{ratio if ratio else '缺'}"
        )
    return "竞价数据（LLM 生成失败，以下为原始数据表）：\n" + "\n".join(rows)


def generate_open_strategy(
    indicators: dict,
    auction_data: list[dict],
    plan_text: str,
    trade_date: str,
    *,
    api_key: str | None = None,
    out_path: str | Path | None = None,
) -> str:
    """生成开盘策略并落盘，返回 Markdown 文本。

    indicators: 管道 compute() 产出（昨日复盘指标）。
    auction_data: compute_auction() 产出的竞价指标列表。
    plan_text: 隔夜预案全文（Markdown）。
    trade_date: 今日日期 YYYYMMDD。
    out_path 缺省：output/{trade_date}_开盘策略.md。
    """
    settings = get_settings()
    api_key = api_key or settings.llm_api_key

    p = get_prompt("module.open_strategy")
    if p is None:
        raise ValueError("模块 prompt module.open_strategy 未找到（prompts/modules/开盘策略.md）")

    title = f"# 📊 {trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]} 开盘策略（{_weekday_cn(trade_date)}）"

    body = "（开盘策略生成失败）"
    messages = [
        {"role": "system", "content": p.body},
        {"role": "user", "content": _open_strategy_user(indicators, auction_data, plan_text, trade_date)},
    ]
    try:
        body = chat(messages, api_key=api_key, max_tokens=4000)  # 推理模型预留 reasoning 预算
    except LLMError as exc:
        body = f"（开盘策略 LLM 生成失败：{exc}）\n\n{_open_strategy_fallback(auction_data)}"

    final = f"{title}\n\n{body}"
    out_path = out_path or (settings.output_dir / f"{trade_date}_开盘策略.md")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(final, encoding="utf-8")
    return final