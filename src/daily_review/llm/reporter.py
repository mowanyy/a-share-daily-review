"""复盘报告生成：模块 prompt × 结构化数据 → 章节 → 分析师组装成 Markdown。

流程（对齐 v0.5）：
1. 取 `module.emotion / module.ladder / module.theme / module.break / module.lhb`
   五个模块 prompt 正文；
2. 指标数据序列化为紧凑 JSON（字段名严格对齐各 prompt 的「输入数据」契约）；
3. 每模块一次 LLM 调用生成章节；单模块失败用数据表兜底，不中断整份报告；
4. `system.analyst` 组装五章节 + 总览 + 次日预案 → 最终 Markdown 落盘 `output/{date}_复盘.md`。
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
from daily_review.prompts import Prompt, get_prompt

_WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

# 章节标题常量（一处定义多处引用，防止重排后改漏产生重复标题）
OVERVIEW_SECTION = "一、总览"
PLAN_SECTION = "七、次日预案"

# 模块顺序与文档章节标题（组装阶段使用；情绪温度是市场级定位，紧跟总览）
_MODULES = [
    ("module.emotion", "二、情绪温度"),
    ("module.ladder", "三、连板梯队"),
    ("module.theme", "四、题材运行周期与归类"),
    ("module.break", "五、炸板与资金"),
    ("module.lhb", "六、龙虎榜与游资"),
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
        "break_count", "break_rate", "promotion", "height_series", "height_position",
    )}
    pool = [
        {k: r.get(k) for k in ("code", "name", "lb_num", "first_limit_time", "open_times", "seal_amount", "industry", "concepts")}
        for r in ind.get("zt_pool", [])
    ]
    ladder = ind.get("ladder", {})
    available = ladder.get("zt_count", 0) > 0
    return {
        "数据可用性": available,
        "数据说明": "数据完整" if available else "涨停池为空（数据未更新）",
        "连板统计": stats,
        "当日涨停池": pool,
        # 已核算梯队分组（高度→数量/代表/弱封标记）：LLM 必须原样引用，禁止重算
        "梯队分组(已核算)": ladder.get("ladder", []),
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
    themes = ind.get("themes", [])
    return {
        "数据可用性": bool(themes),
        "数据说明": f"{len(themes)} 个题材" if themes else "数据缺失",
        "当日各题材": themes,
    }


def _break_payload(ind: dict) -> dict:
    brk = ind.get("break", {})
    available = brk.get("break_count", 0) > 0
    return {
        "数据可用性": available,
        "数据说明": "数据完整" if available else "炸板数据缺失",
        "炸板概览": {"break_count": brk.get("break_count"), "break_rate": brk.get("break_rate")},
        "炸板股资金流向": brk.get("table", []),
    }


def _lhb_payload(ind: dict) -> dict:
    """龙虎榜载荷（对齐 module.lhb 输入契约）。未采集/为空时返回空结构。"""
    lhb = ind.get("lhb") or {}
    available = lhb.get("overview", {}).get("stock_count", 0) > 0
    return {
        "数据可用性": available,
        "数据说明": "数据完整" if available else "龙虎榜数据未更新（盘后 18:00 后）",
        "龙虎榜概览": lhb.get("overview"),
        "个股净买排行": lhb.get("net_rank", []),
        "知名游资动向": lhb.get("hotmoney", []),
        "活跃席位": lhb.get("active_seats", []),
        "涨停联动": lhb.get("zt_cross", []),
        "次日关注候选": lhb.get("watch", []),
    }


def _emotion_payload(ind: dict) -> dict:
    """情绪温度载荷（对齐 module.emotion 输入契约）。"""
    emo = ind.get("emotion") or {}
    notes = emo.get("notes", [])
    return {
        "数据可用性": emo.get("available", False),
        "数据说明": notes[0] if notes else "数据完整",
        "情绪温度(已核算)": {
            "score": emo.get("score"),
            "stage": emo.get("stage"),
            "stage_reason": emo.get("stage_reason"),
            "score_series": [
                {"date": s.get("date"), "score": s.get("score")}
                for s in emo.get("series", [])
            ],
            "days_used": emo.get("days_used"),
        },
        "成分分(已核算)": emo.get("components", {}),
        "原始输入": emo.get("raw", {}),
        "缺失说明": notes,
    }


def _emotion_forced(ind: dict) -> str:
    """情绪温度强制一行：程序核算，LLM 必须原样引用（防幻觉数字/阶段词）。"""
    emo = ind.get("emotion") or {}
    if not emo.get("available"):
        return "情绪温度：数据不足（涨停池为空），阶段未知"
    return (
        f"情绪温度 {emo.get('score')} 分 / 周期 {emo.get('stage')}。"
        f"依据：{emo.get('stage_reason')}"
    )


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


def _lhb_fallback(ind: dict) -> str:
    """龙虎榜兜底：概览 + 净买排行 + 知名游资数据表（模块调用失败时顶替）。"""
    lhb = ind.get("lhb") or {}
    ov = lhb.get("overview") or {}
    if not ov.get("stock_count"):
        return "（当日龙虎榜未更新或数据为空——复盘时间需在盘后 18:00 之后）"
    head = (
        f"上榜 {ov.get('stock_count', 0)} 家 / 净买额 {_fmt_money(ov.get('total_net_amt'))} / "
        f"机构上榜 {ov.get('inst_stock_count', 0)} 家"
    )

    rank = [
        {
            "代码 名称": f"{r['code']} {r['name']}",
            "涨幅": "" if r.get("change_rate") is None else f"{r['change_rate']:+.2f}%",
            "净买额": _fmt_money(r.get("net_amt")),
            "上榜原因": "；".join(r.get("reasons", []))[:40],
            "涨停/连板": f"{r.get('lb_num')}板" if r.get("is_zt") else "否",
        }
        for r in lhb.get("net_rank", [])[:10]
    ]
    rank_tbl = _md_table(rank, ["代码 名称", "涨幅", "净买额", "上榜原因", "涨停/连板"])

    hm = [
        {
            "游资": f"{h.get('tag', '')}（{h.get('style_cn', '')}）",
            "净买总额": _fmt_money(h.get("net_amt")),
            "标的": "、".join(
                f"{s.get('code')} {s.get('stock_name')}（{_fmt_money(s.get('net_amt'))}）"
                for s in (h.get("stocks") or [])[:3]
            ),
        }
        for h in lhb.get("hotmoney", [])[:8]
    ]
    hm_tbl = _md_table(hm, ["游资", "净买总额", "标的"]) if hm else "（当日无知名游资上榜）"
    return f"{head}\n\n### 净买排行\n{rank_tbl}\n\n### 知名游资动向\n{hm_tbl}"


_EMO_COMP_CN = {
    "zt": "涨停家数", "height": "空间板高度", "promote": "晋级延续率",
    "break": "炸板率", "dt": "跌停家数",
}
_EMO_RAW_KEY = {
    "zt": "zt_count", "height": "max_lb", "promote": "promote",
    "break": "break_rate", "dt": "dt_count",
}


def _emotion_fallback(ind: dict) -> str:
    """情绪温度兜底：温度/阶段一行 + 成分拆解表（模块调用失败时顶替）。"""
    emo = ind.get("emotion") or {}
    if not emo.get("available"):
        return "（当日涨停池为空，情绪温度不可用）"
    head = (
        f"情绪温度 {emo.get('score')} 分 / 周期 {emo.get('stage')}。"
        f"依据：{emo.get('stage_reason')}"
    )
    raw = emo.get("raw", {})
    rows = []
    for k, sub in (emo.get("components") or {}).items():
        rv = raw.get(_EMO_RAW_KEY.get(k, ""))
        if k in ("promote", "break") and isinstance(rv, (int, float)):
            rv_txt = f"{rv * 100:.0f}%"
        elif isinstance(rv, (int, float)) and not isinstance(rv, bool):
            rv_txt = f"{rv:.0f}"
        else:
            rv_txt = str(rv)
        rows.append({"成分": _EMO_COMP_CN.get(k, k), "今日值": rv_txt, "成分分(0-100)": f"{sub:.0f}"})
    table = _md_table(rows, ["成分", "今日值", "成分分(0-100)"])
    notes = "；".join(emo.get("notes", []))
    return f"{head}\n\n### 成分拆解\n{table}" + (f"\n\n> 备注：{notes}" if notes else "")


# ---------------------------------------------------------------- 章节生成

def _module_chapter(
    prompt_id: str,
    title: str,
    payload: dict,
    indicators: dict,
    api_key: str,
    fallback,
    *,
    forced: str = "",
    extra_rule: str = "",
    context: str = "",
) -> str:
    """单模块 LLM 生成章节；失败返回兜底数据表（附注失败原因）。

    indicators: 全量指标（fallback 生成数据表需要）。
    forced: 程序核算的确定性内容（如总览一行），以指令形式注入，LLM 必须原样采用。
    extra_rule: 追加的输出纪律（如「已核算表格不得重算」）。
    context: 追加的用户消息上下文（如另一模型提炼的热点简报），仅非空注入。
    """
    p = get_prompt(prompt_id)
    if p is None:
        return f"## {title}\n\n（模块 prompt {prompt_id} 未找到，以下为数据表）\n\n{fallback(indicators)}"
    rules = "只依据输入数据输出，禁止编造。" + (f"\n{extra_rule}" if extra_rule else "")
    forced_hint = (
        f"\n\n【程序核算结果，输出时必须原样采用，不得改写或另造数字】\n{forced}"
        if forced else ""
    )
    context_blk = f"{context}\n" if context else ""
    messages = [
        {"role": "system", "content": f"{p.body}\n\n你是模块分析师，{rules}"},
        {
            "role": "user",
            "content": (
                f"今日数据如下（JSON，字段名与上方「输入数据」契约一致，勿改动含义）：\n"
                f"```json\n{_compact_json(payload)}\n```\n"
                f"{context_blk}"
                f"请按上方「任务」输出「{title}」章节的 Markdown 内容（只输出章节正文，不含章节标题本身）。"
                f"{forced_hint}"
            ),
        },
    ]
    try:
        body = chat(messages, api_key=api_key)
    except LLMError as exc:
        body = f"（模块 {prompt_id} 生成失败：{exc}。以下为数据表兜底）\n\n{fallback(indicators)}"
    # LLM 偶把章节标题也回显（尽管要求只输出正文）→ 去重，防组装出重复标题
    return f"## {title}\n\n{_strip_section_heading(body, title)}"


def _freshness(ind: dict) -> dict:
    """各维度数据可用性汇总（v0.24 B1：结构化新鲜度，LLM 判断哪些有数据再下笔）。"""
    ladder = ind.get("ladder", {})
    lhb = ind.get("lhb") or {}
    emo = ind.get("emotion") or {}
    brk = ind.get("break", {})
    return {
        "涨停梯队": ladder.get("zt_count", 0) > 0,
        "题材": bool(ind.get("themes")),
        "炸板资金": brk.get("break_count", 0) > 0,
        "龙虎榜": lhb.get("overview", {}).get("stock_count", 0) > 0,
        "情绪温度": emo.get("available", False),
        "概念板块": bool(ind.get("concept_boards")),
        "说明": "true=有数据 / false=数据缺失（采集失败或未到更新时间）；真实 0 家也为 true。",
    }


def _headline(ind: dict) -> dict:
    """总览所需核心数据（供分析师写「一、总览」）。"""
    ladder = ind.get("ladder", {})
    emo = ind.get("emotion") or {}
    return {
        "trade_date": ladder.get("trade_date", ""),
        "zt_count": ladder.get("zt_count", 0),
        "lianban_count": ladder.get("lianban_count", 0),
        "max_lb": ladder.get("max_lb", 0),
        "max_lb_stock": ladder.get("max_lb_stock", ""),
        "break_rate": ladder.get("break_rate", 0.0),
        "first_board_count": ladder.get("first_board_count", 0),
        "height_series": ladder.get("height_series", []),
        "emotion_score": emo.get("score"),
        "emotion_stage": emo.get("stage"),
        "emotion_reason": emo.get("stage_reason"),
        "freshness": _freshness(ind),
    }


def _weekday_cn(ymd: str) -> str:
    return _WEEKDAYS[datetime.strptime(ymd, "%Y%m%d").weekday()]


def _strip_section_heading(text: str, title: str) -> str:
    """去掉文本开头的「## {title}」标题（LLM 输出偶自带标题，组装时防重复）。"""
    return re.sub(rf"^##\s*{re.escape(title)}\s*\n?", "", text, count=1).strip()


