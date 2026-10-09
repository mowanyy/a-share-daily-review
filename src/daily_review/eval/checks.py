"""L0 确定性校验（v0.37）：结构与纪律规则，纯函数、零 LLM、零网络。

规则清单（docs/Agent内容评估方案.md 第三节）：
- EVAL-001 产物存在且非空
- EVAL-002 章节结构完整（复盘七章 / 隔夜预案三节 / 开盘策略三节）
- EVAL-003 交易日合法性（离线读交易日历，不触发联网刷新）
- EVAL-004 数据缺失标注纪律（快照 unavailable 时报告须标注「数据缺失」）
- EVAL-005 合规扫描（复用飞书网关 is_compliance_risk 词汇；强荐股话术 = error）
- EVAL-006 正文长度下限（warn）
"""

from __future__ import annotations

import re
from pathlib import Path

from daily_review.config import get_settings
from daily_review.eval.models import EVAL_MIN_BODY_LEN, CheckResult
from daily_review.eval.extract import extract_emotion_score
from daily_review.web.feishu_gateway import COMPLIANCE_REPLY

# 各报告类型的必备章节（匹配 `## / ### 标题` 前缀）
REVIEW_SECTIONS = [
    "一、总览", "二、情绪温度", "三、连板梯队", "四、题材运行周期与归类",
    "五、炸板与资金", "六、龙虎榜与游资", "七、次日预案",
]
PLAN_SECTIONS = ["1. 隔夜消息面汇总", "2. 消息-题材联动分析", "3. 今日关注方向"]
OPEN_SECTIONS = ["1. 竞价总览", "2. 有机会的个股清单", "3. 开盘执行提示"]
REPORT_SECTIONS = {"review": REVIEW_SECTIONS, "plan": PLAN_SECTIONS, "open": OPEN_SECTIONS}

# 数据缺失/重算标注词（prompt 输出纪律要求）
_MISSING_MARKERS = ("数据缺失", "数据不足", "未更新", "数据重算")

# 强荐股话术（明确越过合规边界 → error）；普通交易建议用语（→ warn）
_STRONG_COMPLIANCE = re.compile(
    r"(?:建议|推荐|推荐一只|推荐一个|立即|果断|务必)(?:买入|卖出|买进|加仓|减仓|清仓|追涨|抄底)"
    r"|(?:荐股|保证收益|稳赚|稳赚不赔|包赚|带单|跟单)"
)
# 交易建议用语（建议框里出现即提示复核；不含「净买入」等数据类词）
_REPORT_ADVISORY_WORDS = ("买入", "卖出", "买进", "加仓", "减仓", "清仓", "持仓", "重仓", "满仓", "追涨", "抄底")

_HEADING_RE = re.compile(r"(?m)^\s*#{2,3}\s+")
_BODY_HEADING_RE = re.compile(r"(?m)^[#>].*$")


def _llm_text(md: str, report_type: str) -> str:
    """只取 LLM 生成章节（review=一总览+七次日预案；plan/open 全文均为 LLM 生成）。

    数据章节（二~六）由代码拼接且含「净买入/涨停家数」等数据词，不参与合规与纪律扫描。
    """
    if report_type != "review":
        return md
    out: list[str] = []
    for part in re.split(r"(?m)^## ", md):
        if part.startswith("一、总览") or part.startswith("七、次日预案"):
            out.append(part)
    return "\n".join(out)


def read_trade_calendar_offline() -> set[str] | None:
    """离线读交易日历（data/trade_calendar.csv），不触发 trade_calendar 模块的联网刷新。"""
    p = get_settings().data_dir / "trade_calendar.csv"
    try:
        if not p.exists():
            return None
        return {ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if re.fullmatch(r"\d{8}", ln.strip())}
    except OSError:
        return None


def is_trade_date_offline(trade_date: str) -> bool | None:
    """离线判定：True=交易日 / False=休市 / None=表缺失或未覆盖（未知，勿判休市）。"""
    if not re.fullmatch(r"\d{8}", trade_date):
        return None
    dates = read_trade_calendar_offline()
    if not dates:
        return None
    if trade_date > max(dates):
        return None  # 表未覆盖（未来）→ 未知
    return trade_date in dates


def _ok(check_id: str, message: str) -> CheckResult:
    return CheckResult(check_id, "pass", True, message)


def _warn(check_id: str, message: str) -> CheckResult:
    return CheckResult(check_id, "warn", False, message)


def _error(check_id: str, message: str) -> CheckResult:
    return CheckResult(check_id, "error", False, message)


def _skip(check_id: str, message: str) -> CheckResult:
    return CheckResult(check_id, "skip", False, message)


# ---------------------------------------------------------------- 单条规则


def check_file_exists(path: Path) -> CheckResult:
    """EVAL-001 产物存在且非空。"""
    if not path.exists():
        return _error("EVAL-001", f"产物不存在：{path}")
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return _error("EVAL-001", f"产物为空：{path}")
    return _ok("EVAL-001", f"产物存在且非空（{len(text)} 字符）")


