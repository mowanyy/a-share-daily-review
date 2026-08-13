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
from daily_review.data.local_cache import load_board_constituents, load_industry_map, save_board_constituents, save_industry_map
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


def _fmt_concept_code(v) -> str:
    """概念板块领涨股代码标准化：CSV 往返后可能变 float/int（前导零丢失、空→NaN），
    统一补零为 6 位；None/NaN/空 → ""。"""
    if v is None or pd.isna(v):
        return ""
    if isinstance(v, float):
        v = int(v)
    s = str(v)
    return s.zfill(6) if s.isdigit() else s


def _cached(name: str, trade_date: str, fetch_fn: Callable[[], pd.DataFrame],
            *, use_cache: bool = True) -> pd.DataFrame:
    """CSV 缓存优先；空结果不缓存（下次重取）。

    use_cache=False：跳过缓存直接重取（当日收盘后作废盘中快照用，见 collect）。
    读缓存时对 `code` 列做零填充——CSV 往返会把前导零（如 002428 → 2428）丢失，
    导致与 API 直取（字符串 code）的跨模块匹配（炸板资金流 / 龙虎榜联动）失败。
    """
    settings = get_settings()
    if use_cache and settings.cache_enabled:
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


def _fetch_opt(name: str, trade_date: str, fetch_fn: Callable[[], pd.DataFrame],
               *, use_cache: bool = True) -> tuple[pd.DataFrame, bool]:
    """带成功标志的缓存拉取：失败返回 (空表, False)，绝不中断 collect。

    供情绪温度区分「真实 0 家」（_ok=True 且空表 → 该维记满分）与「数据缺失」
    （_ok=False → 该维剔除重归一）。
    """
    try:
        return _cached(name, trade_date, fetch_fn, use_cache=use_cache), True
    except Exception:
        return _empty_df(["trade_date", "code"]), False


def _build_concept_map(
    zt_codes: list[str],
    trade_date: str,
    boards: pd.DataFrame | None = None,
) -> dict[str, list[str]]:
    """当日涨停股 → 概念标签（取涨幅前 N 的概念板块成分交集，best-effort）。

    boards：已取到的概念板块行情（复用概念块数据，避免重复联网）；None → 内部自取（旧行为）。
    """
    if boards is None:
        try:
            boards = em.fetch_concept_boards()
        except Exception:
            return {}
    if boards.empty:
        return {}
    top = boards.sort_values("pct", ascending=False).head(TOP_CONCEPT_BOARDS)
    mapping: dict[str, list[str]] = {c: [] for c in zt_codes}
    for _, b in top.iterrows():
        board_code = str(b["board_code"])
        codes = load_board_constituents(board_code)
        if codes is None:
            try:
                codes = em.fetch_board_constituents(board_code)
                if codes:
                    save_board_constituents(board_code, codes)
            except Exception:
                continue
        for c in codes:
            if c in mapping:
                mapping[c].append(str(b["board_name"]))
    return {c: vs for c, vs in mapping.items() if vs}


def _fetch_concept_boards_block(
    trade_date: str, *, fresh: bool = True
) -> tuple[pd.DataFrame, bool]:
    """概念板块可选块：仅当日采集（clist 为实时快照；历史日期不采，避免今日快照误导）。

    返回 (df, ok)；非今日 / 失败 → 空表 + False，绝不中断 collect。
    """
    if trade_date != datetime.now().strftime("%Y%m%d"):
        return _empty_df(em.CONCEPT_BOARD_COLUMNS), False
    df, ok = _fetch_opt(
        "concept_boards", trade_date,
        lambda: em.fetch_concept_boards(),
        use_cache=fresh,  # 与 zt 同语义：盘中快照收盘后（>=15:00）作废重取
    )
    return (df if ok else _empty_df(em.CONCEPT_BOARD_COLUMNS)), ok


def _concept_boards_block(
    zt: pd.DataFrame, trade_date: str, *, fresh: bool = True
) -> tuple[pd.DataFrame, bool]:
    """collect 概念板块入口：仅「当日且当日有涨停数据」采集；瞬时失败重试一次。

    - zt 为空（非交易日/无涨停）→ 不采集，避免 clist 今日快照写成非交易日数据。
    - 首次失败（空结果不落缓存）→ 重试一次；块与 concept_map 共用同一份数据。
    返回 (df, ok)；绝不中断 collect。
    """
    if zt.empty:
        return _empty_df(em.CONCEPT_BOARD_COLUMNS), False
    df, ok = _fetch_concept_boards_block(trade_date, fresh=fresh)
    if df.empty and not ok:
        df, ok = _fetch_concept_boards_block(trade_date, fresh=fresh)
    return df, ok


