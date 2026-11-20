"""L1 数据回比（v0.37）：生成内容中的关键数字与权威快照比对，零 LLM 零网络。

规则清单（docs/Agent内容评估方案.md 第四节）：
- EVAL-101 情绪温度分数（绝对差 ≤ 1.0 分）
- EVAL-102/103/104 涨停 / 连板 / 首板家数（相对误差 ≤ 5%）
- EVAL-105 最高板高度（精确相等）
- EVAL-106 空间板龙头名称（文本相等）
- EVAL-107 炸板率（快照为小数 0.202，报告为百分比 20.2%，统一为百分比比对）
- EVAL-108 晋级率（逐对「3进4 12.68%」）
- EVAL-109 昨日情绪温度（仅 plan/open，对照前日快照）
- EVAL-111 题材龙头名称出现在报告中（warn，代码拼装章节恒应命中）

快照缺失的字段一律 skip，不误报（文档 4.3 节）。
"""

from __future__ import annotations

from daily_review.eval.models import EVAL_EMOTION_ABS_TOL, EVAL_NUM_REL_TOL, CheckResult
from daily_review.eval.extract import (
    extract_break_rate,
    extract_emotion_score,
    extract_first_board_count,
    extract_lianban_count,
    extract_max_lb,
    extract_max_lb_stock,
    extract_promotions,
    extract_prev_emotion,
    extract_zt_count,
)


def _pass(cid: str, message: str) -> CheckResult:
    return CheckResult(cid, "pass", True, message)


def _fail(cid: str, message: str) -> CheckResult:
    return CheckResult(cid, "error", False, message)


def _skip(cid: str, message: str) -> CheckResult:
    return CheckResult(cid, "skip", False, message)


def _num_compare(cid: str, label: str, got: float | None, want: float | None,
                 *, rel_tol: float | None = None, abs_tol: float | None = None,
                 suffix: str = "") -> CheckResult:
    """通用数值回比。got/want 任一为 None → skip（报告未写 / 快照无数据）。"""
    if got is None:
        return _skip(cid, f"报告未出现「{label}」，跳过")
    if want is None:
        return _skip(cid, f"快照无「{label}」数据，跳过")
    if abs_tol is not None:
        ok = abs(got - want) <= abs_tol
        diff = f"差 {abs(got - want):.2f}（容差 ±{abs_tol}）"
    else:
        tol = rel_tol if rel_tol is not None else EVAL_NUM_REL_TOL
        ok = (abs(got - want) <= tol * abs(want)) if want else (got == want)
        diff = f"偏差 {abs(got - want) / abs(want) * 100:.1f}%（容差 {tol * 100:.0f}%）" if want else "快照为 0"
    detail = f"{label}：报告 {got}{suffix} vs 快照 {want}{suffix}"
    if ok:
        return _pass(cid, detail)
    return _fail(cid, f"{detail}（{diff}）")


# ---------------------------------------------------------------- 单条回比


def verify_emotion_score(text: str, indicators: dict | None) -> CheckResult:
    """EVAL-101 情绪温度分数。"""
    want = None
    if indicators:
        emotion = indicators.get("emotion")
        if isinstance(emotion, dict) and emotion.get("available"):
            want = emotion.get("score")
    return _num_compare(
        "EVAL-101", "情绪温度", extract_emotion_score(text), want,
        abs_tol=EVAL_EMOTION_ABS_TOL, suffix="分",
    )


def verify_zt_count(text: str, indicators: dict | None) -> CheckResult:
    """EVAL-102 涨停家数。"""
    ladder = indicators.get("ladder") if indicators else None
    want = ladder.get("zt_count") if isinstance(ladder, dict) else None
    return _num_compare("EVAL-102", "涨停家数", extract_zt_count(text), want)

def verify_lianban_count(text: str, indicators: dict | None) -> CheckResult:
    """EVAL-103 连板家数。"""
    ladder = indicators.get("ladder") if indicators else None
    want = ladder.get("lianban_count") if isinstance(ladder, dict) else None
    return _num_compare("EVAL-103", "连板家数", extract_lianban_count(text), want)

def verify_first_board_count(text: str, indicators: dict | None) -> CheckResult:
    """EVAL-104 首板家数。"""
    ladder = indicators.get("ladder") if indicators else None
    want = ladder.get("first_board_count") if isinstance(ladder, dict) else None
    return _num_compare("EVAL-104", "首板家数", extract_first_board_count(text), want)


def verify_max_lb(text: str, indicators: dict | None) -> CheckResult:
    """EVAL-105 最高板高度（精确相等）。"""
    ladder = indicators.get("ladder") if indicators else None
    want = ladder.get("max_lb") if isinstance(ladder, dict) else None
    return _num_compare("EVAL-105", "最高板高度", extract_max_lb(text), want, rel_tol=0.0, suffix="板")


