"""复盘报告生成：模块 prompt × 结构化数据 → 章节 → 分析师组装成 Markdown。

流程（对齐 plan v0.3）：
1. 取 `module.ladder / module.theme / module.break` 三个模块 prompt 正文；
2. 指标数据序列化为紧凑 JSON（字段名严格对齐各 prompt 的「输入数据」契约）；
3. 每模块一次 LLM 调用生成章节；单模块失败用数据表兜底，不中断整份报告；
4. `system.analyst` 组装三章节 + 通用次日预案 → 最终 Markdown 落盘 `output/{date}_复盘.md`。
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from daily_review.config import get_settings
from daily_review.llm.client import LLMError, chat
from daily_review.prompts import get_prompt

_WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

# 模块顺序与文档章节标题（组装阶段使用）
_MODULES = [
    ("module.ladder", "二、连板梯队"),
    ("module.theme", "三、题材运行周期与归类"),
    ("module.break", "四、炸板与资金"),
]


# ---------------------------------------------------------------- 序列化

def _to_jsonable(obj):
    """递归转 JSON 可序列化结构（None / int / float / str / list / dict）。"""
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
    try:  # numpy / pandas 标量
        return _to_jsonable(obj.item())
    except (AttributeError, ValueError):
        return str(obj)


def _compact_json(obj) -> str:
    """紧凑 JSON（保留中文，便于 LLM 阅读）。"""
    return json.dumps(_to_jsonable(obj), ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------- 载荷构建（对齐 prompt 输入契约）

def _ladder_payload(ind: dict) -> dict:
    stats = {k: ind.get(k) for k in (
        "zt_count", "lianban_count", "max_lb", "max_lb_stock",
        "break_count", "break_rate", "promotion", "height_series",
    )}
    pool = [
        {k: r.get(k) for k in ("code", "name", "lb_num", "first_limit_time", "open_times", "seal_amount", "industry", "concepts")}
        for r in ind.get("zt_pool", [])
    ]
    return {
        "连板统计": stats,
        "当日涨停池": pool,
        # 已核算梯队分组（高度→数量/代表/弱封标记）：LLM 必须原样引用，禁止重算
        "梯队分组(已核算)": ind.get("ladder", {}).get("ladder", []),
    }


def _ladder_forced_headline(ind: dict) -> str:
    """总览一行：机械事实由代码核算，LLM 不得改写（防幻觉数字）。"""
    ladder = ind.get("ladder", {})
    code = ""
    max_lb = ladder.get("max_lb", 0)
    for r in ind.get("zt_pool", []):
        if int(r.get("lb_num") or 0) == max_lb:
            code = r.get("code", "")
            break
    stock = f"{code} {ladder.get('max_lb_stock', '')}".strip()
    rate = ladder.get("break_rate", 0) or 0
    return (
        f"涨停 {ladder.get('zt_count', 0)} 家 / 连板 {ladder.get('lianban_count', 0)} 家 / "
        f"空间板 {ladder.get('max_lb', 0)} 板（{stock}）/ 炸板率 {rate * 100:.1f}%"
    )


def _theme_payload(ind: dict) -> dict:
    return {"当日各题材": ind.get("themes", [])}


def _break_payload(ind: dict) -> dict:
    brk = ind.get("break", {})
    return {
        "炸板概览": {"break_count": brk.get("break_count"), "break_rate": brk.get("break_rate")},
        "炸板股资金流向": brk.get("table", []),
    }


# ---------------------------------------------------------------- 兜底章节（模块调用失败时用数据表顶替）

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


def _md_table(rows: list[dict], columns: list[str], max_rows: int = 60) -> str:
    if not rows:
        return "（无数据）"
    header = "| " + " | ".join(columns) + " |"
    sep = "|" + "---|" * len(columns)
    lines = [header, sep]
    for r in rows[:max_rows]:
        cells = []
        for c in columns:
            v = r.get(c)
            if v is None:
                cells.append("")
            elif isinstance(v, float):
                cells.append(f"{v:.2f}" if abs(v) < 1e6 else f"{v:,.0f}")
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    if len(rows) > max_rows:
        lines.append(f"| … 共 {len(rows)} 行（未全部展开） |")
    return "\n".join(lines)


def _ladder_fallback(ind: dict) -> str:
    ladder = ind.get("ladder", {})
    head = (
        f"涨停 {ladder.get('zt_count', 0)} 家 / 连板 {ladder.get('lianban_count', 0)} 家 / "
        f"空间板 {ladder.get('max_lb', 0)} 板（{ladder.get('max_lb_stock', '')}）/ "
        f"炸板率 {ladder.get('break_rate', 0) * 100:.1f}%"
    )
    rows = []
    for layer in ladder.get("ladder", []):
        rows.append({
            "高度": f"{layer['height']}板",
            "数量": layer["count"],
            "代表": " / ".join(layer.get("stocks", [])) or "—",
            "炸板/弱封": "；".join(layer.get("weak", [])) or "—",
        })
    table = _md_table(rows, ["高度", "数量", "代表", "炸板/弱封"])
    prom = "；".join(f"{k}={v}" for k, v in (ladder.get("promotion") or {}).items()) or "缺数据"
    return f"{head}\n\n### 梯队分布\n{table}\n\n晋级率：{prom}"


def _theme_fallback(ind: dict) -> str:
    themes = ind.get("themes", [])
    if not themes:
        return "（当日涨停股行业归类为空或数据不足）"
    rows = [
        {
            "题材": t.get("theme_name", ""),
            "家数": t.get("member_count", 0),
            "最高身位": f"{t.get('max_lb', 0)}板",
            "阶段": t.get("stage", ""),
            "龙头": f"{t.get('leader', {}).get('code', '')} {t.get('leader', {}).get('name', '')}",
            "主线": "是" if t.get("is_main") else "",
        }
        for t in themes
    ]
    return _md_table(rows, ["题材", "家数", "最高身位", "阶段", "龙头", "主线"])


def _break_fallback(ind: dict) -> str:
    brk = ind.get("break", {})
    if not brk.get("table"):
        return f"炸板家数 {brk.get('break_count', 0)}，炸板率 {brk.get('break_rate', 0) * 100:.1f}%（无个股明细）"
    rows = [
        {
            "代码 名称": f"{r['code']} {r['name']}",
            "题材": r.get("industry", ""),
            "炸板次数": r.get("break_times"),
            "收盘涨幅": "" if r.get("up_pct") is None else f"{r['up_pct']:+.2f}%",
            "主力净流入": _fmt_money(r.get("main_net_inflow")),
            "信号": r.get("signal", ""),
        }
        for r in brk["table"]
    ]
    return _md_table(rows, ["代码 名称", "题材", "炸板次数", "收盘涨幅", "主力净流入", "信号"])


# ---------------------------------------------------------------- 章节生成

def _module_chapter(
    prompt_id: str,
    title: str,
    payload: dict,
    api_key: str,
    fallback,
    *,
    forced: str = "",
    extra_rule: str = "",
) -> str:
    """单模块 LLM 生成章节；失败返回兜底数据表（附注失败原因）。

    forced: 程序核算的确定性内容（如总览一行），以指令形式注入，LLM 必须原样采用。
    extra_rule: 追加的输出纪律（如「已核算表格不得重算」）。
    """
    p = get_prompt(prompt_id)
    if p is None:
        return f"## {title}\n\n（模块 prompt {prompt_id} 未找到，以下为数据表）\n\n{fallback()}"
    rules = "只依据输入数据输出，禁止编造。" + (f"\n{extra_rule}" if extra_rule else "")
    forced_hint = (
        f"\n\n【程序核算结果，输出时必须原样采用，不得改写或另造数字】\n{forced}"
        if forced else ""
    )
    messages = [
        {"role": "system", "content": f"{p.body}\n\n你是模块分析师，{rules}"},
        {
            "role": "user",
            "content": (
                f"今日数据如下（JSON，字段名与上方「输入数据」契约一致，勿改动含义）：\n"
                f"```json\n{_compact_json(payload)}\n```\n"
                f"请按上方「任务」输出「{title}」章节的 Markdown 内容（只输出章节正文，不含章节标题本身）。"
                f"{forced_hint}"
            ),
        },
    ]
    try:
        body = chat(messages, api_key=api_key)
    except LLMError as exc:
        body = f"（模块 {prompt_id} 生成失败：{exc}。以下为数据表兜底）\n\n{fallback()}"
    return f"## {title}\n\n{body.strip()}"


def _headline(ind: dict) -> dict:
    """总览所需核心数据（供分析师写「一、总览」）。"""
    ladder = ind.get("ladder", {})
    return {
        "trade_date": ladder.get("trade_date", ""),
        "zt_count": ladder.get("zt_count", 0),
        "lianban_count": ladder.get("lianban_count", 0),
        "max_lb": ladder.get("max_lb", 0),
        "max_lb_stock": ladder.get("max_lb_stock", ""),
        "break_rate": ladder.get("break_rate", 0.0),
        "first_board_count": ladder.get("first_board_count", 0),
        "height_series": ladder.get("height_series", []),
    }


def _weekday_cn(ymd: str) -> str:
    return _WEEKDAYS[datetime.strptime(ymd, "%Y%m%d").weekday()]


def _strip_section_heading(text: str, title: str) -> str:
    """去掉文本开头的「## {title}」标题（LLM 输出偶自带标题，组装时防重复）。"""
    return re.sub(rf"^##\s*{re.escape(title)}\s*\n?", "", text, count=1).strip()