# ---------------------------------------------------------------- 热点简报（多模型协作：模型 B 提炼，注入撰写调用）

def _hotspot_payload(ind: dict) -> dict:
    """热点模型载荷：概念板块涨幅榜 Top-N + 当日题材（已核算）。"""
    return {
        "trade_date": ind.get("trade_date") or (ind.get("ladder") or {}).get("trade_date", ""),
        "概念板块涨幅榜": ind.get("concept_boards", []),
        "当日题材(已核算)": [
            {
                "theme_name": t.get("theme_name", ""),
                "member_count": t.get("member_count", 0),
                "max_lb": t.get("max_lb", 0),
                "stage": t.get("stage", ""),
                "leader": (t.get("leader") or {}).get("name", ""),
                "is_main": t.get("is_main", False),
            }
            for t in (ind.get("themes") or [])[:5]
        ],
    }


def _hotspot_brief(indicators: dict, api_key: str, *, model: str | None = None) -> str:
    """热点信息模型（模型 B）提炼当日热点简报；无数据/无 prompt/失败返回 ""（不中断报告）。

    model：默认取 settings.hotspot_model（env HOTSPOT_MODEL），空 → 回落主模型 llm_model。
    """
    if not (indicators.get("concept_boards") or []):
        return ""
    p = get_prompt("module.hotspot")
    if p is None:
        return ""
    model = model or get_settings().hotspot_model or None
    date = (indicators.get("ladder") or {}).get("trade_date", "")
    messages = [
        {"role": "system", "content": p.body},
        {
            "role": "user",
            "content": (
                f"复盘日期：{date[:4]}-{date[4:6]}-{date[6:]}。已采集行情如下（JSON，字段名与「输入数据」契约一致）：\n"
                f"```json\n{_compact_json(_hotspot_payload(indicators))}\n```\n"
                "请按「任务」提炼当日 2-4 条热点主线简报。只输出简报正文，150-250 字，不加标题。"
            ),
        },
    ]
    try:
        # 1500：推理模型（deepseek-v4-flash）的 reasoning_content 会先占预算，500 易被思考吃光致正文为空
        return chat(messages, api_key=api_key, model=model, temperature=0.5, max_tokens=1500).strip()
    except LLMError:
        return ""


