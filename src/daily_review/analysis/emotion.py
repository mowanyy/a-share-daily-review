"""情绪温度指标（对齐 docs/数据结构.md 的 EmotionStats）。

把五个已采集的东财池子维度合成 0-100 情绪温度分 + 四阶段情绪周期：
  涨停家数 / 空间板高度 / 晋级延续率 / 炸板率(反向) / 跌停家数(反向)

- 全部 15:00 后即可算（不依赖 17:30 龙虎榜）；龙虎榜净买等仅作可选增强、不进核心分。
- 温度分可解释：每个子分都可回溯到「经验锚点 + 分段线性插值」。
- 锚点/权重为经验值（A 股超短常规区间），集中定义，待 20+ 交易日真实数据校准。

情绪周期（市场级，带「期」）：冰点期 / 修复期 / 高潮期 / 退潮期，
与题材运行阶段（题材级，裸词 启动/发酵/高潮/退潮）两套词并存，不可混用。
"""

from __future__ import annotations

import pandas as pd

# ---------------------------------------------------------------- 锚点（经验值，待校准）
# {原始值: 子分}，键升序；段内线性插值，端点 clamp。
ZT_ANCHORS      = {20: 0, 70: 50, 120: 100}   # 涨停家数
HEIGHT_ANCHORS  = {1: 0, 4: 50, 6: 75, 8: 100}   # 空间板高度（板）
PROMOTE_ANCHORS = {0.0: 0, 0.25: 50, 0.5: 100}   # 晋级延续率
BREAK_ANCHORS   = {0.5: 0, 0.30: 50, 0.15: 100}   # 炸板率（反向：越高分越低）
DT_ANCHORS      = {50: 0, 15: 50, 0: 100}          # 跌停家数（反向：越多分越低）

# 权重（和为 1；缺失维度剔除后重归一）
WEIGHTS = {"zt": 0.30, "height": 0.20, "promote": 0.20, "break": 0.15, "dt": 0.15}

# 阶段阈值（温度分静态区间：<45 冰点/修复带；45-70 修复/退潮带；≥70 高潮/退潮带）
SCORE_STAGE_MID = 45.0
SCORE_STAGE_HIGH = 70.0
# 方向判定：今日比最近有分前日下降超过该值才视为「向下」（持平/微跌 1 分内算上升）
DIRECTION_EPS = 1.0


# ---------------------------------------------------------------- 归一化

def _piecewise(v: float, anchors: dict) -> float:
    """分段线性插值 + 端点 clamp → 子分 0-100。anchors 键升序，段内线性。"""
    if v is None:
        return None
    xs = sorted(anchors)
    if v <= xs[0]:
        return float(anchors[xs[0]])
    if v >= xs[-1]:
        return float(anchors[xs[-1]])
    for a, b in zip(xs, xs[1:]):
        if a <= v <= b:
            frac = (v - a) / (b - a)
            return round(anchors[a] + frac * (anchors[b] - anchors[a]), 1)
    return float(anchors[xs[-1]])  # 不可达（保证路径完整）


def _overall_promote(prev_zt: pd.DataFrame, zt: pd.DataFrame) -> float | None:
    """晋级延续率 = |昨日涨停 ∩ 今日涨停| / |昨日涨停家数|（整体口径）。

    区别于连板梯队的分档晋级率（如 3进4）；prev_zt 为空 → None（剔除重归一）。
    """
    if prev_zt is None or prev_zt.empty:
        return None
    prev_codes = set(prev_zt["code"].astype(str))
    today_codes = set(zt["code"].astype(str))
    base = len(prev_codes)
    if base == 0:
        return None
    return round(len(prev_codes & today_codes) / base, 4)


def _day_components(zt, zb, dt, prev_zt, *, zb_ok: bool, dt_ok: bool) -> tuple[dict, dict, list[str]]:
    """单日各维子分 components + 原始值 raw + 缺失说明 missing。

    关键区分：`*_ok=False` 表示「数据缺失」→ 该维剔除重归一；
    `*_ok=True 且空表` 表示「真实 0 家」→ 该维计满分（如 0 跌停 → 100 分）。
    """
    components: dict[str, float] = {}
    raw: dict = {}
    missing: list[str] = []

    zt_count = len(zt)
    raw["zt_count"] = zt_count
    components["zt"] = _piecewise(zt_count, ZT_ANCHORS)

    max_lb = int(zt["lb_num"].max())
    raw["max_lb"] = max_lb
    components["height"] = _piecewise(max_lb, HEIGHT_ANCHORS)

    promo = _overall_promote(prev_zt, zt)
    if promo is None:
        missing.append("promote")
    else:
        raw["promote"] = promo
        components["promote"] = _piecewise(promo, PROMOTE_ANCHORS)

    if not zb_ok:
        missing.append("break")
    else:
        rate = len(zb) / (zt_count + len(zb)) if (zt_count + len(zb)) else 0.0
        raw["break_rate"] = round(rate, 4)
        components["break"] = _piecewise(rate, BREAK_ANCHORS)

    if not dt_ok:
        missing.append("dt")
    else:
        dt_count = len(dt)
        raw["dt_count"] = dt_count
        components["dt"] = _piecewise(dt_count, DT_ANCHORS)

    return components, raw, missing


def _weighted(components: dict) -> tuple[float | None, dict]:
    """加权合成：缺失维度剔除并重归一。返回 (score, weights_used)。"""
    used = {k: WEIGHTS[k] for k in components if components[k] is not None}
    total = sum(used.values())
    if not used or total == 0:
        return None, {}
    weights_used = {k: round(w / total, 4) for k, w in used.items()}
    score = sum(components[k] * weights_used[k] for k in used)
    return round(score, 1), weights_used


