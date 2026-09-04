"""龙虎榜游资分析（v0.4，对齐 prompts/modules/龙虎榜游资.md 的输入契约）。

输入：每日榜单（LHB_DAILY_COLUMNS）+ 买卖席位（LHB_SEAT_COLUMNS）+ 当日涨停池 zt。

去重约定（避免一只股票多原因导致重复计数）：
- 每日榜单按 code 去重，取 |净买额| 最大的一条为代表，原因列表合并；
- 席位明细按 (code, seat_code) 去重，同上取 |净额| 最大一条。

输出：
  overview      上榜股票数 / 净买总额 / 买入总额 / 原因分布 / 机构上榜数
  net_rank      个股净买额降序 TOP N（含涨幅/换手/原因/买卖席位数）
  hotmoney      知名游资动向：每个游资标签 → 风格 + 净买总额 + 涉及股票明细
  active_seats  当日活跃席位（净买额 TOP N，含游资标签）
  zt_cross      上榜且当日涨停/连板股（游资 × 连板联动）
  watch         次日关注（知名游资明显净买 + 连板或高涨幅）
"""

from __future__ import annotations

import math

import pandas as pd

from daily_review.data.hotmoney_seats import match_hotmoney, seat_style_cn

# 阈值（集中配置）
HOTMONEY_NET_THRESHOLD = 30_000_000   # 游资单票净买 ≥ 3000 万视为「明显出手」
NET_RANK_TOP = 15                      # 净买排行展示条数
ACTIVE_SEAT_TOP = 15                   # 活跃席位展示条数
WATCH_CHANGE_RATE = 8.0                # 关注股涨幅下限（%）
WATCH_MAX = 5                          # 关注股最多条数


def _dedup_max_abs(rows: list[dict], keys: list[str], amount_key: str) -> list[dict]:
    """按 keys 去重：每组保留 |amount_key| 最大的一条。"""
    best: dict[tuple, dict] = {}
    for r in rows:
        k = tuple(r[key] for key in keys)
        cur = best.get(k)
        if cur is None or abs(r.get(amount_key) or 0) > abs(cur.get(amount_key) or 0):
            best[k] = r
    return list(best.values())


def _norm_daily(daily: pd.DataFrame) -> list[dict]:
    """每日榜单去重：按 code 取 |净买额| 最大一条为准，原因/类型独立累积合并。

    原因累积独立于代表行选择——即使代表行是「后出现的更大净额行」，
    前面行的原因也不会丢失。
    """
    if daily.empty:
        return []
    by_code: dict[str, dict] = {}
    reasons_map: dict[str, list[str]] = {}
    types_map: dict[str, list[str]] = {}
    for _, r in daily.iterrows():
        code = str(r["code"])
        net = r.get("lhb_net_amt") or 0
        cur = by_code.get(code)
        if cur is None or abs(net) > abs(cur.get("lhb_net_amt") or 0):
            by_code[code] = {c: r.get(c) for c in daily.columns}
        for reason in ([str(r.get("reason"))] if r.get("reason") else []):
            if reason and reason not in reasons_map.setdefault(code, []):
                reasons_map[code].append(reason)
        for rt in ([str(r.get("reason_type"))] if r.get("reason_type") else []):
            if rt and rt not in types_map.setdefault(code, []):
                types_map[code].append(rt)
    out = []
    for code, row in by_code.items():
        row = dict(row)
        row["reasons"] = reasons_map[code]
        row["reason_types"] = types_map[code]
        out.append(row)
    return out


def _norm_seats(seats: pd.DataFrame) -> list[dict]:
    """席位明细去重：按 (code, seat_code) 取 |净额| 最大一条。"""
    if seats.empty:
        return []
    best: dict[tuple, dict] = {}
    for _, r in seats.iterrows():
        key = (str(r["code"]), str(r["seat_code"]))
        cur = best.get(key)
        if cur is None or abs(r.get("net_amt") or 0) > abs(cur.get("net_amt") or 0):
            best[key] = {c: r.get(c) for c in seats.columns}
    return list(best.values())


def _reason_dist(daily: pd.DataFrame) -> list[dict]:
    """上榜原因分布（原始行逐条统计，count）。"""
    counter: dict[str, int] = {}
    for reason in daily["reason"].dropna():
        if not reason:
            continue
        counter[str(reason)] = counter.get(str(reason), 0) + 1
    return [
        {"reason": k, "count": v}
        for k, v in sorted(counter.items(), key=lambda x: -x[1])
    ]


def _inst_count(norm: list[dict]) -> int:
    """机构上榜股数：原因类型含「机构买入」或席位含「机构专用」。"""
    n = 0
    for r in norm:
        types = " ".join(r.get("reason_types", []))
        if "机构" in types:
            n += 1
    return n


def _cross_zt(codes: set[str], zt: pd.DataFrame) -> dict[str, int]:
    """涨停池 → {code: lb_num}，仅保留在 codes 内的。"""
    if zt is None or zt.empty:
        return {}
    out: dict[str, int] = {}
    for _, r in zt.iterrows():
        c = str(r["code"])
        if c in codes:
            out[c] = int(r["lb_num"])
    return out