def _hotspot_fallback_text(indicators: dict) -> str:
    """热点 LLM 失败时的确定性替代：概念板块涨幅/主力净流入 Top-N 文本（非 LLM 提炼）。"""
    rows = (indicators.get("concept_boards") or [])[:5]
    if not rows:
        return ""
    lines = []
    for r in rows:
        pct = r.get("pct")
        inflow = r.get("main_net_inflow")
        leader = r.get("leader_name") or ""
        pct_s = "缺" if pct is None else f"{pct:+.2f}%"
        inflow_s = _fmt_money(inflow) if inflow is not None else "缺"
        leader_s = f"（领涨 {leader}）" if leader else ""
        lines.append(f"· {r.get('board_name', '')}：涨幅 {pct_s}，主力净流入 {inflow_s}{leader_s}")
    return "当日概念板块涨幅靠前：\n" + "\n".join(lines)


def _hotspot_hint(brief: str, source: str) -> str:
    """注入三章节的措辞：告知撰写模型热点线索来源，须引用/校验、不得凭空捏造。

    source: "LLM 提炼"（模型 B 产出）/ "程序按概念板块核算"（确定性 Top-N 兜底）。
    来源措辞随 source 自适应，避免「程序核算」文本被误标为「热点信息模型提炼」。
    """
    if source == "LLM 提炼":
        label = "另一模型提炼的当日热点（LLM 提炼）"
        provenance = "由独立的「热点信息模型」基于已采集行情提炼（非凭空生成）"
    else:
        label = "程序按概念板块核算的当日热点"
        provenance = "由程序按概念板块涨幅/主力净流入核算（确定性文本，未经过模型提炼）"
    return (
        f"【{label}】\n{brief}\n\n"
        f"以上热点线索{provenance}，用于锚定当日主线。"
        "撰写本章时须**引用并校验**：与你本章输入数据能对上的才采用；"
        "矛盾时以输入数据为准并在文中说明；不得编造该线索之外的「热点主线」。"
    )


