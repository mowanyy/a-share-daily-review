"""连板梯队指标计算（对齐 docs/数据结构.md 的 LadderStats）。

输入：当日涨停池 / 当日炸板池 / 昨日涨停池 / 近 N 日空间板高度序列。
晋级率（promotion）定义：昨日 N 板股中今日封上 N+1 板的比例。
"""

from __future__ import annotations

import pandas as pd

# 炸板次数明显阈值 / 封单薄弱阈值（元）——集中定义，便于调整
WEAK_OPEN_TIMES = 3
WEAK_SEAL_AMOUNT = 50_000_000  # 封单 < 5000 万视为薄弱


def _compute_height_position(height_series: list[dict]) -> dict:
    """分析空间板高度在历史序列中的位置。

    height_series: 近 N 日（含今日）空间板高度序列，最新日期在前。
    返回：
        { "current": int, "max": int, "min": int,
          "percentile": float, "label": "高位"|"中位"|"低位"|"仅1日",
          "trend": "上升"|"下降"|"持平" }
    - 仅 1 个数据点（只有今日，无历史）：返回 "仅1日" 标签
    - 所有值相同：返回 "中位" 标签
    - trend 根据今日与昨日高度比较得出
    """
    if not height_series:
        return {"current": 0, "max": 0, "min": 0, "percentile": 0.0, "label": "无数据", "trend": "持平"}
    values = [s["max_lb"] for s in height_series]
    current = values[0]  # 最新（今日）
    max_val = max(values)
    min_val = min(values)

    # 趋势：今日 vs 昨日
    yesterday = values[1] if len(values) > 1 else current
    if current > yesterday:
        trend = "上升"
    elif current < yesterday:
        trend = "下降"
    else:
        trend = "持平"

    if len(values) == 1:
        label = "仅1日"
        pct = 0.5
    elif max_val == min_val:
        # 所有值相同 → 中位
        label = "中位"
        pct = 0.5
    else:
        # 百分位：当前值在序列中从小到大排的位置
        sorted_vals = sorted(values)
        rank = sorted_vals.index(current)
        pct = rank / (len(sorted_vals) - 1)
        if pct >= 0.7:
            label = "高位"
        elif pct <= 0.3:
            label = "低位"
        else:
            label = "中位"
    return {
        "current": current,
        "max": max_val,
        "min": min_val,
        "percentile": round(pct, 2),
        "label": label,
        "trend": trend,
    }


def _max_lb_stock(zt: pd.DataFrame) -> str:
    """空间板个股：取连板数最高的；并列时取封板最早的。"""
    if zt.empty:
        return ""
    max_lb = zt["lb_num"].max()
    top = zt[zt["lb_num"] == max_lb].copy()
    top = top.sort_values("first_limit_time")
    return str(top.iloc[0]["name"])


def compute_promotion(prev_zt: pd.DataFrame, zt: pd.DataFrame) -> dict[str, float | None]:
    """晋级率：{N进N+1: 比例}。昨日 N 板家数为 0 时返回 None（无数据）。"""
    if prev_zt.empty:
        return {}
    # 今日 代码→连板数
    today_lb = dict(zip(zt["code"], zt["lb_num"]))
    promotion: dict[str, float | None] = {}
    max_prev = int(prev_zt["lb_num"].max())
    for h in range(1, max_prev + 1):
        prev_h = prev_zt[prev_zt["lb_num"] == h]
        base = len(prev_h)
        if base == 0:
            continue
        promoted = sum(1 for c in prev_h["code"] if today_lb.get(c) == h + 1)
        promotion[f"{h}进{h + 1}"] = round(promoted / base, 4)
    return promotion


def _build_ladder_table(zt: pd.DataFrame) -> list[dict]:
    """梯队分组表：每高度数量 + 代表股 + 炸板/封单异常标记。"""
    if zt.empty:
        return []
    table: list[dict] = []
    for h in range(int(zt["lb_num"].max()), 0, -1):
        layer = zt[zt["lb_num"] == h].copy()
        layer = layer.sort_values(["first_limit_time", "seal_amount"], ascending=[True, False])
        stocks = []
        for _, r in layer.head(2).iterrows():
            tag = f'{r["industry"] or "-"}'.strip()
            stocks.append(f'{r["code"]} {r["name"]} {r["first_limit_time"] or "?"} 封 {tag}')
        weak = [
            f'{r["code"]} {r["name"]}'
            for _, r in layer.iterrows()
            if r["open_times"] >= WEAK_OPEN_TIMES or (r["seal_amount"] or 0) < WEAK_SEAL_AMOUNT
        ]
        table.append({
            "height": h,
            "count": len(layer),
            "stocks": stocks,
            "weak": weak,
        })
    return table


def compute_ladder(
    zt: pd.DataFrame,
    zb: pd.DataFrame,
    prev_zt: pd.DataFrame,
    height_series: list[dict],
    long_height_series: list[dict] | None = None,
) -> dict:
    """计算 LadderStats 指标与梯队表。

    height_series: 近 N 日（含今日）空间板高度序列，元素 `{"date": YYYYMMDD, "max_lb": int}`，
    最新日期在前。
    long_height_series: 可选，更长窗口高度序列（如 20 个交易日），用于更稳健的位置统计。
    若提供，则 height_position 基于此计算；否则回退到 height_series。
    """
    zt_count = len(zt)
    lianban_count = int((zt["lb_num"] >= 2).sum()) if zt_count else 0
    max_lb = int(zt["lb_num"].max()) if zt_count else 0
    max_lb_stock = _max_lb_stock(zt)
    break_count = len(zb)
    break_rate = round(break_count / (zt_count + break_count), 4) if (zt_count + break_count) else 0.0
    promotion = compute_promotion(prev_zt, zt)
    ladder = _build_ladder_table(zt)
    first_board_count = int((zt["lb_num"] == 1).sum()) if zt_count else 0

    # 长序列优先用于位置统计
    hs = long_height_series if long_height_series is not None else height_series

    return {
        "trade_date": str(zt["trade_date"].iloc[0]) if zt_count else "",
        "zt_count": zt_count,
        "lianban_count": lianban_count,
        "max_lb": max_lb,
        "max_lb_stock": max_lb_stock,
        "first_board_count": first_board_count,
        "break_count": break_count,
        "break_rate": break_rate,
        "promotion": promotion,
        "ladder": ladder,
        "height_series": height_series,
        "height_position": _compute_height_position(hs),
    }
