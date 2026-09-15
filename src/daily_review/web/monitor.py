"""盘中异常检测引擎（v0.33）：纯函数，检测 4 类异常并输出结构化的 Anomaly 对象。

搭配 MarketDaemon 使用：每轮轮询拿到 snapshot delta 后，调用 detect_anomalies()
判断该窗口是否出现异常，如有则推送到飞书。

检测规则：
1. 炸板潮（Broken Surge）  — 单窗口 ≥5 只炸板
2. 题材爆发（Theme Outbreak）— 同行业 ≥3 只新涨停（需 industry 映射）
3. 龙头异动（Leader Move）  — 空间板炸板/回封
4. 情绪骤变（Emotion Crash）— 涨停数较上轮降 >30%
"""

from __future__ import annotations

from dataclasses import dataclass, field


# 卡片颜色（与 feishu_gateway 一致）
CARD_COLOR_RED = "red"
CARD_COLOR_GREEN = "green"
CARD_COLOR_BLUE = "blue"

# ---------- 阈值 ----------

_BROKEN_SURGE_WARNING = 5    # ≥5 只炸板 → warning
_BROKEN_SURGE_ALERT = 10     # ≥10 只炸板 → alert
_THEME_OUTBREAK_MIN = 3      # 同行业 ≥3 只新涨停 → 题材爆发
_EMOTION_CRASH_RATIO = 0.7   # 涨停数 ≤ 上轮 70% → 情绪骤变


@dataclass
class Anomaly:
    """一次检测到的异常事件。"""

    type: str               # 异常类型："炸板潮" / "题材爆发" / "龙头异动" / "情绪骤变"
    severity: str           # 严重度："info" / "warning" / "alert"
    message: str            # 人类可读的描述
    stocks: list[str]       # 涉及股票代码列表（可为空）
    card_color: str = CARD_COLOR_BLUE  # 飞书卡片颜色


# ---------------------------------------------------------------- 检测入口


def detect_anomalies(
    baseline: dict,
    current_delta: dict,
    prev_delta: dict | None = None,
    space_board: dict | None = None,
    *,
    industry_map: dict[str, str] | None = None,
) -> list[Anomaly]:
    """检测当前窗口的市场异常。

    Args:
        baseline: take_baseline() 返回的基准 dict
        current_delta: snapshot() 返回的当前窗口 delta
        prev_delta: 上一轮 delta（首次为 None 时用 baseline 代替）
        space_board: 空间板信息，如 {"code": "600001", "lb_num": 6}
        industry_map: 代码 → 行业 映射（用于题材爆发检测）

    Returns:
        检测到的异常列表（可能为空）
    """
    anomalies: list[Anomaly] = []

    broken = current_delta.get("broken", [])
    new_zt = current_delta.get("new_zt", [])
    re_sealed = current_delta.get("re_sealed", [])

    # 1. 炸板潮
    _check_broken_surge(broken, anomalies)

    # 2. 题材爆发
    if industry_map is not None and new_zt:
        _check_theme_outbreak(new_zt, industry_map, anomalies)

    # 3. 龙头异动
    if space_board:
        _check_leader_move(space_board, broken, re_sealed, anomalies)

    # 4. 情绪骤变
    _check_emotion_crash(baseline, current_delta, prev_delta, anomalies)

    return anomalies


# ---------------------------------------------------------------- 各规则


def _check_broken_surge(broken: list[str], anomalies: list[Anomaly]) -> None:
    """炸板潮检测：单窗口 ≥5 只炸板。"""
    if len(broken) >= _BROKEN_SURGE_ALERT:
        anomalies.append(Anomaly(
            type="炸板潮",
            severity="alert",
            message=f"⚠️ 短时间内 {len(broken)} 只股票炸板，抛压显著！",
            stocks=broken,
            card_color=CARD_COLOR_RED,
        ))
    elif len(broken) >= _BROKEN_SURGE_WARNING:
        anomalies.append(Anomaly(
            type="炸板潮",
            severity="warning",
            message=f"⚠️ 盘中出现 {len(broken)} 只股票炸板，注意风险",
            stocks=broken,
            card_color=CARD_COLOR_RED,
        ))


def _check_theme_outbreak(
    new_zt: list[str],
    industry_map: dict[str, str],
    anomalies: list[Anomaly],
) -> None:
    """题材爆发检测：同行业 ≥3 只新涨停。

    按行业分组统计新涨停股票，行业命中 ≥3 只时触发。
    """
    industry_counts: dict[str, list[str]] = {}
    for code in new_zt:
        ind = industry_map.get(code, "其他")
        industry_counts.setdefault(ind, []).append(code)

    for ind, codes in industry_counts.items():
        if len(codes) >= _THEME_OUTBREAK_MIN:
            names = ", ".join(codes)
            anomalies.append(Anomaly(
                type="题材爆发",
                severity="warning" if len(codes) >= 5 else "info",
                message=f"🔥 题材「{ind}」批量涨停 {len(codes)} 只：{names}",
                stocks=codes,
                card_color=CARD_COLOR_GREEN,
            ))


def _check_leader_move(
    space_board: dict,
    broken: list[str],
    re_sealed: list[str],
    anomalies: list[Anomaly],
) -> None:
    """龙头异动检测：空间板炸板或回封。"""
    code = space_board.get("code", "")
    lb = space_board.get("lb_num", 0)
    if not code:
        return

    if code in broken:
        anomalies.append(Anomaly(
            type="龙头异动",
            severity="alert",
            message=f"💥 空间板 {code}（{lb} 连板）炸板！",
            stocks=[code],
            card_color=CARD_COLOR_RED,
        ))
    elif code in re_sealed:
        anomalies.append(Anomaly(
            type="龙头异动",
            severity="info",
            message=f"✅ 空间板 {code}（{lb} 连板）回封",
            stocks=[code],
            card_color=CARD_COLOR_GREEN,
        ))


def _check_emotion_crash(
    baseline: dict,
    current_delta: dict,
    prev_delta: dict | None,
    anomalies: list[Anomaly],
) -> None:
    """情绪骤变检测：涨停数较上轮降 >30%。"""
    cur_zt = current_delta.get("zt_count", 0)

    # 取参照：优先用上轮，没有则用 baseline
    ref_zt = None
    if prev_delta is not None:
        ref_zt = prev_delta.get("zt_count")
    if ref_zt is None:
        ref_zt = baseline.get("zt_count", 0)

    if ref_zt > 0 and cur_zt < ref_zt * _EMOTION_CRASH_RATIO:
        drop_pct = round((1 - cur_zt / ref_zt) * 100)
        anomalies.append(Anomaly(
            type="情绪骤变",
            severity="warning",
            message=f"📉 涨停数骤降 {drop_pct}%（{ref_zt} → {cur_zt}），情绪降温",
            stocks=[],
            card_color=CARD_COLOR_RED,
        ))