def check_sections(md: str, report_type: str) -> CheckResult:
    """EVAL-002 章节结构完整。"""
    missing = []
    for s in REPORT_SECTIONS.get(report_type, []):
        pat = re.compile(rf"(?m)^\s*#{{2,3}}\s*{re.escape(s)}")
        if not pat.search(md):
            missing.append(s)
    if missing:
        return _error("EVAL-002", f"缺少章节：{'、'.join(missing)}")
    return _ok("EVAL-002", f"{report_type} 章节结构完整（{len(REPORT_SECTIONS[report_type])} 节）")


def check_trade_date(trade_date: str) -> CheckResult:
    """EVAL-003 交易日合法性（离线判定，不联网刷新）。"""
    result = is_trade_date_offline(trade_date)
    if result is None:
        return _skip("EVAL-003", "交易日历缺失/未覆盖该日期，跳过判定")
    if result is False:
        return _error("EVAL-003", f"{trade_date} 非交易日，却生成了报告")
    return _ok("EVAL-003", f"{trade_date} 为交易日")


def check_missing_discipline(
    md: str,
    report_type: str,
    indicators: dict | None,
    prev_indicators: dict | None,
) -> CheckResult:
    """EVAL-004 数据缺失标注纪律（软告警：快照可能过期，不硬判造假）。"""
    has_marker = any(mk in md for mk in _MISSING_MARKERS)
    if report_type == "review":
        if indicators is None:
            return _skip("EVAL-004", "快照缺失，无法判定数据缺失纪律")
        emotion = indicators.get("emotion")
        if not isinstance(emotion, dict):
            return _skip("EVAL-004", "快照无情绪温度维度，跳过")
        if emotion.get("available") is False:
            if has_marker:
                return _ok("EVAL-004", "快照数据不可用，报告已按纪律标注「数据缺失」")
            return _warn("EVAL-004", "快照显示情绪温度不可用，但报告未标注「数据缺失」（可能快照过期或 LLM 漏标）")
        if emotion.get("available") is True:
            if has_marker and not extract_emotion_score(md):
                return _warn("EVAL-004", "报告标注数据缺失但快照有值（可能误标）")
            return _ok("EVAL-004", "情绪温度数据可用，无缺失标注问题")
        return _skip("EVAL-004", "情绪温度可用性未知，跳过")
    # plan / open：引用「昨日」快照；前日快照缺失时必须显式标注来源（v0.30 纪律）
    if prev_indicators is None:
        if has_marker:
            return _ok("EVAL-004", "前日快照缺失，报告已标注「数据重算/缺失」来源")
        return _warn("EVAL-004", "前日快照缺失，报告未标注「数据重算/缺失」来源（v0.30 纪律）")
    return _ok("EVAL-004", "前日快照存在，昨日数值已锚定")


def check_compliance(md: str, report_type: str) -> CheckResult:
    """EVAL-005 合规扫描（只扫 LLM 生成章节）：

    - 命中强荐股话术（「建议买入」「荐股」等）→ error；
    - 命中交易建议用语（买入/卖出/加仓…）→ warn（对外推送前复核合规边界）；
    - 其余 → pass。不直接用飞书网关 is_compliance_risk：其例外词表（涨停/炸板）
      按用户提问意图设计，全文扫描会被豁免形同虚设，且「净买入」等数据词会误报。
    """
    text = _llm_text(md, report_type)
    if _STRONG_COMPLIANCE.search(text):
        return _error("EVAL-005", "命中强荐股话术（描述性复盘不应给出具体交易建议）")
    hits = [w for w in _REPORT_ADVISORY_WORDS if w in text]
    if hits:
        return _warn(
            "EVAL-005",
            f"LLM 章节命中交易建议用语：{'、'.join(hits)}（对外推送前请核对合规边界；{COMPLIANCE_REPLY}）",
        )
    return _ok("EVAL-005", "LLM 章节未命中交易建议用语")


def check_min_length(md: str) -> CheckResult:
    """EVAL-006 正文长度下限（warn）。"""
    body = _BODY_HEADING_RE.sub("", md)
    n = len(body.strip())
    if n < EVAL_MIN_BODY_LEN:
        return _warn("EVAL-006", f"正文过短（{n} 字 < {EVAL_MIN_BODY_LEN}），可能输出被截断")
    return _ok("EVAL-006", f"正文长度 {n} 字达标")


# ---------------------------------------------------------------- 汇总


def check_all(
    md: str,
    trade_date: str,
    report_type: str,
    indicators: dict | None,
    prev_indicators: dict | None,
    *,
    path: Path | None = None,
) -> list[CheckResult]:
    """跑全部 L0 规则（EVAL-001..006）。"""
    out: list[CheckResult] = []
    if path is not None:
        out.append(check_file_exists(path))
    out.append(check_sections(md, report_type))
    out.append(check_trade_date(trade_date))
    out.append(check_missing_discipline(md, report_type, indicators, prev_indicators))
    out.append(check_compliance(md, report_type))
    out.append(check_min_length(md))
    return out