def verify_max_lb_stock(text: str, indicators: dict | None) -> CheckResult:
    """EVAL-106 空间板龙头名称。"""
    got = extract_max_lb_stock(text)
    ladder = indicators.get("ladder") if indicators else None
    want = ladder.get("max_lb_stock") if isinstance(ladder, dict) else None
    if not got:
        return _skip("EVAL-106", "报告未出现「空间板/最高板 N 板（名称）」，跳过")
    if not want:
        return _skip("EVAL-106", "快照无空间板龙头数据，跳过")
    got_name = got.replace("⚠", "").strip()
    if want == got_name or want in got_name or got_name in want:
        return _pass("EVAL-106", f"空间板龙头：报告与快照一致（{want}）")
    return _fail("EVAL-106", f"空间板龙头：报告 {got_name} vs 快照 {want}")


def verify_break_rate(text: str, indicators: dict | None) -> CheckResult:
    """EVAL-107 炸板率（快照小数 → 百分比统一比对）。"""
    ladder = indicators.get("ladder") if indicators else None
    want_pct = None
    if isinstance(ladder, dict) and ladder.get("break_rate") is not None:
        want_pct = ladder["break_rate"] * 100
    return _num_compare("EVAL-107", "炸板率", extract_break_rate(text), want_pct, suffix="%")


def verify_promotions(text: str, indicators: dict | None) -> CheckResult:
    """EVAL-108 晋级率：逐对比对，任一不符 → error；均无法判定 → skip。"""
    ladder = indicators.get("ladder") if indicators else None
    promo = ladder.get("promotion") if isinstance(ladder, dict) else None
    pairs = extract_promotions(text)
    if not pairs:
        return _skip("EVAL-108", "报告未出现「N进N 百分比」表述，跳过")
    if not isinstance(promo, dict) or not promo:
        return _skip("EVAL-108", "快照无晋级率数据，跳过")
    checked = 0
    for key, got_pct in pairs:
        want_frac = promo.get(key)
        if want_frac is None:
            continue
        checked += 1
        res = _num_compare(
            "EVAL-108", f"{key}晋级率", got_pct, want_frac * 100,
            suffix="%",
        )
        if res.level == "error":
            return res
    if checked == 0:
        return _skip("EVAL-108", "报告中的晋级表述快照无对应数据，跳过")
    return _pass("EVAL-108", f"晋级率一致（核对 {checked} 对）")


def verify_prev_emotion(text: str, indicators: dict | None) -> CheckResult:
    """EVAL-109 昨日情绪温度（plan/open）：对照前日快照。"""
    want = None
    if indicators:
        emotion = indicators.get("emotion")
        if isinstance(emotion, dict) and emotion.get("available"):
            want = emotion.get("score")
    return _num_compare(
        "EVAL-109", "昨日情绪温度", extract_prev_emotion(text), want,
        abs_tol=EVAL_EMOTION_ABS_TOL, suffix="分",
    )


def verify_theme_leaders(text: str, indicators: dict | None) -> CheckResult:
    """EVAL-111 题材龙头名称出现在报告中（代码拼装章节恒应命中，warn 即可）。"""
    themes = indicators.get("themes") if indicators else None
    if not isinstance(themes, list) or not themes:
        return _skip("EVAL-111", "快照无题材数据，跳过")
    names = [t["leader"]["name"] for t in themes if isinstance(t, dict) and t.get("leader") and t["leader"].get("name")]
    names = [n for n in names if n]
    if not names:
        return _skip("EVAL-111", "快照题材无龙头名称，跳过")
    missing = [n for n in names if n not in text]
    if missing:
        return CheckResult("EVAL-111", "warn", False, f"题材龙头未出现在报告中：{'、'.join(missing)}（代码拼装章节，正常应恒有）")
    return _pass("EVAL-111", f"题材龙头均出现在报告中（{len(names)} 位）")


# ---------------------------------------------------------------- 汇总


def verify_all(
    text: str,
    report_type: str,
    indicators: dict | None,
    prev_indicators: dict | None,
) -> list[CheckResult]:
    """按报告类型选择 L1 回比规则。"""
    out: list[CheckResult] = []
    if report_type == "review":
        out.append(verify_emotion_score(text, indicators))
        out.append(verify_zt_count(text, indicators))
        out.append(verify_lianban_count(text, indicators))
        out.append(verify_first_board_count(text, indicators))
        out.append(verify_max_lb(text, indicators))
        out.append(verify_max_lb_stock(text, indicators))
        out.append(verify_break_rate(text, indicators))
        out.append(verify_promotions(text, indicators))
        out.append(verify_theme_leaders(text, indicators))
    else:  # plan / open：引用昨日数据，仅回比「昨日情绪温度」
        out.append(verify_prev_emotion(text, prev_indicators))
    return out