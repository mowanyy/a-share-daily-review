"""端到端复盘管道：采集 → 指标 → LLM 报告（v0.3）。

collect(trade_date)  采集池子/时间线/资金流/行业映射，落盘 data/{date}/*.csv（有缓存则复用）
compute(collected)   指标层：连板梯队 + 题材归类 + 炸板净流入
generate_report()    见 llm.reporter（CLI 无 --no-llm 时调用）
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

import pandas as pd

from daily_review.analysis import analyze_break, build_themes, compute_ladder
from daily_review.config import get_settings
from daily_review.data import eastmoney_pool as em
from daily_review.data.repo import load_csv, save_csv

# 时间线长度（近 N 个交易日：今日 + N-1 天历史，供晋级率/题材时序/高度序列）
TIMELINE_DAYS = 6
# 概念标签最多取前 N 个板块（best-effort，仅当日）
TOP_CONCEPT_BOARDS = 12


def _empty_df(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=columns)


def _cached(name: str, trade_date: str, fetch_fn: Callable[[], pd.DataFrame]) -> pd.DataFrame:
    """CSV 缓存优先；空结果不缓存（下次重取）。"""
    settings = get_settings()
    if settings.cache_enabled:
        try:
            df = load_csv(name, trade_date)
            if not df.empty:
                return df
        except Exception:
            pass
    df = fetch_fn()
    if not df.empty:
        save_csv(df, name, trade_date)
    return df


def _build_concept_map(zt_codes: list[str], trade_date: str) -> dict[str, list[str]]:
    """当日涨停股 → 概念标签（取涨幅前 N 的概念板块成分交集，best-effort）。"""
    try:
        boards = em.fetch_concept_boards()
    except Exception:
        return {}
    if boards.empty:
        return {}
    top = boards.sort_values("pct", ascending=False).head(TOP_CONCEPT_BOARDS)
    mapping: dict[str, list[str]] = {c: [] for c in zt_codes}
    for _, b in top.iterrows():
        try:
            codes = em.fetch_board_constituents(b["board_code"])
        except Exception:
            continue
        for c in codes:
            if c in mapping:
                mapping[c].append(str(b["board_name"]))
    return {c: vs for c, vs in mapping.items() if vs}


def collect(trade_date: str) -> dict:
    """采集指定交易日全部输入数据（含时间线）。返回结构化 dict。"""
    print(f"[采集] 交易日 {trade_date}")

    # 1. 当日池子
    zt = _cached("zt_pool", trade_date, lambda: em.fetch_zt_pool(trade_date))
    zb = _cached("zb_pool", trade_date, lambda: em.fetch_zb_pool(trade_date))
    try:
        dt = em.fetch_dt_pool(trade_date)
        if not dt.empty:
            save_csv(dt, "dt_pool", trade_date)
    except Exception:
        dt = _empty_df(["trade_date", "code", "name", "up_pct"])
    print(f"  涨停 {len(zt)} / 炸板 {len(zb)} / 跌停 {len(dt)}")

    # 2. 时间线：近 N 交易日（由近及远），含每日本身池子（缓存复用）
    dates = em.resolve_recent_trade_dates(trade_date, n_days=TIMELINE_DAYS)
    if trade_date not in dates:
        dates = [trade_date] + dates
    print(f"  时间线 {len(dates)} 个交易日: {','.join(dates)}")

    timeline: list[tuple[str, pd.DataFrame]] = []
    for d in dates:
        pool = _cached(f"zt_pool", d, lambda d=d: em.fetch_zt_pool(d))
        timeline.append((d, pool))

    # 空间板高度序列（最新在前）
    height_series = [
        {"date": d, "max_lb": int(p["lb_num"].max()) if not p.empty else 0}
        for d, p in timeline
    ]
    # 晋级用：昨日池子（timeline 第 2 新 = 最新历史日）；旧→新给题材时序
    prev_pools = [(d, p) for d, p in reversed(timeline) if d != trade_date]
    prev_zt = prev_pools[-1][1] if prev_pools else _empty_df(list(zt.columns))

    # 3. 行业全名映射（best-effort，池内 hybk 兜底）
    industry_map: dict[str, str] = {}
    try:
        industry_map = em.fetch_stock_industry_map()
    except Exception:
        pass
    if industry_map:
        zt["industry"] = zt["industry"].map(lambda x: industry_map.get(x, x))
        zb["industry"] = zb["industry"].map(lambda x: industry_map.get(x, x))

    # 4. 概念标签（仅当日有意义；历史日期跳过，避免用今日板块误导）
    concept_map: dict[str, list[str]] = {}
    if trade_date == datetime.now().strftime("%Y%m%d"):
        try:
            concept_map = _build_concept_map([str(c) for c in zt["code"]], trade_date)
        except Exception:
            pass

    # 5. 炸板股资金流（当日 clist 批量 / 历史单股 fflow，见 eastmoney_pool.fetch_moneyflow）
    zb_codes = [str(c) for c in zb["code"]] if not zb.empty else []
    name_map = dict(zip(zb["code"].astype(str), zb["name"])) if not zb.empty else {}
    moneyflow = _empty_df(["trade_date", "code", "name", "main_net_inflow", "super_net_inflow", "big_net_inflow"])
    if zb_codes:
        moneyflow = em.fetch_moneyflow(zb_codes, trade_date, name_map)
        if not moneyflow.empty:
            save_csv(moneyflow, "moneyflow_zb", trade_date)
    print(f"  资金流 {len(moneyflow)} 条")

    return {
        "trade_date": trade_date,
        "zt": zt,
        "zb": zb,
        "dt": dt,
        "prev_zt": prev_zt,
        "prev_pools": prev_pools,
        "height_series": height_series,
        "concept_map": concept_map,
        "moneyflow": moneyflow,
        "timeline_dates": dates,
    }


def compute(collected: dict) -> dict:
    """指标层：LadderStats + 题材 + 炸板净流入。返回 reporter 需要的 indicators。"""
    zt = collected["zt"]
    zb = collected["zb"]
    prev_zt = collected["prev_zt"]
    prev_pools = collected["prev_pools"]
    concept_map = collected["concept_map"]
    moneyflow = collected["moneyflow"]

    ladder = compute_ladder(zt, zb, prev_zt, collected["height_series"])
    themes = build_themes(zt, prev_pools, concept_map)
    break_res = analyze_break(zb, moneyflow, break_rate=ladder["break_rate"])

    # 涨停池精简（补 concepts / industry），对齐 module.ladder 输入契约
    concepts = collected["concept_map"]
    zt_pool = [
        {
            "code": str(r["code"]),
            "name": str(r["name"]),
            "lb_num": int(r["lb_num"]),
            "first_limit_time": r["first_limit_time"] or "",
            "open_times": int(r["open_times"]),
            "seal_amount": None if pd.isna(r["seal_amount"]) else round(float(r["seal_amount"]), 2),
            "industry": str(r["industry"] or ""),
            "concepts": concepts.get(str(r["code"]), []),
        }
        for _, r in zt.iterrows()
    ]

    return {
        "trade_date": collected["trade_date"],
        "ladder": ladder,
        "themes": themes,
        "break": break_res,
        "zt_pool": zt_pool,
        "timeline_dates": collected["timeline_dates"],
    }
