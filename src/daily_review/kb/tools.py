"""数据工具（function-calling）：问答模式下按需取数，契约对齐 prompts/tools/数据工具schema.md。

- 6 个工具：query_zt_pool / query_zb_pool / query_moneyflow / query_ladder_stats /
  query_theme / query_themes_timeline
- 按交易日 memo 采集与指标（DataToolContext）：同一天多次工具调用不重复跑管道
- 结果用 reporter._compact_json 紧凑序列化；异常/未知工具 → {"error":...}（LLM 可解释，不中断）
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from daily_review.llm.reporter import _compact_json
from daily_review.pipeline import collect, compute

# 单次工具响应体量上限（超限截断并注明，防长池子撑爆上下文）
ZT_POOL_CAP = 60
THEME_MEMBERS_CAP = 30


def default_trade_date() -> str:
    """缺省交易日：探测最近有涨停数据的交易日（与 cli._probe_recent_date 等价，避免循环依赖）。"""
    from daily_review.data import eastmoney_pool

    today = datetime.today().strftime("%Y%m%d")
    try:
        dates = eastmoney_pool.resolve_recent_trade_dates(today, n_days=1)
    except Exception:  # noqa: BLE001 — 探测失败退化为今天
        return today
    return dates[0] if dates else today


class DataToolContext:
    """按交易日的采集/指标 memo 缓存，避免同一会话内重复抓取。"""

    def __init__(self, default_date: str | None = None):
        self.default_date = default_date or default_trade_date()
        self._collected: dict[str, dict] = {}
        self._indicators: dict[str, dict] = {}

    def resolve_date(self, arg: str | None) -> str:
        return (arg or self.default_date).strip()

    def collected(self, trade_date: str) -> dict:
        if trade_date not in self._collected:
            self._collected[trade_date] = collect(trade_date)
        return self._collected[trade_date]

    def indicators(self, trade_date: str) -> dict:
        if trade_date not in self._indicators:
            self._indicators[trade_date] = compute(self.collected(trade_date))
        return self._indicators[trade_date]


def _day_theme_row(trade_date: str, sub) -> dict:
    """单日某行业（题材）成员概况：家数 / 最高身位 / 龙头。"""
    if sub is None or sub.empty:
        return {"trade_date": trade_date, "member_count": 0, "max_lb": 0, "leader": ""}
    max_lb = int(sub["lb_num"].max())
    top = sub[sub["lb_num"] == max_lb].sort_values("first_limit_time")
    r = top.iloc[0]
    return {
        "trade_date": trade_date,
        "member_count": int(len(sub)),
        "max_lb": max_lb,
        "leader": f"{r['code']} {r['name']}",
    }


def _tool_zt_pool(ctx: DataToolContext, args: dict) -> dict:
    date = ctx.resolve_date(args.get("trade_date"))
    pool = ctx.indicators(date)["zt_pool"]
    capped = pool[:ZT_POOL_CAP]
    return {
        "trade_date": date,
        "count": len(pool),
        "zt_pool": capped,
        "note": None if len(pool) <= ZT_POOL_CAP else f"共 {len(pool)} 家，仅返回前 {ZT_POOL_CAP} 家",
    }


def _tool_zb_pool(ctx: DataToolContext, args: dict) -> dict:
    date = ctx.resolve_date(args.get("trade_date"))
    brk = ctx.indicators(date)["break"]
    rows = [
        {k: r.get(k) for k in ("code", "name", "break_times", "first_seal_time", "up_pct", "industry", "main_net_inflow", "signal")}
        for r in brk.get("table", [])
    ]
    return {
        "trade_date": date,
        "break_count": brk.get("break_count", 0),
        "break_rate": brk.get("break_rate"),
        "zb_pool": rows,
    }


def _tool_moneyflow(ctx: DataToolContext, args: dict) -> dict:
    code = str(args.get("code") or "").strip().zfill(6)
    if not code or code == "000000":
        return {"error": "缺少 code 参数（6 位股票代码）"}
    date = ctx.resolve_date(args.get("trade_date"))
    row = next(
        (r for r in ctx.indicators(date)["break"].get("table", []) if str(r["code"]).zfill(6) == code),
        None,
    )
    if not row:
        return {"error": f"{date} 未查询到 {code} 的资金流（资金流工具仅覆盖当日炸板股）", "trade_date": date}
    return {
        "trade_date": date,
        "code": row["code"],
        "name": row["name"],
        "main_net_inflow": row.get("main_net_inflow"),
        "super_net_inflow": row.get("super_net_inflow"),
        "big_net_inflow": row.get("big_net_inflow"),
    }


def _tool_ladder_stats(ctx: DataToolContext, args: dict) -> dict:
    date = ctx.resolve_date(args.get("trade_date"))
    l = ctx.indicators(date)["ladder"]
    return {
        "trade_date": date,
        "zt_count": l.get("zt_count"),
        "lianban_count": l.get("lianban_count"),
        "max_lb": l.get("max_lb"),
        "max_lb_stock": l.get("max_lb_stock"),
        "break_count": l.get("break_count"),
        "break_rate": l.get("break_rate"),
        "promotion": l.get("promotion"),
    }


def _tool_theme(ctx: DataToolContext, args: dict) -> dict:
    date = ctx.resolve_date(args.get("trade_date"))
    themes = ctx.indicators(date)["themes"]
    name = str(args.get("theme_name") or "").strip()
    if name:
        t = next((x for x in themes if x["theme_name"] == name), None)
        if not t:
            return {
                "error": f"当日题材「{name}」不存在（题材=行业归类）",
                "trade_date": date,
                "available_themes": [x["theme_name"] for x in themes][:10],
            }
        out = dict(t)
        out["members"] = out["members"][:THEME_MEMBERS_CAP]
        return {"trade_date": date, "theme": out}
    return {
        "trade_date": date,
        "themes": [
            {k: x.get(k) for k in ("theme_name", "member_count", "max_lb", "stage", "is_main")}
            for x in themes
        ],
    }


def _tool_themes_timeline(ctx: DataToolContext, args: dict) -> dict:
    theme_name = str(args.get("theme_name") or "").strip()
    if not theme_name:
        return {"error": "缺少 theme_name 参数（题材=行业归类名）"}
    days = max(1, int(args.get("days") or 5))
    date = ctx.resolve_date(args.get("trade_date"))
    col = ctx.collected(date)
    prev = col["prev_pools"]  # 旧→新，不含当日
    hist = list(prev)[-(days - 1):] if days > 1 else []
    rows = []
    for d, pool in hist:
        sub = pool[pool["industry"] == theme_name] if not pool.empty else pool
        rows.append(_day_theme_row(d, sub))
    today = col["zt"]
    today_sub = today[today["industry"] == theme_name] if not today.empty else today
    rows.append(_day_theme_row(date, today_sub))
    return {"theme_name": theme_name, "timeline": rows}


# ---------------------------------------------------------------- 概念池工具（v0.14）

def _normalize_stocks_input(raw) -> list[dict]:
    """把 stocks 参数归一为 [{code,name,note}]：支持 dict(批量) 或 list。"""
    stocks: list[dict] = []
    if isinstance(raw, dict):
        raw = [{"code": c, "name": n} for c, n in raw.items()]
    for s in raw or []:
        if isinstance(s, str):
            stocks.append({"code": s, "name": ""})
        elif isinstance(s, dict):
            stocks.append(s)
    return stocks


def _tool_concept_pool_create(ctx: DataToolContext, args: dict) -> dict:
    from daily_review.web.concept_pool import create_pool

    name = str(args.get("name") or "").strip()
    if not name:
        return {"error": "缺少 name 参数（概念池名称）"}
    try:
        result = create_pool(name, description=str(args.get("description") or ""))
        result["hint"] = "创建后可用 concept_pool_add_stocks 添加股票"
        return result
    except ValueError as exc:
        return {"error": str(exc)}


def _tool_concept_pool_delete(ctx: DataToolContext, args: dict) -> dict:
    from daily_review.web.concept_pool import delete_pool

    name = str(args.get("name") or "").strip()
    if not name:
        return {"error": "缺少 name 参数（概念池名称）"}
    return delete_pool(name)


def _tool_concept_pool_add_stocks(ctx: DataToolContext, args: dict) -> dict:
    from daily_review.web.concept_pool import add_stocks

    name = str(args.get("name") or "").strip()
    if not name:
        return {"error": "缺少 name 参数（概念池名称）"}
    stocks = _normalize_stocks_input(args.get("stocks"))
    if not stocks:
        return {"error": "缺少 stocks 参数（要添加的股票代码/名称）"}
    try:
        return add_stocks(name, stocks)
    except ValueError as exc:
        return {"error": str(exc)}


def _tool_concept_pool_remove_stocks(ctx: DataToolContext, args: dict) -> dict:
    from daily_review.web.concept_pool import remove_stocks

    name = str(args.get("name") or "").strip()
    if not name:
        return {"error": "缺少 name 参数（概念池名称）"}
    codes = str(args.get("codes") or "").strip()
    codes_list = [c.strip() for c in codes.replace("，", ",").split(",") if c.strip()]
    if not codes_list:
        return {"error": "缺少 codes 参数（逗号分隔的股票代码）"}
    return remove_stocks(name, codes_list)


def _tool_concept_pool_list(ctx: DataToolContext, args: dict) -> dict:
    from daily_review.web.concept_pool import list_pools

    pools = list_pools()
    return {"concept_pools": pools, "count": len(pools)}


def _tool_concept_pool_query(ctx: DataToolContext, args: dict) -> dict:
    from daily_review.web.concept_pool import query_pool

    name = str(args.get("name") or "").strip()
    if not name:
        return {"error": "缺少 name 参数（概念池名称）"}
    stocks = query_pool(name)
    if stocks is None:
        return {"error": f"概念池「{name}」不存在", "available": [p["name"] for p in list_pools()]}
    return {"name": name, "stock_count": len(stocks), "stocks": stocks}


# 工具名 → 处理器（name 即 function-calling 的 function name）
_TOOL_HANDLERS: dict[str, callable] = {
    "query_zt_pool": _tool_zt_pool,
    "query_zb_pool": _tool_zb_pool,
    "query_moneyflow": _tool_moneyflow,
    "query_ladder_stats": _tool_ladder_stats,
    "query_theme": _tool_theme,
    "query_themes_timeline": _tool_themes_timeline,
    # v0.14 概念池
    "concept_pool_create": _tool_concept_pool_create,
    "concept_pool_delete": _tool_concept_pool_delete,
    "concept_pool_add_stocks": _tool_concept_pool_add_stocks,
    "concept_pool_remove_stocks": _tool_concept_pool_remove_stocks,
    "concept_pool_list": _tool_concept_pool_list,
    "concept_pool_query": _tool_concept_pool_query,
}

TOOL_NAMES: list[str] = list(_TOOL_HANDLERS)


def execute_tool(name: str, args: dict, ctx: DataToolContext) -> str:
    """执行一个工具，返回紧凑 JSON 字符串（错误也序列化为 JSON，供 LLM 解释）。"""
    handler = _TOOL_HANDLERS.get(name)
    if handler is None:
        return _compact_json({"error": f"未知工具 {name}", "available": TOOL_NAMES})
    try:
        return _compact_json(handler(ctx, args or {}))
    except Exception as exc:  # noqa: BLE001 — 采集/指标失败不中断对话
        return _compact_json({"error": f"{name} 执行失败：{exc}", "args": args})


def get_tool_schemas() -> list[dict]:
    """OpenAI 兼容 function-calling schema，字段契约严格对齐 prompts/tools/数据工具schema.md。"""
    trade_date_schema = {
        "type": "string",
        "description": "交易日 YYYYMMDD，缺省为最近交易日",
    }
    return [
        {
            "type": "function",
            "function": {
                "name": "query_zt_pool",
                "description": "查询指定交易日的涨停池（连板梯队数据）。返回涨停股列表，字段：code/name/lb_num/first_limit_time/open_times/seal_amount/industry/concepts。",
                "parameters": {
                    "type": "object",
                    "properties": {"trade_date": trade_date_schema},
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "query_zb_pool",
                "description": "查询指定交易日的炸板池。返回炸板股列表，字段：code/name/break_times/first_seal_time/up_pct/industry/main_net_inflow/signal。",
                "parameters": {
                    "type": "object",
                    "properties": {"trade_date": trade_date_schema},
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "query_moneyflow",
                "description": "查询个股资金流（仅覆盖当日炸板股）。返回 main/super/big 主力净流入（单位：元）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string", "description": "6 位股票代码，如 600001"},
                        "trade_date": trade_date_schema,
                    },
                    "required": ["code"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "query_ladder_stats",
                "description": "查询指定交易日连板统计（含晋级率）。返回 zt_count/lianban_count/max_lb/max_lb_stock/break_count/break_rate/promotion。",
                "parameters": {
                    "type": "object",
                    "properties": {"trade_date": trade_date_schema},
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "query_theme",
                "description": "查询指定交易日的题材归类（题材=行业归类）。可指定 theme_name 查单个题材，缺省返回全部。字段：theme_name/member_count/max_lb/stage/leader。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "theme_name": {"type": "string", "description": "题材名（行业名），缺省返回全部"},
                        "trade_date": trade_date_schema,
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "query_themes_timeline",
                "description": "查询题材近 N 日时序（判断运行周期用）。返回每日 member_count/max_lb/leader。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "theme_name": {"type": "string", "description": "题材名（行业名）"},
                        "days": {"type": "integer", "description": "近 N 个交易日，缺省 5"},
                    },
                    "required": ["theme_name"],
                },
            },
        },
        # ---------- v0.14 概念池工具 ----------
        {
            "type": "function",
            "function": {
                "name": "concept_pool_create",
                "description": "创建概念池（短线炒作题材的股票池）。如创建“低空经济概念”。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "概念池名称，如 低空经济、AI概念"},
                        "description": {"type": "string", "description": "可选描述"},
                    },
                    "required": ["name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "concept_pool_delete",
                "description": "删除概念池（会删除其中所有股票）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "概念池名称"},
                    },
                    "required": ["name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "concept_pool_add_stocks",
                "description": "向概念池添加股票。支持批量添加。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "概念池名称"},
                        "stocks": {
                            "type": "array",
                            "description": "股票列表，每项 {code, name, note?}。也可传对象 {code: name} 或代码字符串列表",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "code": {"type": "string", "description": "6位股票代码"},
                                    "name": {"type": "string", "description": "股票名称"},
                                    "note": {"type": "string", "description": "添加备注"},
                                },
                                "required": ["code"],
                            },
                        },
                    },
                    "required": ["name", "stocks"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "concept_pool_remove_stocks",
                "description": "从概念池移除股票。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "概念池名称"},
                        "codes": {"type": "string", "description": "逗号分隔的股票代码，如 600001,600002"},
                    },
                    "required": ["name", "codes"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "concept_pool_list",
                "description": "列出所有概念池（名称、股票数量、创建时间）。",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "concept_pool_query",
                "description": "查询概念池中的股票列表（代码、名称、添加日期、备注）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "概念池名称"},
                    },
                    "required": ["name"],
                },
            },
        },
    ]
