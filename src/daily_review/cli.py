"""命令行入口：python -m daily_review kline / realtime / review。

示例：
  python -m daily_review kline --code 600000 --lmt 30
  python -m daily_review realtime --codes 600601,002398,600789
  python -m daily_review review --date 20260806 --no-llm
  python -m daily_review review            # 缺省探测最近交易日，需 .env 配置 DEEPSEEK_API_KEY
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from daily_review.config import get_settings
from daily_review.data import eastmoney, eastmoney_pool, repo, sina


def _print(df) -> None:
    print(df.to_string(index=False))


def _cmd_kline(args) -> None:
    df = eastmoney.fetch_kline(args.code, klt=args.klt, fqt=args.fqt, lmt=args.lmt)
    _print(df)
    path = repo.save_csv(df, f"kline_{args.code}")
    print(f"\n已保存: {path}")


def _cmd_realtime(args) -> None:
    codes = [c for c in args.codes.split(",") if c.strip()]
    df = sina.fetch_realtime(codes)
    _print(df)
    name = f"realtime_{datetime.today().strftime('%Y%m%d')}"
    path = repo.save_csv(df, name)
    print(f"\n已保存: {path}")


def _print_summary(ind: dict) -> None:
    ladder = ind["ladder"]
    print("\n=== 指标 ===")
    print(
        f"涨停 {ladder['zt_count']} / 连板 {ladder['lianban_count']} / "
        f"空间板 {ladder['max_lb']} 板({ladder['max_lb_stock']})"
    )
    print(f"炸板 {ladder['break_count']} / 炸板率 {ladder['break_rate'] * 100:.1f}%")
    prom = ladder.get("promotion") or {}
    if prom:
        print("晋级率: " + "  ".join(f"{k}={v * 100:.1f}%" for k, v in prom.items()))
    print(f"题材 {len(ind['themes'])} 个")
    for t in ind["themes"][:5]:
        leader = (t.get("leader") or {}).get("name", "")
        print(
            f"  - {t['theme_name']} 家数{t['member_count']} 高度{t['max_lb']} "
            f"阶段{t['stage']} 龙头 {leader}"
        )
    print(f"炸板资金流表 {len(ind['break'].get('table', []))} 行")


def _cmd_review(args) -> None:
    from daily_review.llm.client import LLMError
    from daily_review.llm.reporter import generate_report
    from daily_review.pipeline import collect, compute

    trade_date = args.date or ""
    if not trade_date:
        dates = eastmoney_pool.resolve_recent_trade_dates(
            datetime.today().strftime("%Y%m%d"), n_days=1
        )
        trade_date = dates[0] if dates else datetime.today().strftime("%Y%m%d")
        print(f"[review] 缺省交易日: {trade_date}")

    collected = collect(trade_date)
    indicators = compute(collected)
    _print_summary(indicators)

    if args.no_llm:
        print("\n已跳过 LLM 报告（--no-llm）；数据与指标已就绪")
        return

    print("\n[LLM] 生成复盘报告（DeepSeek）...")
    md = generate_report(indicators, trade_date)
    out = get_settings().output_dir / f"{trade_date}_复盘.md"
    print(f"\n已生成: {out}")
    print(f"（{len(md.splitlines())} 行）")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="daily-review", description="每日复盘 · 数据采集 + 端到端复盘 CLI（v0.3）"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_kline = sub.add_parser("kline", help="东方财富日 K 线")
    p_kline.add_argument("--code", required=True, help="股票代码，如 600000")
    p_kline.add_argument("--lmt", type=int, default=120, help="返回条数（默认 120）")
    p_kline.add_argument("--klt", type=int, default=101, help="周期 101=日线 102=周线")
    p_kline.add_argument("--fqt", type=int, default=0, help="复权 0=不复权 1=前复权 2=后复权")
    p_kline.set_defaults(func=_cmd_kline)

    p_rt = sub.add_parser("realtime", help="新浪实时行情")
    p_rt.add_argument(
        "--codes", required=True, help="逗号分隔代码，如 600601,002398,600789"
    )
    p_rt.set_defaults(func=_cmd_realtime)

    p_rev = sub.add_parser(
        "review",
        help="端到端复盘：采集→指标→LLM 报告（收盘 15:00 后数据才完整）",
    )
    p_rev.add_argument("--date", default="", help="交易日 YYYYMMDD，缺省探测最近交易日")
    p_rev.add_argument(
        "--no-llm", action="store_true", help="只采集+算指标，跳过 LLM 报告"
    )
    p_rev.set_defaults(func=_cmd_review)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