# ---------------------------------------------------------------- 组装摘要（供总览/预案）

def _build_digest(ind: dict) -> dict:
    """紧凑摘要：三大章结论的浓缩，供「总览 + 次日预案」调用使用（避免回抄全文超 token）。"""
    ladder = ind.get("ladder", {})
    brk = ind.get("break", {})
    return {
        "核心数据": _headline(ind),
        "梯队要点": {
            "晋级率": ladder.get("promotion", {}),
            "高度序列": ladder.get("height_series", []),
        },
        "主要题材": [
            {
                "name": t.get("theme_name", ""),
                "member_count": t.get("member_count", 0),
                "max_lb": t.get("max_lb", 0),
                "stage": t.get("stage", ""),
                "leader": (t.get("leader") or {}).get("name", ""),
                "stage_reason": t.get("stage_reason", ""),
            }
            for t in ind.get("themes", [])[:5]
        ],
        "炸板概览": {
            "break_count": brk.get("break_count", 0),
            "break_rate": brk.get("break_rate", 0.0),
            "watch": brk.get("watch", [])[:3],
        },
    }


# ---------------------------------------------------------------- 主入口

def generate_report(
    indicators: dict,
    trade_date: str,
    *,
    api_key: str | None = None,
    out_path: str | Path | None = None,
) -> str:
    """生成完整复盘 Markdown 并落盘，返回文本。

    indicators: 管道产出 {ladder, themes, break, zt_pool}（详见 pipeline.compute）。
    out_path 缺省：output/{trade_date}_复盘.md（get_settings().output_dir 下）。

    组装策略：三大章（二三四）由模块调用生成后**代码直接拼接**；
    仅「一、总览」「五、次日预案」两小节走 LLM（喂紧凑摘要，防超 token 截断丢章节）。
    """
    settings = get_settings()
    if not (api_key or settings.llm_api_key):
        raise LLMError(
            "未配置 DEEPSEEK_API_KEY：请在项目根目录 .env 写入后重试（示例：DEEPSEEK_API_KEY=sk-xxx）"
        )
    api_key = api_key or settings.llm_api_key

    analyst = get_prompt("system.analyst")
    plan = get_prompt("module.plan")
    plan_rule = "未指定战法 → 通用预案：仅给方向与风险，不给具体买卖点。" if plan else ""
    title = f"# 📊 {trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]} 复盘（{_weekday_cn(trade_date)}）"

    # 1. 三模块章节（LLM，失败用数据表兜底）
    chapters: list[str] = []
    builders = {
        "module.ladder": (_ladder_payload, _ladder_fallback),
        "module.theme": (_theme_payload, _theme_fallback),
        "module.break": (_break_payload, _break_fallback),
    }
    for prompt_id, title_cn in _MODULES:
        payload_fn, fallback_fn = builders[prompt_id]
        kw: dict = {}
        if prompt_id == "module.ladder":
            # 总览一行/梯队分组由程序核算：注入后 LLM 原样引用，防幻觉数字
            kw = {
                "forced": _ladder_forced_headline(indicators),
                "extra_rule": (
                    "「总览一行」与「梯队分组(已核算)」是程序核算结果，必须在章节中原样引用（含全部数字）；"
                    "总览一行即上一条程序核算结果，直接作为「1. 总览一行」的内容，不要另写家数；"
                    "梯队表格直接采用已核算分组，禁止自行重算家数。"
                ),
            }
        chapters.append(_module_chapter(prompt_id, title_cn, payload_fn(indicators), api_key, fallback_fn, **kw))

    # 2. 总览（LLM，喂核心数据）
    overview = f"核心数据：{_compact_json(_headline(indicators))}\n\n（总览生成失败，详见下方章节）"
    messages = [
        {"role": "system", "content": (analyst.body if analyst else "你是 A 股超短连板复盘分析师。")},
        {
            "role": "user",
            "content": (
                f"复盘日期：{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}（{_weekday_cn(trade_date)}）\n"
                f"核心数据：{_compact_json(_headline(indicators))}\n\n"
                f"请输出「## 一、总览」的正文：1 段情绪定性（用「修复/高潮/退潮」等定位，结合空间板高度与炸板率）+ 核心数据一行。"
                f"只输出正文，不要标题、不要编造数字。"
            ),
        },
    ]
    try:
        overview = chat(messages, api_key=api_key, max_tokens=600)
    except LLMError as exc:
        overview = f"核心数据：{_compact_json(_headline(indicators))}\n\n（总览生成失败：{exc}）"

    # 3. 次日预案（LLM，喂紧凑摘要）
    plan_body = f"（预案生成失败。{plan_rule}）"
    messages = [
        {
            "role": "system",
            "content": (
                f"{analyst.body if analyst else '你是 A 股超短连板复盘分析师。'}\n\n{plan_rule}\n"
                "预案是「条件触发」式的，不是确定性预测；每个建议注明依据（当日哪个数据）。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"复盘日期：{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}（{_weekday_cn(trade_date)}）\n"
                f"当日结构化摘要（JSON）：\n```json\n{_compact_json(_build_digest(indicators))}\n```\n\n"
                f"请输出「## 五、次日预案」的正文：1 段次日核心观点 + 1–3 个关注方向（题材+具体对象+观察信号）"
                f"+ 风险警示。全部用「若…则…」条件句式；未指定战法，只给方向与风险，不给具体买卖点。"
                f"只输出正文，不要标题、不要编造数字。"
            ),
        },
    ]
    try:
        plan_body = chat(messages, api_key=api_key, max_tokens=1500)
    except LLMError as exc:
        plan_body = f"（预案生成失败：{exc}。{plan_rule}）"

    # 4. 组装（章节由代码拼接，保证齐全不丢）
    overview = _strip_section_heading(overview, "一、总览")
    plan_body = _strip_section_heading(plan_body, "五、次日预案")
    final = "\n\n".join([
        title,
        f"## 一、总览\n\n{overview}",
        *chapters,
        f"## 五、次日预案\n\n{plan_body}",
    ])

    # 5. 落盘
    out_path = out_path or (settings.output_dir / f"{trade_date}_复盘.md")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(final, encoding="utf-8")
    return final
