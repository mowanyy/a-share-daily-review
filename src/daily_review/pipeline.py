"""端到端复盘管道：采集 → 指标 → LLM 报告（v0.3/v0.4）。

collect(trade_date)  采集池子/时间线/资金流/行业映射/龙虎榜，落盘 data/{date}/*.csv（有缓存则复用）
compute(collected)   指标层：连板梯队 + 题材归类 + 炸板净流入 + 龙虎榜游资
generate_report()    见 llm.reporter（CLI 无 --no-llm 时调用）
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

import pandas as pd

from daily_review.analysis import analyze_break, analyze_lhb, build_themes, compute_emotion, compute_ladder
from daily_review.config import get_settings
from daily_review.data import eastmoney_lhb, eastmoney_pool as em
from daily_review.data.repo import load_csv, save_csv

# 时间线长度（近 N 个交易日：今日 + N-1 天历史，供晋级率/题材时序/高度序列）
TIMELINE_DAYS = 6
# 概念标签最多取前 N 个板块（best-effort，仅当日）
TOP_CONCEPT_BOARDS = 12


def _empty_df(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=columns)


def _zfill_codes(df: pd.DataFrame) -> pd.DataFrame:
    """把 `code` 列统一零填充为 6 位字符串（防 CSV 往返丢前导零）。

    CSV 读回后 code 可能是 int/float（002428 → 2428.0，pandas 3.0 甚至把
    "000001" 解析为 1.0）：数值型先去尾 ".0" 再转字符串，最后 zfill(6)。
    """
    if "code" not in df.columns:
        return df
    df = df.copy()
    s = df["code"]
    if s.dtype.kind in "iuf":  # int/uint/float 数值型
        s = s.fillna("").astype(str).str.replace(r"\.0$", "", regex=True)
    else:
        s = s.astype(str)
    df["code"] = s.mask(s.str.len() > 0, s.str.zfill(6))
    return df


def _cached(name: str, trade_date: str, fetch_fn: Callable[[], pd.DataFrame]) -> pd.DataFrame:
    """CSV 缓存优先；空结果不缓存（下次重取）。

    读缓存时对 `code` 列做零填充——CSV 往返会把前导零（如 002428 → 2428）丢失，
    导致与 API 直取（字符串 code）的跨模块匹配（炸板资金流 / 龙虎榜联动）失败。
    """
    settings = get_settings()
    if settings.cache_enabled:
        try:
            df = load_csv(name, trade_date)
            if not df.empty:
                return _zfill_codes(df)
        except Exception:
            pass
    df = fetch_fn()
    if not df.empty:
        save_csv(df, name, trade_date)
    return df


def _fetch_opt(name: str, trade_date: str, fetch_fn: Callable[[], pd.DataFrame]) -> tuple[pd.DataFrame, bool]:
    """带成功标志的缓存拉取：失败返回 (空表, False)，绝不中断 collect。

    供情绪温度区分「真实 0 家」（_ok=True 且空表 → 该维记满分）与「数据缺失」
    （_ok=False → 该维剔除重归一）。
    """
    try:
        return _cached(name, trade_date, fetch_fn), True
    except Exception:
        return _empty_df(["trade_date", "code"]), False


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


def collect(trade_date: str, n_days: int = TIMELINE_DAYS) -> dict:
    """采集指定交易日全部输入数据（含时间线）。返回结构化 dict。

    n_days：时间线长度（今日 + 历史交易日数），review 缺省 6，看板可传更大窗口（如 10）。
    """
    print(f"[采集] 交易日 {trade_date}（时间线 {n_days} 日）")

    # 1. 当日池子（zb/dt 带抓取成功标志，供情绪温度区分「0 家」与「缺失」）
    zt = _cached("zt_pool", trade_date, lambda: em.fetch_zt_pool(trade_date))
    zb, zb_ok = _fetch_opt("zb_pool", trade_date, lambda: em.fetch_zb_pool(trade_date))
    dt, dt_ok = _fetch_opt("dt_pool", trade_date, lambda: em.fetch_dt_pool(trade_date))
    print(f"  涨停 {len(zt)} / 炸板 {len(zb)} / 跌停 {len(dt)}")

    # 2. 时间线：近 N 交易日（由近及远），含每日本身池子（缓存复用）
    dates = em.resolve_recent_trade_dates(trade_date, n_days=n_days)
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

    # 情绪温度历史日（旧→新，与 prev_pools 同序）：每日本身 zt/zb/dt + 抓取成功标志
    hist_days: list[dict] = []
    for d, pool in prev_pools:
        zb_i, ok_zb = _fetch_opt("zb_pool", d, lambda d=d: em.fetch_zb_pool(d))
        dt_i, ok_dt = _fetch_opt("dt_pool", d, lambda d=d: em.fetch_dt_pool(d))
        hist_days.append({"date": d, "zt": pool, "zb": zb_i, "dt": dt_i, "zb_ok": ok_zb, "dt_ok": ok_dt})

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

    # 6. 龙虎榜（盘后约 17:30 更新；盘中/未更新日为空表，优雅降级不报错）
    try:
        lhb_daily = _cached("lhb_daily", trade_date, lambda: eastmoney_lhb.fetch_lhb_daily(trade_date))
    except Exception:
        lhb_daily = _empty_df(eastmoney_lhb.LHB_DAILY_COLUMNS)
    try:
        lhb_seats = _cached("lhb_seats", trade_date, lambda: eastmoney_lhb.fetch_lhb_seats(trade_date))
    except Exception:
        lhb_seats = _empty_df(eastmoney_lhb.LHB_SEAT_COLUMNS)
    print(f"  龙虎榜 {len(lhb_daily)} 条 / 席位 {len(lhb_seats)} 条")

    # 盘中标记：当日且当前时间 < 15:00（情绪温度记「盘中预览」）
    is_intraday = trade_date == datetime.now().strftime("%Y%m%d") and datetime.now().hour < 15

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
        "lhb_daily": lhb_daily,
        "lhb_seats": lhb_seats,
        "hist_days": hist_days,
        "zb_ok": zb_ok,
        "dt_ok": dt_ok,
        "is_intraday": is_intraday,
        "timeline_dates": dates,
    }


def compute(collected: dict) -> dict:
    """指标层：LadderStats + 题材 + 炸板净流入。返回 reporter 需要的 indicators。"""
    zt = collected["zt"]
    zb = collected["zb"]
    dt = collected["dt"]
    prev_zt = collected["prev_zt"]
    prev_pools = collected["prev_pools"]
    concept_map = collected["concept_map"]
    moneyflow = collected["moneyflow"]

    ladder = compute_ladder(zt, zb, prev_zt, collected["height_series"])
    themes = build_themes(zt, prev_pools, concept_map)
    break_res = analyze_break(zb, moneyflow, break_rate=ladder["break_rate"])
    lhb_res = analyze_lhb(
        collected.get("lhb_daily", _empty_df(eastmoney_lhb.LHB_DAILY_COLUMNS)),
        collected.get("lhb_seats", _empty_df(eastmoney_lhb.LHB_SEAT_COLUMNS)),
        zt,
    )
    emotion = compute_emotion(
        zt,
        zb,
        dt,
        collected.get("hist_days", []),
        zb_ok=collected.get("zb_ok", True),
        dt_ok=collected.get("dt_ok", True),
        is_intraday=collected.get("is_intraday", False),
    )

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
        "lhb": lhb_res,
        "emotion": emotion,
        "zt_pool": zt_pool,
        "timeline_dates": collected["timeline_dates"],
    }