# ---------------------------------------------------------------- 阶段判定

def _judge_stage(series: list[float]) -> tuple[str, str]:
    """阶段判定 + 依据句。series: 有分的日序列，旧→新（升序）。

    规则链（方向优先于绝对带）：
      R1 冰点期：<45 且向下
      R2 修复期：<70 且向上（含低位回升）；或 <45 且向上
      R3 高潮期：≥70 且向上
      R4 退潮期：≥45 且向下（含高位回落，如 80→65）
      R5 单日兜底（无前日参照）：按绝对区间 ≥70 高潮 / ≥45 修复 / <45 冰点
    """
    today = series[-1]
    if len(series) >= 2:
        prev = series[-2]
        diff = today - prev
    else:
        prev = None
        diff = None

    if prev is None:
        if today >= SCORE_STAGE_HIGH:
            stage = "高潮期"
        elif today >= SCORE_STAGE_MID:
            stage = "修复期"
        else:
            stage = "冰点期"
        reason = f"情绪温度 {today:.0f} 分（仅单日，无方向参照），判定{stage}"
        return stage, reason

    arrow = "→".join(f"{s:.0f}" for s in series)
    if diff < -DIRECTION_EPS:
        stage = "冰点期" if today < SCORE_STAGE_MID else "退潮期"
    else:
        stage = "高潮期" if today >= SCORE_STAGE_HIGH else "修复期"
    reason = f"近{len(series)}日情绪温度 {arrow}，今日 {today:.0f} 分（较昨日 {diff:+.0f}），判定{stage}"
    return stage, reason


# ---------------------------------------------------------------- 主入口

def _unavailable(reason: str) -> dict:
    """数据不足时的统一返回（不引入第 5 个阶段值）。"""
    return {
        "available": False,
        "score": None,
        "stage": None,
        "stage_reason": reason,
        "components": {},
        "raw": {},
        "series": [],
        "weights_used": {},
        "days_used": 0,
        "notes": [reason],
    }


def compute_emotion(
    zt: pd.DataFrame,
    zb: pd.DataFrame,
    dt: pd.DataFrame,
    hist_days: list[dict],
    *,
    zb_ok: bool = True,
    dt_ok: bool = True,
    is_intraday: bool = False,
) -> dict:
    """情绪温度主入口。恒返回完整 dict，任何异常不 raise。

    hist_days: list[{date, zt, zb, dt, zb_ok, dt_ok}]，**旧→新**（不含今日，
    与 prev_pools 同序）；每日本身的 prev_zt 由序列内部相邻日推导。
    is_intraday: 盘中标记（trade_date==今日 且 当前时间 < 15:00）。
    """
    if zt.empty:
        return _unavailable("涨停池为空，情绪温度不可用")

    trade_date = str(zt["trade_date"].iloc[0])
    notes: list[str] = []
    if is_intraday:
        notes.append("盘中预览，数据未完整（建议收盘 15:00 后跑）")

    # 日序列（旧→新，含今日）；每日本身 prev_zt 由前一日 zt 推导
    records: list[dict] = []
    prev = None
    for h in hist_days:
        records.append({
            "date": h["date"],
            "zt": h["zt"], "zb": h["zb"], "dt": h["dt"],
            "zb_ok": h.get("zb_ok", True), "dt_ok": h.get("dt_ok", True),
            "prev_zt": prev,
        })
        prev = h["zt"]
    records.append({
        "date": trade_date,
        "zt": zt, "zb": zb, "dt": dt,
        "zb_ok": zb_ok, "dt_ok": dt_ok,
        "prev_zt": prev,
    })

    series: list[dict] = []
    for rec in records:
        if rec["zt"].empty:
            series.append({"date": rec["date"], "score": None, "ok": False, "missing": []})
            continue
        components, raw, missing = _day_components(
            rec["zt"], rec["zb"], rec["dt"], rec["prev_zt"],
            zb_ok=rec["zb_ok"], dt_ok=rec["dt_ok"],
        )
        score, weights_used = _weighted(components)
        series.append({
            "date": rec["date"],
            "score": score,
            "ok": score is not None,
            "components": components,
            "raw": raw,
            "missing": missing,
            "weights_used": weights_used,
        })

    today_ser = series[-1]
    if not today_ser["ok"]:
        return _unavailable("涨停池为空，情绪温度不可用")

    avail_scores = [s["score"] for s in series if s.get("ok")]
    stage, reason = _judge_stage(avail_scores)

    # 缺失说明
    for m in today_ser["missing"]:
        notes.append(f"{m} 数据缺失，该维未计入（权重已重归一）")
    missing_dates = [s["date"] for s in series if not s.get("ok")]
    if missing_dates:
        notes.append(f"序列缺日：{','.join(missing_dates)}，趋势按实际有分日判定")

    # series 输出：最新在前
    out_series = [
        {
            "date": s["date"],
            "score": s.get("score"),
            "weights_used": s.get("weights_used", {}),
            "missing": s.get("missing", []),
        }
        for s in reversed(series)
    ]

    return {
        "available": True,
        "trade_date": trade_date,
        "score": today_ser["score"],
        "stage": stage,
        "stage_reason": reason,
        "components": today_ser["components"],
        "raw": today_ser["raw"],
        "series": out_series,
        "weights_used": today_ser["weights_used"],
        "days_used": len(avail_scores),
        "notes": notes,
    }
