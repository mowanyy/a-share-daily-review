"""炸板净流入分析（对齐 docs/数据结构.md 的 MoneyFlow 应用）。

炸板股 × 资金流，按主力净流入降序，给出信号分类与次日关注小结。
"""

from __future__ import annotations

import pandas as pd

# 信号阈值（集中配置，见 module.break prompt「阈值由程序配置」）
HIGH_UP_PCT = 5.0       # 收盘涨幅 ≥ 5% 视为「较高」
POS_INFLOW = 0.0        # 主力净流入 > 0 为正
NEG_INFLOW = -20_000_000  # 主力净流入 < -2000 万视为「显著为负」
WARN_BREAK_TIMES = 3    # 炸板 ≥ 3 次视为封板意愿反复


def _signal(main: float | None, up_pct: float | None, break_times: int) -> str:
    if main is None:
        return "缺资金流"
    if main > POS_INFLOW:
        if break_times >= WARN_BREAK_TIMES:
            return "⚠️ 谨慎（净流入为正但炸板次数多）"
        if (up_pct or 0) >= HIGH_UP_PCT:
            return "🟢 反包关注"
        return "🟢 关注"
    if main < NEG_INFLOW:
        return "🔴 规避（资金显著流出）"
    return "🔴 观察（资金净流出）"


def analyze_break(zb: pd.DataFrame, moneyflow: pd.DataFrame, break_rate: float | None = None) -> dict:
    """炸板净流入排序表 + 信号 + 次日关注小结。"""
    if zb.empty:
        return {"break_count": 0, "break_rate": break_rate or 0.0, "table": [], "watch": []}

    flow = moneyflow.set_index("code")
    rows = []
    for _, r in zb.iterrows():
        code = str(r["code"])
        fl = flow.loc[code] if code in flow.index else None
        main = None if fl is None else fl["main_net_inflow"]
        rows.append({
            "code": code,
            "name": str(r["name"]),
            "industry": str(r["industry"] or ""),
            "break_times": int(r["break_times"]),
            "first_seal_time": r["first_seal_time"] or "",
            "up_pct": None if pd.isna(r["up_pct"]) else round(float(r["up_pct"]), 2),
            "main_net_inflow": None if main is None else round(float(main), 2),
            "super_net_inflow": None if fl is None or pd.isna(fl["super_net_inflow"]) else round(float(fl["super_net_inflow"]), 2),
            "big_net_inflow": None if fl is None or pd.isna(fl["big_net_inflow"]) else round(float(fl["big_net_inflow"]), 2),
            "signal": _signal(main, r["up_pct"], int(r["break_times"])),
        })

    # 主力净流入降序；无资金流数据排末尾
    rows.sort(key=lambda x: (x["main_net_inflow"] is None, -x["main_net_inflow"] if x["main_net_inflow"] is not None else 0))
    # 次日关注：主力净流入为正且涨幅较高者优先
    watch = [
        {k: x[k] for k in ("code", "name", "industry", "break_times", "up_pct", "main_net_inflow", "signal")}
        for x in rows if x["main_net_inflow"] is not None and x["main_net_inflow"] > POS_INFLOW
    ][:3]

    return {
        "break_count": len(zb),
        "break_rate": break_rate,
        "table": rows,
        "watch": watch,
    }
