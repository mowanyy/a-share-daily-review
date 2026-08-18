"""定时推送（v0.21）：生成报告 → 提取标题+摘要 → 推送飞书群机器人。

复用现有生成函数（report/premarket），摘要确定性提取（不调 LLM），
推送走 notify.send_feishu。交易时段语义：
- review：盘后 18:00（龙虎榜 18:00 完整后）
- plan：盘前 07:30（隔夜消息面）
- open：09:25-09:30（竞价后个股筛选，v0.22 起本地计划任务准点触发）

供 GitHub Actions 定时调用（python -m daily_review push --type X），也供本地手动调试。
v0.21.7：失败/跳过也会向飞书推一条 ⏭/❌ 状态提示（避免「没推=无声」，见 _with_status_notice）。
v0.23：A2 幂等——(type,date) 已推送过则跳过（--force 强制重推），状态记在
      data/push_state.json（云端 runner 是全新环境无共享状态，幂等按同源生效）。
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from daily_review.config import get_settings
from daily_review.data import eastmoney_pool
from daily_review.notify import FeishuError, send_feishu

REPORT_TYPE_LABEL = {"review": "复盘报告", "plan": "隔夜预案", "open": "开盘策略"}
_REPORT_TYPES = tuple(REPORT_TYPE_LABEL)

_SHANGHAI = ZoneInfo("Asia/Shanghai")


class NoDataError(RuntimeError):
    """非交易日 / 无数据：跳过推送（不是错误）。"""


# ---------------------------------------------------------------- 时间工具


def beijing_now() -> datetime:
    """北京时间当前时刻（GitHub runner 是 UTC，这里强制用东八区）。"""
    return datetime.now(_SHANGHAI)


def beijing_today() -> str:
    return beijing_now().strftime("%Y%m%d")


def _find_prev_trade_date(trade_date: str) -> str:
    """trade_date 的前一交易日（等价 cli._find_prev_trade_date，避免循环 import）。"""
    dates = eastmoney_pool.resolve_recent_trade_dates(trade_date, n_days=2)
    if not dates:
        return trade_date
    if dates[0] == trade_date and len(dates) > 1:
        return dates[1]
    return dates[0]


# ---------------------------------------------------------------- 摘要提取


def _clean_md(line: str) -> str:
    """单行 Markdown 清理：去标题 #、加粗 **、列表符 -、行内代码 `。"""
    s = line.strip()
    s = re.sub(r"^#{1,6}\s*", "", s)
    s = re.sub(r"^[-*+]\s+", "", s)
    s = s.replace("**", "")
    s = s.replace("`", "")
    return s.strip()


def _extract_section(md_text: str, section_title: str, max_lines: int = 15) -> str:
    """提取某个 ## 章节的正文（到下一处 # 标题为止）。"""
    lines = md_text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.strip().startswith("#") and section_title in ln:
            start = i + 1
            break
    if start is None:
        return ""
    body: list[str] = []
    for ln in lines[start:]:
        if ln.strip().startswith("#"):
            break
        body.append(ln)
    cleaned = [_clean_md(ln) for ln in body if _clean_md(ln)]
    return "\n".join(cleaned[:max_lines])


def summarize(md_text: str, report_type: str) -> str:
    """提取「标题 + 摘要」纯文本（约 1500-2000 字，飞书 text 单条上限内）。"""
    label = REPORT_TYPE_LABEL.get(report_type, "报告")
    lines = md_text.splitlines()
    # 标题：第一行 # 📊 YYYY-MM-DD 复盘/隔夜预案/开盘策略（周X）
    title = ""
    for ln in lines:
        if ln.strip().startswith("#"):
            title = _clean_md(ln)
            break

    head = f"【每日复盘】{label} · {title}"

    if report_type == "review":
        # 推 4 个核心章节：总览（情绪定调）+ 情绪温度 + 连板梯队 + 次日预案（明天怎么做）
        sections = [
            ("一、总览", 20),
            ("二、情绪温度", 10),
            ("三、连板梯队", 12),
            ("七、次日预案", 20),
        ]
        parts: list[str] = []
        for sec_title, max_lines in sections:
            body = _extract_section(md_text, sec_title, max_lines=max_lines)
            if body:
                parts.append(f"【{sec_title}】\n{body}")
        if parts:
            return f"{head}\n\n" + "\n\n".join(parts)

    # plan / open：正文即核心内容（消息面汇总+关注方向 / 竞价总览+个股清单），取 30 行
    content = [ln for ln in lines[1:] if _clean_md(ln)]
    body = "\n".join(_clean_md(ln) for ln in content[:30])
    return f"{head}\n\n{body}" if body else head


