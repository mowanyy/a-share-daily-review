"""命令行入口：python -m daily_review kline / realtime。

示例：
  python -m daily_review kline --code 600000 --lmt 30
  python -m daily_review realtime --codes 600601,002398,600789
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from daily_review.data import eastmoney, repo, sina


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="daily-review", description="每日复盘 · 数据采集 CLI（v0.2）"
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

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