def analyze_lhb(
    daily: pd.DataFrame,
    seats: pd.DataFrame,
    zt: pd.DataFrame | None = None,
) -> dict:
    """龙虎榜游资分析主入口。daily/seats 为空时返回空结构（不抛错）。"""
    norm = _norm_daily(daily)
    zt_codes = set(str(c) for c in zt["code"]) if zt is not None and not zt.empty else set()
    zt_lb = _cross_zt(zt_codes, zt)

    # ---- 概览 ----
    overview = {
        "stock_count": len(norm),
        "total_net_amt": sum(r["lhb_net_amt"] or 0 for r in norm),
        "total_buy_amt": sum(r["lhb_buy_amt"] or 0 for r in norm),
        "total_sell_amt": sum(r["lhb_sell_amt"] or 0 for r in norm),
        "reason_dist": _reason_dist(daily),
        "inst_stock_count": _inst_count(norm),
    }

    # ---- 净买排行 ----
    net_rank = sorted(norm, key=lambda r: -(r.get("lhb_net_amt") or 0))[:NET_RANK_TOP]
    net_rank = [
        {
            "code": r["code"],
            "name": r["name"],
            "change_rate": r.get("change_rate"),
            "turnover_rate": r.get("turnover_rate"),
            "net_amt": r.get("lhb_net_amt"),
            "buy_amt": r.get("lhb_buy_amt"),
            "sell_amt": r.get("lhb_sell_amt"),
            "reasons": r.get("reasons", []),
            "reason_types": r.get("reason_types", []),
            "buy_seats": r.get("buy_seats"),
            "sell_seats": r.get("sell_seats"),
            "is_zt": r["code"] in zt_codes,
            "lb_num": zt_lb.get(r["code"], 0),
        }
        for r in net_rank
    ]

    # ---- 席位与游资 ----
    seat_rows = _norm_seats(seats)
    seat_view: list[dict] = []
    for r in seat_rows:
        full_name = r.get("seat_name") or ""
        abbr = r.get("seat_abbr") or ""
        # 全名可能插有「有限责任公司」等字样，关键词命中不了 → 拼接简称一起匹配
        match_name = f"{full_name} {abbr}".strip()
        tag = match_hotmoney(match_name)
        seat_view.append({
            "seat_code": r["seat_code"],
            "seat_name": full_name or abbr,
            "seat_abbr": abbr,
            "code": r["code"],
            "stock_name": r.get("name", ""),
            "act_buy": r.get("act_buy"),
            "act_sell": r.get("act_sell"),
            "net_amt": r.get("net_amt"),
            "reason": r.get("reason", ""),
            "change_rate": r.get("change_rate"),
            "is_zt": r["code"] in zt_codes,
            "lb_num": zt_lb.get(r["code"], 0),
            "tag": tag["tag"] if tag else "",
            "style": tag["style"] if tag else "",
            "style_cn": seat_style_cn(tag["style"]) if tag else "",
        })

    # 活跃席位：按净买额降序（仅净买为正者上榜），TOP N
    active_seats = sorted(
        [s for s in seat_view if (s["net_amt"] or 0) > 0],
        key=lambda s: -s["net_amt"],
    )[:ACTIVE_SEAT_TOP]

    # 知名游资：按标签聚合（排除散户通道拉萨系——它用于识别而非游资）
    hotmoney_map: dict[str, dict] = {}
    for s in seat_view:
        if not s["tag"] or s["style"] == "retail":
            continue
        item = hotmoney_map.setdefault(s["tag"], {
            "tag": s["tag"],
            "style": s["style"],
            "style_cn": s["style_cn"],
            "net_amt": 0.0,
            "buy_amt": 0.0,
            "stocks": [],
        })
        item["net_amt"] += s["net_amt"] or 0
        item["buy_amt"] += s["act_buy"] or 0
        stock_row = {k: s[k] for k in (
            "code", "stock_name", "act_buy", "act_sell", "net_amt",
            "reason", "change_rate", "is_zt", "lb_num",
        )}
        item["stocks"].append(stock_row)
    hotmoney = sorted(hotmoney_map.values(), key=lambda h: -h["net_amt"])
    for h in hotmoney:
        h["stocks"] = sorted(h["stocks"], key=lambda x: -(x["net_amt"] or 0))[:10]

    # ---- 涨停联动：全部上榜股 ∩ 当日涨停池 ----
    zt_cross = [
        {
            "code": r["code"],
            "name": r["name"],
            "change_rate": r.get("change_rate"),
            "net_amt": r.get("lhb_net_amt"),
            "reasons": r.get("reasons", []),
            "lb_num": zt_lb.get(r["code"], 0),
        }
        for r in norm if r["code"] in zt_codes
    ]
    zt_cross.sort(key=lambda x: -(x["net_amt"] or 0))

    # ---- 次日关注：知名游资明显净买 +（连板 或 涨幅高）----
    watch: list[dict] = []
    for h in hotmoney:
        for s in h["stocks"]:
            if (s.get("net_amt") or 0) < HOTMONEY_NET_THRESHOLD:
                continue
            if not s["is_zt"] and (s.get("change_rate") or 0) < WATCH_CHANGE_RATE:
                continue
            watch.append({
                "tag": h["tag"],
                "code": s["code"],
                "name": s["stock_name"],
                "net_amt": s["net_amt"],
                "change_rate": s["change_rate"],
                "lb_num": s["lb_num"],
                "is_zt": s["is_zt"],
            })
            if len(watch) >= WATCH_MAX:
                break
        if len(watch) >= WATCH_MAX:
            break

    return {
        "overview": overview,
        "net_rank": net_rank,
        "hotmoney": hotmoney,
        "active_seats": active_seats,
        "zt_cross": zt_cross,
        "watch": watch,
    }