# ---------------------------------------------------------------- 生成


def _has_data(indicators: dict) -> bool:
    """判断指标是否有可用数据（涨停数 > 0）。"""
    return int(indicators.get("ladder", {}).get("zt_count", 0) or 0) > 0


def _no_data_reason(date: str) -> str:
    """A3：用权威交易日历区分「非交易日」 vs 「数据未更新」（不再笼统报错）。"""
    from daily_review.data.trade_calendar import is_trade_date

    try:
        verdict = is_trade_date(date)
    except Exception:
        verdict = None
    if verdict is False:
        return "非交易日（交易所休市）"
    if verdict is True:
        return "交易日但数据未更新（可能早于 15:00 或接口异常）"
    return "非交易日或数据未更新"


def generate(report_type: str, date: str) -> str:
    """生成报告并落盘 output/，返回 Markdown 全文。

    数据为空/非交易日 → 抛 NoDataError（由 push_report 转为「跳过」）。
    """
    from daily_review.pipeline import collect, compute

    if report_type not in _REPORT_TYPES:
        raise ValueError(f"report_type 需为 review/plan/open，收到：{report_type}")

    if report_type == "review":
        from daily_review.llm.reporter import generate_report

        collected = collect(date)
        indicators = compute(collected)
        if not _has_data(indicators):
            raise NoDataError(f"{date} 无涨停数据（{_no_data_reason(date)}）")
        return generate_report(indicators, date)

    # plan / open：数据基准是前一交易日
    prev_date = _find_prev_trade_date(date)
    collected = collect(prev_date)
    indicators = compute(collected)

    if report_type == "plan":
        from daily_review.data.eastmoney_news import fetch_overnight_news
        from daily_review.llm.premarket import generate_overnight_plan

        if not _has_data(indicators):
            raise NoDataError(f"{prev_date} 无涨停数据（{_no_data_reason(prev_date)}）")
        try:
            news = fetch_overnight_news(date)
        except Exception:
            news = []
        return generate_overnight_plan(indicators, news, date)

    # open
    from daily_review.analysis.auction import compute_auction, fetch_auction_data
    from daily_review.llm.premarket import generate_open_strategy

    if not _has_data(indicators):
        raise NoDataError(f"{prev_date} 无涨停数据（{_no_data_reason(prev_date)}）")

    # 隔夜预案文案（可能不存在）
    plan_path = get_settings().output_dir / f"{date}_隔夜预案.md"
    plan_text = plan_path.read_text(encoding="utf-8") if plan_path.exists() else ""

    # 竞价数据（昨日涨停股 + 昨日封单映射）
    zt = collected.get("zt")
    auction_data: list[dict] = []
    if zt is not None and not zt.empty:
        codes = [str(c) for c in zt["code"]]
        try:
            quotes = fetch_auction_data(codes)
            prev_seal_map = {}
            if "fund" in zt.columns:
                import pandas as pd

                for _, r in zt.iterrows():
                    fund = r.get("fund")
                    if fund is not None and pd.notna(fund):
                        prev_seal_map[str(r["code"])] = float(fund)
            auction_data = compute_auction(quotes, prev_seal_map=prev_seal_map)
        except Exception:
            auction_data = []
    # 开盘策略依赖竞价数据；无竞价数据（休市/未开盘）→ 跳过
    if not auction_data:
        raise NoDataError(f"{date} 无竞价数据（非交易日或未到竞价时点）")
    return generate_open_strategy(indicators, auction_data, plan_text, date)


# ---------------------------------------------------------------- 幂等状态（v0.23 A2）


def _state_path() -> Path:
    """幂等状态文件 data/push_state.json（gitignored；云端 runner 是全新环境无共享状态）。"""
    return get_settings().data_dir / "push_state.json"