# ---------------------------------------------------------------- 组装摘要（供总览/预案）

def _build_digest(ind: dict) -> dict:
    """紧凑摘要：三大章结论的浓缩，供「总览 + 次日预案」调用使用（避免回抄全文超 token）。"""
    ladder = ind.get("ladder", {})
    brk = ind.get("break", {})
    lhb = ind.get("lhb") or {}
    emo = ind.get("emotion") or {}
    return {
        "核心数据": _headline(ind),
        "数据可用性": _freshness(ind),
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
        "龙虎榜": {
            "stock_count": (lhb := ind.get("lhb") or {}).get("overview", {}).get("stock_count", 0),
            "total_net_amt": lhb.get("overview", {}).get("total_net_amt"),
            "hotmoney": [
                {"tag": h.get("tag", ""), "style_cn": h.get("style_cn", ""),
                 "net_amt": h.get("net_amt", 0),
                 "stocks": [f"{s.get('code')} {s.get('stock_name')}" for s in h.get("stocks", [])[:2]]}
                for h in lhb.get("hotmoney", [])[:5]
            ],
            "watch": lhb.get("watch", [])[:3],
        },
        "情绪温度": {
            "score": (emo := ind.get("emotion") or {}).get("score"),
            "stage": emo.get("stage"),
            "stage_reason": emo.get("stage_reason"),
        },
    }


