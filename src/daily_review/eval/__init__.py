"""Agent 生成内容评估（v0.37 L0/L1）：确定性校验 + 数据回比，零 LLM 零网络。

L0（checks.py）：结构与纪律校验——产物存在/章节完整/交易日合法/数据缺失标注/合规/长度。
L1（verify.py）：生成内容与权威快照（data/review_snapshots/{date}.json）回比——
  情绪温度/涨停/连板/首板/最高板/龙头/炸板率/晋级率/昨日情绪温度。
明确不做 LLM 互评（成本高、judge 自身幻觉）；评估本身不调 LLM、不发网络请求。

入口：evaluate_report(trade_date, report_type) → EvalReport
CLI：python -m daily_review eval --date YYYYMMDD --type review|plan|open [--json]
详见 docs/Agent内容评估方案.md。
"""

from __future__ import annotations

from pathlib import Path

from daily_review.config import get_settings
from daily_review.analysis.review_snapshot import load_review_snapshot
from daily_review.eval import checks
from daily_review.eval.models import EvalReport, CheckResult
from daily_review.eval.verify import verify_all

# 报告类型 → 产物文件名后缀
REPORT_SUFFIX = {
    "review": "复盘",
    "plan": "隔夜预案",
    "open": "开盘策略",
}
REPORT_TYPES: tuple[str, ...] = tuple(REPORT_SUFFIX)


def prev_trade_date_offline(trade_date: str) -> str | None:
    """离线求前一个交易日（仅读 data/trade_calendar.csv，不联网刷新）。"""
    dates = checks.read_trade_calendar_offline()
    if not dates:
        return None
    before = [d for d in dates if d < trade_date]
    return max(before) if before else None


def evaluate_report(
    trade_date: str,
    report_type: str = "review",
    *,
    output_path: str | Path | None = None,
    indicators: dict | None = None,
    prev_indicators: dict | None = None,
) -> EvalReport:
    """评估某日某类型报告的生成内容，返回 EvalReport（不写库，由调用方决定入库）。

    output_path: 缺省 output/{trade_date}_{类型后缀}.md
    indicators:  权威快照的 indicators；缺省 load_review_snapshot(trade_date)
    prev_indicators: plan/open 引用「昨日情绪温度」用前日快照；缺省自动按离线日历求前日
    """
    if report_type not in REPORT_SUFFIX:
        raise ValueError(f"未知报告类型：{report_type}（可选 {REPORT_TYPES}）")
    if not (isinstance(trade_date, str) and len(trade_date) == 8 and trade_date.isdigit()):
        raise ValueError(f"非法交易日：{trade_date}（应为 YYYYMMDD）")

    if output_path is None:
        output_path = get_settings().output_dir / f"{trade_date}_{REPORT_SUFFIX[report_type]}.md"
    path = Path(output_path)
    md = path.read_text(encoding="utf-8") if path.exists() else ""

    if indicators is None:
        indicators = load_review_snapshot(trade_date)
    if prev_indicators is None and report_type in ("plan", "open"):
        prev_date = prev_trade_date_offline(trade_date)
        if prev_date:
            prev_indicators = load_review_snapshot(prev_date)

    # 产物缺失 → 只报 EVAL-001，其余检查无意义（避免级联误报）
    if not md:
        return EvalReport(
            trade_date=trade_date,
            report_type=report_type,
            checks=[CheckResult("EVAL-001", "error", False, f"产物不存在或为空：{path}")],
        )

    results = checks.check_all(md, trade_date, report_type, indicators, prev_indicators, path=path)
    results += verify_all(md, report_type, indicators, prev_indicators)
    return EvalReport(trade_date=trade_date, report_type=report_type, checks=results)


__all__ = [
    "REPORT_SUFFIX", "REPORT_TYPES", "evaluate_report", "prev_trade_date_offline",
    "EvalReport", "CheckResult",
]