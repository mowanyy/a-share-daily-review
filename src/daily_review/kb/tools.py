"""数据工具（function-calling）：问答模式下按需取数，契约对齐 prompts/tools/数据工具schema.md。

v0.35 插件化重构：工具通过 @register_tool 装饰器注册，自动构建 handler 映射与 schema。
加新工具只需写一个函数 + @register_tool，无需修改核心代码。
外部工具放在 kb/tools/ 目录下，自动发现。

- 6 个工具：query_zt_pool / query_zb_pool / query_moneyflow / query_ladder_stats /
  query_theme / query_themes_timeline
- 按交易日 memo 采集与指标（DataToolContext）：同一天多次工具调用不重复跑管道
- 结果用 reporter._compact_json 紧凑序列化；异常/未知工具 → {"error":...}（LLM 可解释，不中断）
- v0.35：execute_tool 返回 (json_str, duration_ms) 元组，支持 Trace 追踪
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any

from daily_review.llm.reporter import _compact_json
from daily_review.pipeline import collect, compute

# 单次工具响应体量上限（超限截断并注明，防长池子撑爆上下文）
ZT_POOL_CAP = 60
THEME_MEMBERS_CAP = 30

# ---------------------------------------------------------------- 进程级采集缓存池（v0.35.5）
#
# 故障背景：QA 工具调用每次都新建 DataToolContext（qa.py _run_loop 内），
# 导致**每条消息都触发一次全量联网采集**（涨停池+时间线+资金流+龙虎榜，
# 实测单次 collect 156s+compute 50s，QA_TIMEOUT=120s 必超时 → 网关永远只回
# "我正在思考中"，用户感知为"@了不回复"）。这里改为模块级共享缓存池：
#   - 同一进程内跨会话/跨消息复用采集与指标结果
#   - 当日数据 TTL 限时（盘中数据会变，5 分钟过期重采）
#   - 历史日期数据定稿，进程生命周期内永久复用
# 锁保护：网关 QA 走 ThreadPoolExecutor，Web 端 Flask 多线程，需线程安全。

_POOL_LOCK = threading.Lock()
_COLLECT_CACHE: dict[str, tuple[float, dict]] = {}     # trade_date -> (ts, collected)
_INDICATORS_CACHE: dict[str, tuple[float, dict]] = {}  # trade_date -> (ts, indicators)
_TODAY_TTL = 300  # 当日数据缓存 TTL（秒）：5 分钟


def _today_str() -> str:
    return datetime.now().strftime("%Y%m%d")


def _pool_fresh(trade_date: str, ts: float) -> bool:
    """缓存是否仍新鲜：历史日期定稿永久；当日按 TTL。"""
    if trade_date < _today_str():
        return True
    return (time.time() - ts) <= _TODAY_TTL


def clear_process_cache() -> None:
    """清空进程级采集/指标缓存池（测试隔离与手动重置用）。"""
    with _POOL_LOCK:
        _COLLECT_CACHE.clear()
        _INDICATORS_CACHE.clear()


def process_cache_stats() -> dict:
    """缓存池状态（键数与最新命中时间），供诊断/测试断言。"""
    with _POOL_LOCK:
        return {
            "collected": {k: round(ts, 1) for k, (ts, _) in _COLLECT_CACHE.items()},
            "indicators": {k: round(ts, 1) for k, (ts, _) in _INDICATORS_CACHE.items()},
        }

# ---------------------------------------------------------------- 插件化注册表（v0.35）

_TOOL_HANDLERS: dict[str, callable] = {}
_TOOL_SCHEMAS: list[dict] = []


def register_tool(
    name: str,
    description: str,
    parameters: dict,
    required: list[str] | None = None,
):
    """装饰器：注册一个数据工具，自动构建 handler 映射 + OpenAI 兼容 schema。

    Args:
        name: 工具名（function-calling 的 function name）
        description: 工具描述
        parameters: JSON Schema 的 properties 字段
        required: 必填参数列表

    Usage:
        @register_tool("my_tool", "描述", {"param": {"type": "string", "description": "..."}})
        def _my_tool(ctx: DataToolContext, args: dict) -> dict:
            ...
    """
    def decorator(func):
        _TOOL_HANDLERS[name] = func
        _TOOL_SCHEMAS.append({
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": parameters,
                    "required": required or [],
                },
            },
        })
        return func
    return decorator


def _discover_external_tools() -> None:
    """扫描 kb/tools/ 目录，自动导入外部工具模块。

    外部模块只要放在 kb/tools/ 下、使用 @register_tool 装饰器，
    即可自动注册，无需修改 core 代码。
    """
    import importlib
    from pathlib import Path

    tools_dir = Path(__file__).parent / "tools"
    if not tools_dir.exists():
        return
    for f in sorted(tools_dir.glob("*.py")):
        if f.name.startswith("_"):
            continue
        try:
            importlib.import_module(f"daily_review.kb.tools.{f.stem}")
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("外部工具模块加载失败 %s: %s", f.name, exc)


# ---------------------------------------------------------------- 通用工具

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
    """按交易日的采集/指标 memo 缓存，避免同一会话内重复抓取。

    v0.35.5：采集/指标缓存升级为**进程级共享缓存池**（_POOL_LOCK/_COLLECT_CACHE/
    _INDICATORS_CACHE）——同进程内跨会话、跨消息复用，当日数据 TTL 300s、历史
    日期永久；实例只保留 default_date（QA 每次回答都新建 ctx 也能吃到共享缓存）。
    """

    def __init__(self, default_date: str | None = None):
        self.default_date = default_date or default_trade_date()

    def resolve_date(self, arg: str | None) -> str:
        return (arg or self.default_date).strip()

    def collected(self, trade_date: str) -> dict:
        with _POOL_LOCK:
            hit = _COLLECT_CACHE.get(trade_date)
            if hit is not None and _pool_fresh(trade_date, hit[0]):
                return hit[1]
        # 锁外执行网络采集（避免长耗时持锁阻塞其他线程；网关 QA 串行、Web 端多线程双采
        # 只是浪费一次采集，不致死锁）
        data = collect(trade_date)
        with _POOL_LOCK:
            _COLLECT_CACHE[trade_date] = (time.time(), data)
        return data

    def indicators(self, trade_date: str) -> dict:
        with _POOL_LOCK:
            hit = _INDICATORS_CACHE.get(trade_date)
            if hit is not None and _pool_fresh(trade_date, hit[0]):
                return hit[1]
        # 锁外执行 compute + collected（同样避免嵌套持锁）
        data = compute(self.collected(trade_date))
        with _POOL_LOCK:
            _INDICATORS_CACHE[trade_date] = (time.time(), data)
        return data


# ---------------------------------------------------------------- 数据工具（v0.7）

@register_tool(
    "query_zt_pool",
    "查询指定交易日的涨停池（连板梯队数据）。返回涨停股列表，字段：code/name/lb_num/first_limit_time/open_times/seal_amount/industry/concepts。",
    {"trade_date": {"type": "string", "description": "交易日 YYYYMMDD，缺省为最近交易日"}},
)
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


@register_tool(
    "query_zb_pool",
    "查询指定交易日的炸板池。返回炸板股列表，字段：code/name/break_times/first_seal_time/up_pct/industry/main_net_inflow/signal。",
    {"trade_date": {"type": "string", "description": "交易日 YYYYMMDD，缺省为最近交易日"}},
)
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


@register_tool(
    "query_moneyflow",
    "查询个股资金流（仅覆盖当日炸板股）。返回 main/super/big 主力净流入（单位：元）。",
    {
        "code": {"type": "string", "description": "6 位股票代码，如 600001"},
        "trade_date": {"type": "string", "description": "交易日 YYYYMMDD，缺省为最近交易日"},
    },
    required=["code"],
)
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


@register_tool(
    "query_ladder_stats",
    "查询指定交易日连板统计（含晋级率）。返回 zt_count/lianban_count/max_lb/max_lb_stock/break_count/break_rate/promotion。",
    {"trade_date": {"type": "string", "description": "交易日 YYYYMMDD，缺省为最近交易日"}},
)
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


@register_tool(
    "query_theme",
    "查询指定交易日的题材归类（题材=行业归类）。可指定 theme_name 查单个题材，缺省返回全部。字段：theme_name/member_count/max_lb/stage/leader。",
    {
        "theme_name": {"type": "string", "description": "题材名（行业名），缺省返回全部"},
        "trade_date": {"type": "string", "description": "交易日 YYYYMMDD，缺省为最近交易日"},
    },
)
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


@register_tool(
    "query_themes_timeline",
    "查询题材近 N 日时序（判断运行周期用）。返回每日 member_count/max_lb/leader。",
    {
        "theme_name": {"type": "string", "description": "题材名（行业名）"},
        "days": {"type": "integer", "description": "近 N 个交易日，缺省 5"},
        "trade_date": {"type": "string", "description": "交易日 YYYYMMDD，缺省为最近交易日"},
    },
    required=["theme_name"],
)
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


@register_tool(
    "concept_pool_create",
    "创建概念池（短线炒作题材的股票池）。如创建\u201c低空经济概念\u201d。",
    {
        "name": {"type": "string", "description": "概念池名称，如 低空经济、AI概念"},
        "description": {"type": "string", "description": "可选描述"},
    },
    required=["name"],
)
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


@register_tool(
    "concept_pool_delete",
    "删除概念池（会删除其中所有股票）。",
    {"name": {"type": "string", "description": "概念池名称"}},
    required=["name"],
)
def _tool_concept_pool_delete(ctx: DataToolContext, args: dict) -> dict:
    from daily_review.web.concept_pool import delete_pool

    name = str(args.get("name") or "").strip()
    if not name:
        return {"error": "缺少 name 参数（概念池名称）"}
    return delete_pool(name)


@register_tool(
    "concept_pool_add_stocks",
    "向概念池添加股票。支持批量添加。",
    {
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
    required=["name", "stocks"],
)
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


@register_tool(
    "concept_pool_remove_stocks",
    "从概念池移除股票。",
    {
        "name": {"type": "string", "description": "概念池名称"},
        "codes": {"type": "string", "description": "逗号分隔的股票代码，如 600001,600002"},
    },
    required=["name", "codes"],
)
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


@register_tool(
    "concept_pool_list",
    "列出所有概念池（名称、股票数量、创建时间）。",
    {},
)
def _tool_concept_pool_list(ctx: DataToolContext, args: dict) -> dict:
    from daily_review.web.concept_pool import list_pools

    pools = list_pools()
    return {"concept_pools": pools, "count": len(pools)}


@register_tool(
    "concept_pool_query",
    "查询概念池中的股票列表（代码、名称、添加日期、备注）。",
    {"name": {"type": "string", "description": "概念池名称"}},
    required=["name"],
)
def _tool_concept_pool_query(ctx: DataToolContext, args: dict) -> dict:
    from daily_review.web.concept_pool import list_pools, query_pool

    name = str(args.get("name") or "").strip()
    if not name:
        return {"error": "缺少 name 参数（概念池名称）"}
    stocks = query_pool(name)
    if stocks is None:
        return {"error": f"概念池「{name}」不存在", "available": [p["name"] for p in list_pools()]}
    return {"name": name, "stock_count": len(stocks), "stocks": stocks}


# ---------------------------------------------------------------- 跨 Agent 通信工具（v0.20）


@register_tool(
    "query_agent",
    "调用其他 Agent（如基金经理风格分析）获取其专业分析意见。可用 agent_id：qa_general（知识问答）、fund_张坤（张坤型）、fund_刘格菘（刘格菘型）、fund_丘栋荣（丘栋荣型）、fund_葛兰（葛兰型）、hotspot_brief（热点简报）。",
    {
        "agent_id": {"type": "string", "description": "Agent ID，如 fund_张坤、qa_general、hotspot_brief"},
        "question": {"type": "string", "description": "要问的问题，如「分析 600519 的估值分位」"},
        "klt": {"type": "integer", "description": "K 线周期，102=周K 103=月K（仅基金经理 Agent 使用）", "default": 102},
    },
    required=["agent_id", "question"],
)
def _tool_query_agent(ctx: DataToolContext, args: dict) -> dict:
    """调用其他 Agent（如基金经理），获取其专业分析意见。"""
    from daily_review.web.agent_registry import call_agent, list_agents

    agent_id = str(args.get("agent_id") or "").strip()
    question = str(args.get("question") or "").strip()
    if not agent_id:
        available = ", ".join(a["id"] for a in list_agents())
        return {"error": f"缺少 agent_id 参数。可用 agent：{available}"}
    if not question:
        return {"error": "缺少 question 参数"}
    context = {"trade_date": ctx.default_date, "klt": args.get("klt", 102)}
    answer = call_agent(agent_id, question, context)
    return {"agent_id": agent_id, "answer": answer}


# ---------------------------------------------------------------- 公开接口

TOOL_NAMES: list[str] = list(_TOOL_HANDLERS)


def execute_tool(name: str, args: dict, ctx: DataToolContext) -> tuple[str, float]:
    """执行一个工具，返回 (紧凑 JSON 字符串, 耗时毫秒) 元组。

    错误也序列化为 JSON 字符串，供 LLM 解释。
    v0.35：新增耗时返回，支持 Trace 追踪。
    """
    t0 = time.perf_counter()
    handler = _TOOL_HANDLERS.get(name)
    if handler is None:
        result = _compact_json({"error": f"未知工具 {name}", "available": TOOL_NAMES})
        duration_ms = (time.perf_counter() - t0) * 1000
        return result, duration_ms
    try:
        result = _compact_json(handler(ctx, args or {}))
        duration_ms = (time.perf_counter() - t0) * 1000
        return result, duration_ms
    except Exception as exc:  # noqa: BLE001 — 采集/指标失败不中断对话
        result = _compact_json({"error": f"{name} 执行失败：{exc}", "args": args})
        duration_ms = (time.perf_counter() - t0) * 1000
        return result, duration_ms


def get_tool_schemas() -> list[dict]:
    """返回所有已注册工具的 OpenAI 兼容 function-calling schema。

    首次调用时触发外部工具发现。
    """
    # 首次调用时发现外部工具（确保 schema 包含外部工具）
    if not getattr(get_tool_schemas, "_discovered", False):
        _discover_external_tools()
        get_tool_schemas._discovered = True
    return _TOOL_SCHEMAS


# 首次导入时发现外部工具
_discover_external_tools()