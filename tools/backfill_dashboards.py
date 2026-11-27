"""批量补采历史行情数据 + 预生成历史看板（v0.38.1 数据完善）。

让看板任意历史日期秒开：缺 zt/zb 的日期走 pipeline.collect 补齐（顺带 moneyflow/lhb），
已有 zt/zb 的日期仅生成新版看板（读盘为主）；可单独补采 moneyflow/lhb。

用法：
  python -m tools.backfill_dashboards --days 60          # 最近 60 个交易日：补数据 + 生成看板
  python -m tools.backfill_dashboards --days 60 --skip-data   # 只批量生成看板（数据已齐）
  python -m tools.backfill_dashboards --days 60 --skip-dashboards  # 只补数据
  python -m tools.backfill_dashboards --dates 20260910,20260911
  python -m tools.backfill_dashboards --clean-junk       # 清理周末污染数据目录（谨慎）
节奏：每日期间 sleep（默认 1.5s）防东财限流；失败不中断，汇总打印。
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from daily_review.config import get_settings  # noqa: E402
from daily_review.data import eastmoney_lhb, eastmoney_pool as em  # noqa: E402
from daily_review.data.repo import load_csv, save_csv  # noqa: E402


def _has_zt(date: str) -> bool:
    """zt_pool 是否**有效**：非空且含关键列 lb_num（防旧版/坏文件被误判已有）。"""
    try:
        df = load_csv("zt_pool", date)
        return (not df.empty) and "lb_num" in df.columns
    except Exception:
        return False


def _fill_extra(date: str, *, fill_mf: bool, fill_lhb: bool) -> dict:
    """轻量补 moneyflow_zb / lhb_daily / lhb_seats（zt/zb 已有时，不重拉池子）。"""
    stats = {"mf": "skip", "lhb": "skip"}
    zb = None
    try:
        zb = load_csv("zb_pool", date)
    except Exception:
        zb = None
    if fill_mf and zb is not None and not zb.empty:
        mf_exists = False
        try:
            mf_exists = not load_csv("moneyflow_zb", date).empty
        except Exception:
            pass
        if not mf_exists:
            try:
                name_map = dict(zip(zb["code"].astype(str), zb["name"]))
                codes = [str(c) for c in zb["code"]]
                mf = em.fetch_moneyflow(codes, date, name_map)
                if not mf.empty:
                    save_csv(mf, "moneyflow_zb", date)
                    stats["mf"] = f"ok({len(mf)})"
                else:
                    stats["mf"] = "empty"
            except Exception as exc:
                stats["mf"] = f"err:{type(exc).__name__}"
    if fill_lhb:
        lhb_exists = False
        try:
            lhb_exists = not load_csv("lhb_daily", date).empty
        except Exception:
            pass
        if not lhb_exists:
            try:
                daily = eastmoney_lhb.fetch_lhb_daily(date)
                if not daily.empty:
                    save_csv(daily, "lhb_daily", date)
                seats = eastmoney_lhb.fetch_lhb_seats(date)
                if not seats.empty:
                    save_csv(seats, "lhb_seats", date)
                stats["lhb"] = f"ok(d{len(daily)}/s{len(seats)})"
            except Exception as exc:
                stats["lhb"] = f"err:{type(exc).__name__}"
    return stats


def resolve_dates(today: str, days: int) -> list[str]:
    """最近 days 个交易日（由近及远，今日在列则置首）。"""
    dates = em.resolve_recent_trade_dates(today, n_days=days)
    if today not in dates:
        dates = [today] + dates
    return dates[:days]


def clean_weekend_junk() -> list[str]:
    """清理日历确认**休市**的污染数据目录（v0.31 曾把周末写成交易日）。

    仅删 `is_trade_date == False`（表覆盖范围内明确非交易日）；表外未来日期
    （None，如当日日历未更新）一律不删，防误删真实数据。
    """
    from daily_review.data.trade_calendar import is_trade_date

    removed: list[str] = []
    data_dir = get_settings().data_dir
    for child in sorted(data_dir.iterdir()):
        if not (child.is_dir() and len(child.name) == 8 and child.name.isdigit()):
            continue
        if is_trade_date(child.name) is False:  # 仅明确休市才删
            shutil.rmtree(child, ignore_errors=True)
            removed.append(child.name)
    return removed


def run(args: argparse.Namespace) -> int:
    today = datetime.today().strftime("%Y%m%d")
    if args.dates:
        dates = [d.strip() for d in args.dates.split(",") if d.strip()]
    else:
        dates = resolve_dates(today, args.days)
    print(f"[backfill] 日期范围 {len(dates)} 个交易日: {dates[0]} .. {dates[-1]}")

    ok, fail = 0, 0
    for i, date in enumerate(dates, 1):
        stats: list[str] = []
        try:
            if not args.skip_data:
                if _has_zt(date):
                    stats.append("zt已有")
                    extra = _fill_extra(date, fill_mf=args.fill_extra, fill_lhb=args.fill_extra)
                    stats += [f"{k}={v}" for k, v in extra.items()]
                else:
                    from daily_review.pipeline import collect

                    c = collect(date)
                    zt_n = len(c["zt"])
                    stats.append(f"collect zt={zt_n}")
            if not args.skip_dashboards:
                from daily_review.dashboard import generate_dashboard

                html = generate_dashboard(date)
                stats.append(f"看板{len(html)}B")
            print(f"[{i}/{len(dates)}] {date} | " + " | ".join(stats))
            ok += 1
        except Exception as exc:  # noqa: BLE001 —— 单日失败不中断批量
            print(f"[{i}/{len(dates)}] {date} | FAIL {type(exc).__name__}: {exc}")
            fail += 1
        if i < len(dates) and args.sleep:
            time.sleep(args.sleep)
    print(f"[backfill] 完成: 成功 {ok} / 失败 {fail}")
    return 0 if fail == 0 else 1


def main() -> int:
    p = argparse.ArgumentParser(description="批量补采历史数据 + 预生成历史看板")
    p.add_argument("--days", type=int, default=60, help="最近 N 个交易日（默认 60）")
    p.add_argument("--dates", default="", help="指定日期逗号分隔（优先于 --days）")
    p.add_argument("--skip-data", action="store_true", help="不补数据，只生成看板")
    p.add_argument("--skip-dashboards", action="store_true", help="不生成看板，只补数据")
    p.add_argument("--fill-extra", action="store_true", help="zt/zb 已有时补 moneyflow/lhb")
    p.add_argument("--sleep", type=float, default=1.5, help="每日期间隔秒数（防限流）")
    p.add_argument("--clean-junk", action="store_true", help="清理周末污染数据目录后退出")
    args = p.parse_args()
    if args.clean_junk:
        removed = clean_weekend_junk()
        print(f"[backfill] 清理非交易日污染目录 {len(removed)} 个: {removed}")
        return 0
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