# ---------------------------------------------------------------- 次日预案（可战法驱动）

def _cap_text(text: str, limit: int = 6000) -> str:
    """截断超长正文（防御：用户上传的战法可能很长），保留可读性。"""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + f"\n\n……（正文过长，已截断至 {limit} 字）"


def _plan_rule(plan: Prompt | None, strategy: Prompt | None) -> str:
    """次日预案执行规则一句话：指定战法 → 只执行该战法；未指定 → 通用预案。"""
    if strategy is not None:
        return f"已指定战法「{strategy.name}」：只执行该战法，不自行混合其他战法逻辑。"
    if plan is not None:
        return "未指定战法 → 通用预案：仅给方向与风险，不给具体买卖点。"
    return ""


def _plan_system(
    analyst: Prompt | None,
    plan: Prompt | None,
    strategy: Prompt | None,
    plan_rule: str,
) -> str:
    """次日预案 system 消息：分析师角色 +（战法时）module.plan 执行规则 + 战法正文。"""
    if strategy is None:
        # 与原实现逐字节一致（向后兼容）
        return (
            f"{analyst.body if analyst else '你是 A 股超短连板复盘分析师。'}\n\n{plan_rule}\n"
            "预案是「条件触发」式的，不是确定性预测；每个建议注明依据（当日哪个数据）。"
        )
    parts = [analyst.body if analyst else "你是 A 股超短连板复盘分析师。"]
    if plan is not None:
        parts.append(plan.body)  # module.plan 的任务/输出结构/执行规则/输出纪律
    meta = f"适用 {strategy.applies_to}" if strategy.applies_to else "适用情绪阶段见正文"
    parts.append(
        f"【用户指定的战法：{strategy.name}（v{strategy.version or '0.1.0'}，{meta}）】\n"
        f"{_cap_text(strategy.body)}"
    )
    parts.append(plan_rule)
    parts.append("预案是「条件触发」式的，不是确定性预测；每个建议注明依据（当日哪个数据）。")
    return "\n\n".join(parts)