def collect(trade_date: str, n_days: int = TIMELINE_DAYS) -> dict:
    """采集指定交易日全部输入数据（含时间线）。返回结构化 dict。

    n_days：时间线长度（今日 + 历史交易日数），review 缺省 6，看板可传更大窗口（如 10）。
    """
    print(f"[采集] 交易日 {trade_date}（时间线 {n_days} 日）")

    # 当日收盘后（>=15:00）重跑同一日期：作废盘中快照缓存，强制重取完整数据
    # （否则盘中先跑过的非空 zt_pool 会被永久复用，收盘后重跑永远读到陈旧快照）
    now = datetime.now()
    today = now.strftime("%Y%m%d")
    after_close = trade_date == today and now.time() >= datetime.strptime("15:00", "%H:%M").time()
    fresh = not after_close

    # 1. 当日池子（zb/dt 带抓取成功标志，供情绪温度区分「0 家」与「缺失」）
    zt = _cached("zt_pool", trade_date, lambda: em.fetch_zt_pool(trade_date), use_cache=fresh)
    zb, zb_ok = _fetch_opt("zb_pool", trade_date, lambda: em.fetch_zb_pool(trade_date), use_cache=fresh)
    dt, dt_ok = _fetch_opt("dt_pool", trade_date, lambda: em.fetch_dt_pool(trade_date), use_cache=fresh)
    print(f"  涨停 {len(zt)} / 炸板 {len(zb)} / 跌停 {len(dt)}")

    # 2. 时间线：近 N 交易日（由近及远），含每日本身池子（缓存复用）
    dates = em.resolve_recent_trade_dates(trade_date, n_days=n_days)
    if trade_date not in dates:
        dates = [trade_date] + dates
    print(f"  时间线 {len(dates)} 个交易日: {','.join(dates)}")

    timeline: list[tuple[str, pd.DataFrame]] = []
    for d in dates:
        pool = _cached("zt_pool", d, lambda d=d: em.fetch_zt_pool(d),
                       use_cache=(d != today or fresh))
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
    #    优先走本地缓存（7 天 TTL），缓存过期/缺失才联网拉取
    industry_map: dict[str, str] = {}
    cached_map = load_industry_map()
    if cached_map is not None:
        industry_map = cached_map
    else:
        try:
            industry_map = em.fetch_stock_industry_map()
            if industry_map:
                save_industry_map(industry_map)
        except Exception:
            pass
    if industry_map:
        zt["industry"] = zt["industry"].map(lambda x: industry_map.get(x, x))
        zb["industry"] = zb["industry"].map(lambda x: industry_map.get(x, x))

    # 4. 概念板块（可选块：clist 实时快照，仅「当日且当日有涨停数据」采集；
    #    历史日期/非交易日不采，避免今日快照误导；瞬时失败重试一次）
    concept_boards, concept_boards_ok = _concept_boards_block(zt, trade_date, fresh=fresh)

    # 4a. 概念标签（复用概念板块数据，仅当日；历史日期跳过）
    concept_map: dict[str, list[str]] = {}
    if trade_date == datetime.now().strftime("%Y%m%d"):
        try:
            concept_map = _build_concept_map(
                [str(c) for c in zt["code"]], trade_date,
                boards=None if concept_boards.empty else concept_boards,
            )
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
    is_intraday = trade_date == today and now.hour < 15

    return {
        "trade_date": trade_date,
        "zt": zt,
        "zb": zb,
        "dt": dt,
        "prev_zt": prev_zt,
        "prev_pools": prev_pools,
        "height_series": height_series,
        "concept_map": concept_map,
        "concept_boards": concept_boards,
        "concept_boards_ok": concept_boards_ok,
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

    # 概念板块（供热点模型提炼当日热点；仅当日有值）。4 列/7 列两种形态都用 .get 兜底。
    # 缓存往返防护：CSV 读回后领涨股代码变 float（前导零丢失）、空值变 NaN → 统一标准化。
    concept_boards: list[dict] = []
    cb = collected.get("concept_boards")
    if cb is not None and not cb.empty:
        if "pct" in cb.columns:
            cb = cb.sort_values("pct", ascending=False)
        cb = cb.head(TOP_CONCEPT_BOARDS)
        for _, r in cb.iterrows():
            concept_boards.append({
                "board_name": "" if pd.isna(r.get("board_name")) else str(r.get("board_name")),
                "pct": None if pd.isna(r.get("pct")) else float(r.get("pct")),
                "main_net_inflow": None if pd.isna(r.get("main_net_inflow")) else float(r.get("main_net_inflow")),
                "leader_code": _fmt_concept_code(r.get("leader_code")),
                "leader_name": "" if pd.isna(r.get("leader_name")) else str(r.get("leader_name")),
                "leader_pct": None if pd.isna(r.get("leader_pct")) else float(r.get("leader_pct")),
            })

    return {
        "trade_date": collected["trade_date"],
        "ladder": ladder,
        "themes": themes,
        "break": break_res,
        "lhb": lhb_res,
        "emotion": emotion,
        "zt_pool": zt_pool,
        "concept_boards": concept_boards,
        "timeline_dates": collected["timeline_dates"],
    }