def _load_state() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    """原子写状态（tmp + os.replace，与 repo.save_csv 同款防半写）。"""
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


# ---------------------------------------------------------------- 主入口


def _status_notice(status: str, label: str, date: str, message: str) -> str:
    """非 sent 状态的飞书提示文本（⏭ 跳过 / ❌ 失败）。"""
    icon = "⏭" if status == "skipped" else "❌"
    return f"【每日复盘】{icon} {label} · {date}\n{message}"


def _try_notify(text: str) -> str:
    """发送状态提示；失败静默返回告警串（不回抛，不影响主流程/主状态）。"""
    if not text:
        return ""
    try:
        send_feishu(text)
    except FeishuError as exc:
        return f"（状态提示发送失败：{exc}）"
    return ""


def _with_status_notice(result: dict) -> dict:
    """非 sent 结果：补一条飞书状态提示；提示发送失败仅记录，不改主状态。"""
    if result.get("status") == "sent":
        return result
    text = _status_notice(
        result["status"],
        REPORT_TYPE_LABEL.get(result.get("report_type", ""), "报告"),
        result.get("date", ""),
        result.get("message", ""),
    )
    failed = _try_notify(text)
    if failed:
        result = {**result, "message": result["message"] + failed}
    return result


def push_report(report_type: str, date: str | None = None, force: bool = False) -> dict:
    """生成报告并推送飞书。返回 {status, report_type, date, message, error}。

    status: sent（已推送）| skipped（休市/无数据跳过）| error（失败）
    v0.21.7：非 sent 状态（除周末外）也会向飞书推一条 ⏭/❌ 状态提示，避免「没推=无声」
    ——GitHub Actions 排程派发会迟到，失败/跳过只在 Actions 页面根本看不出。
    v0.22：仅周末完全静默（双休日连 ⏭ 也不发，零打扰）；工作日休市/失败仍提示。
    v0.23：A2 幂等——同源 (type, date) 已推送过且非 force 时直接跳过（不发任何飞书
    消息，免重复打扰）；--force 强制重推并刷新状态；失败/跳过不写状态。
    """
    if report_type not in _REPORT_TYPES:
        raise ValueError(f"report_type 需为 review/plan/open，收到：{report_type}")

    # 周末直接跳过（周六日 A 股休市）：v0.22 起完全静默，不打扰
    if beijing_now().weekday() >= 5:
        return {
            "status": "skipped",
            "report_type": report_type,
            "date": date or beijing_today(),
            "message": "周末休市，跳过推送",
            "error": "",
        }

    date = date or beijing_today()
    key = f"{report_type}_{date}"

    # 幂等：已推送过 → 跳过，不发重复消息
    if not force and key in _load_state():
        return {
            "status": "skipped",
            "report_type": report_type,
            "date": date,
            "message": f"该日已推送过（{REPORT_TYPE_LABEL[report_type]} {date}），--force 可强制重推",
            "error": "",
        }

    try:
        md = generate(report_type, date)
    except NoDataError as exc:
        result = {
            "status": "skipped",
            "report_type": report_type,
            "date": date,
            "message": str(exc),
            "error": "",
        }
    except Exception as exc:  # noqa: BLE001 —— 采集/网络失败也属「跳过」而非崩溃
        result = {
            "status": "error",
            "report_type": report_type,
            "date": date,
            "message": f"报告生成失败：{type(exc).__name__}: {exc}",
            "error": str(exc),
        }
    else:
        text = summarize(md, report_type)
        try:
            send_feishu(text)
        except FeishuError as exc:
            result = {
                "status": "error",
                "report_type": report_type,
                "date": date,
                "message": f"推送失败：{exc}",
                "error": str(exc),
            }
        else:
            # 推送成功才记幂等状态；失败/跳过不写，下次重试仍会尝试
            _save_state(
                {
                    **_load_state(),
                    key: {
                        "time": datetime.now().astimezone().isoformat(timespec="seconds"),
                        "chars": len(text),
                    },
                }
            )
            return {
                "status": "sent",
                "report_type": report_type,
                "date": date,
                "message": f"已推送{REPORT_TYPE_LABEL[report_type]}（{len(text)} 字）",
                "error": "",
            }
    return _with_status_notice(result)