def _plan_user(
    trade_date: str,
    indicators: dict,
    strategy: Prompt | None,
    emo_hint: str = "",
    hotspot: str = "",
) -> str:
    """次日预案 user 消息：日期 + 紧凑摘要 + 输出指令（战法驱动或通用）。

    hotspot: 另一模型提炼的当日热点（热点简报），仅非空注入（作为预案锚点）。
    """
    if strategy is not None:
        instr = (
            f"请按战法「{strategy.name}」的触发条件输出「## {PLAN_SECTION}」的正文："
            "1 段次日核心观点 + 1–3 个关注方向（题材+具体对象+符合战法的触发/买入条件）"
            "+ 晋级/断板两情形梯队预案 + 风险警示（按战法反例逐条核对，数据不足明说缺什么）。"
        )
    else:
        instr = (
            f"请输出「## {PLAN_SECTION}」的正文：1 段次日核心观点 + 1–3 个关注方向（题材+具体对象+观察信号）"
            "+ 风险警示。全部用「若…则…」条件句式；未指定战法，只给方向与风险，不给具体买卖点。"
        )
    body = (
        f"复盘日期：{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}（{_weekday_cn(trade_date)}）\n"
        f"当日结构化摘要（JSON）：\n```json\n{_compact_json(_build_digest(indicators))}\n```\n\n"
    )
    if hotspot:
        body += f"{hotspot}\n\n"
    body += f"{instr}{emo_hint}只输出正文，不要标题、不要编造数字。"
    return body


# ---------------------------------------------------------------- 主入口

def generate_report(
    indicators: dict,
    trade_date: str,
    *,
    api_key: str | None = None,
    out_path: str | Path | None = None,
    strategy: Prompt | None = None,
) -> str:
    """生成完整复盘 Markdown 并落盘，返回文本。

    indicators: 管道产出 {ladder, themes, break, lhb, emotion, zt_pool}（详见 pipeline.compute）。
    out_path 缺省：output/{trade_date}_复盘.md（get_settings().output_dir 下）。
    strategy: 用户指定战法（Prompt，role=strategy）；缺省 None → 通用预案（与原行为一致）。
    传入后「七、次日预案」按 module.plan 执行规则 + 战法正文驱动。

    组装策略：五个模块章节（二~六）由模块调用生成后**代码直接拼接**；
    仅「一、总览」「七、次日预案」走 LLM（喂紧凑摘要，防超 token 截断丢章节）。
    """
    settings = get_settings()
    if not (api_key or settings.llm_api_key):
        raise LLMError(
            "未配置 DEEPSEEK_API_KEY：请在项目根目录 .env 写入后重试（示例：DEEPSEEK_API_KEY=sk-xxx）"
        )
    api_key = api_key or settings.llm_api_key

    analyst = get_prompt("system.analyst")
    plan = get_prompt("module.plan")
    title = f"# 📊 {trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]} 复盘（{_weekday_cn(trade_date)}）"

    # 0. 热点简报（模型 B，独立一次调用；失败→确定性 Top-N；无概念数据→不注入）
    hotspot_hint = ""
    if indicators.get("concept_boards"):
        brief = _hotspot_brief(indicators, api_key)
        if brief:
            hotspot_hint = _hotspot_hint(brief, "LLM 提炼")
        else:
            fallback = _hotspot_fallback_text(indicators)
            if fallback:
                hotspot_hint = _hotspot_hint(fallback, "程序按概念板块核算")

    # 1. 各模块章节（LLM，失败用数据表兜底）
    chapters: list[str] = []
    builders = {
        "module.emotion": (_emotion_payload, _emotion_fallback),
        "module.ladder": (_ladder_payload, _ladder_fallback),
        "module.theme": (_theme_payload, _theme_fallback),
        "module.break": (_break_payload, _break_fallback),
        "module.lhb": (_lhb_payload, _lhb_fallback),
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
        elif prompt_id == "module.emotion":
            # 温度分/阶段/依据句由程序核算：注入后 LLM 必须原样引用，禁止重算
            kw = {
                "forced": _emotion_forced(indicators),
                "extra_rule": (
                    "「情绪温度(已核算)」与「成分分(已核算)」是程序核算结果，必须原样引用（含全部数字与阶段词）；"
                    "禁止重算温度分、改写阶段词或另造依据句；原始输入仅用于组织证据句；数据缺失标注「缺数据」。"
                    "情绪周期（冰点期/修复期/高潮期/退潮期，带「期」）是市场级阶段，题材运行阶段（启动/发酵/高潮/退潮，裸词）是题材级，本章只用带「期」的市场级阶段词。"
                ),
            }
        elif prompt_id == "module.theme" and hotspot_hint:
            # 题材章节注入热点简报：模型 B 提炼的热点主线，供题材归类/主线判定引用校验
            kw = {"context": hotspot_hint}
        chapters.append(_module_chapter(prompt_id, title_cn, payload_fn(indicators), indicators, api_key, fallback_fn, **kw))

    # 2. 总览（LLM，喂核心数据 + 情绪温度强制引用）
    emo = indicators.get("emotion") or {}
    emotion_forced = ""
    if emo.get("available"):
        emotion_forced = (
            f"\n\n【程序核算结果，总览情绪定性必须原样采用，不得另写阶段词】\n"
            f"情绪温度 {emo.get('score')} 分 / 周期 {emo.get('stage')}。依据：{emo.get('stage_reason')}"
        )
    overview = f"核心数据：{_compact_json(_headline(indicators))}\n\n（总览生成失败，详见下方章节）"
    messages = [
        {"role": "system", "content": (analyst.body if analyst else "你是 A 股超短连板复盘分析师。")},
        {
            "role": "user",
            "content": (
                f"复盘日期：{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}（{_weekday_cn(trade_date)}）\n"
                f"核心数据：{_compact_json(_headline(indicators))}\n\n"
                f"{hotspot_hint and hotspot_hint + chr(10) * 2 or ''}"
                f"请输出「## {OVERVIEW_SECTION}」的正文：1 段情绪定性——必须原样引用程序核算的情绪温度"
                f"（分数/阶段，见 emotion_score/emotion_stage/emotion_reason），不得另写阶段词；"
                f"再结合空间板高度与炸板率补充一句话；最后给核心数据一行。"
                f"{emotion_forced}"
                f"只输出正文，不要标题、不要编造数字。"
            ),
        },
    ]
    try:
        overview = chat(messages, api_key=api_key, max_tokens=1200)  # 推理模型预留 reasoning 预算
    except LLMError as exc:
        overview = f"核心数据：{_compact_json(_headline(indicators))}\n\n（总览生成失败：{exc}）"

    # 3. 次日预案（LLM，喂紧凑摘要 + 情绪温度基调；可指定战法驱动）
    if strategy is not None and strategy.role != "strategy":
        strategy = None  # 防御：非战法 prompt 一律按未指定处理
    plan_rule = _plan_rule(plan, strategy)
    emo_hint = ""
    if emo.get("available"):
        emo_hint = (
            f"\n情绪温度：{emo.get('score')} 分 / {emo.get('stage')}"
            f"（程序核算，预案情绪基调必须与其一致，不得另写阶段词）"
        )
    plan_body = f"（预案生成失败。{plan_rule}）"
    messages = [
        {"role": "system", "content": _plan_system(analyst, plan, strategy, plan_rule)},
        {"role": "user", "content": _plan_user(trade_date, indicators, strategy, emo_hint, hotspot=hotspot_hint)},
    ]
    try:
        plan_body = chat(messages, api_key=api_key, max_tokens=2500)  # 推理模型预留 reasoning 预算
    except LLMError as exc:
        plan_body = f"（预案生成失败：{exc}。{plan_rule}）"

    # 4. 组装（章节由代码拼接，保证齐全不丢）
    overview = _strip_section_heading(overview, OVERVIEW_SECTION)
    plan_body = _strip_section_heading(plan_body, PLAN_SECTION)
    final = "\n\n".join([
        title,
        f"## {OVERVIEW_SECTION}\n\n{overview}",
        *chapters,
        f"## {PLAN_SECTION}\n\n{plan_body}",
    ])

    # 5. 落盘
    out_path = out_path or (settings.output_dir / f"{trade_date}_复盘.md")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(final, encoding="utf-8")
    return final
