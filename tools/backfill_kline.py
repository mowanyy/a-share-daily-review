"""批量拉取涨停股历史日 K（回测数据，v0.38.4）。

涨停池聚合涨停股集合（data/{date}/zt_pool.csv 全部日期）→ 每只拉日 K（push2his 接口任意历史）
→ 落盘 data/kline/{code}.csv（按股票存，回测友好；data/*/ 已 gitignore 不入库）。

用法：
  python -m tools.backfill_kline                 # 全量：聚合涨停股 → 拉日K（已存在跳过）
  python -m tools.backfill_kline --lmt 1000      # 每只返回最近 1000 根日K（约 4 年）
  python -m tools.backfill_kline --codes 000001,600519   # 指定代码
  python -m tools.backfill_kline --force         # 覆盖已有
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from daily_review.config import get_settings  # noqa: E402
from daily_review.data import eastmoney  # noqa: E402


def collect_zt_codes(data_dir: Path) -> list[str]:
    """聚合所有日期 zt_pool 的涨停股代码（去重，按首现日期序）。"""
    import pandas as pd

    codes: list[str] = []
    seen: set[str] = set()
    for d in sorted(p for p in data_dir.iterdir() if p.is_dir() and len(p.name) == 8 and p.name.isdigit()):
        csv = d / "zt_pool.csv"
        if not csv.exists():
            continue
        try:
            df = pd.read_csv(csv, encoding="utf-8-sig")
        except Exception:
            continue
        if df.empty or "code" not in df.columns:
            continue
        for c in df["code"].astype(str):
            c = c.zfill(6)
            if c not in seen:
                seen.add(c)
                codes.append(c)
    return codes


def fetch_one(code: str, klt: int, fqt: int, lmt: int) -> object:
    from daily_review.data.eastmoney import fetch_kline

    return fetch_kline(code, klt=klt, fqt=fqt, lmt=lmt)


def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    kline_dir = settings.data_dir / "kline"
    kline_dir.mkdir(parents=True, exist_ok=True)

    if args.codes:
        codes = [c.strip().zfill(6) for c in args.codes.split(",") if c.strip()]
    else:
        codes = collect_zt_codes(settings.data_dir)
    print(f"[kline] 股票数 {len(codes)}，lmt={args.lmt}，fqt={args.fqt}")

    ok, skip, fail = 0, 0, 0
    for i, code in enumerate(codes, 1):
        out = kline_dir / f"{code}.csv"
        if out.exists() and not args.force:
            skip += 1
            if i % 50 == 0 or i == len(codes):
                print(f"[{i}/{len(codes)}] {code} skip（已有）…累计 ok={ok} skip={skip} fail={fail}")
            continue
        try:
            df = fetch_one(code, args.klt, args.fqt, args.lmt)
            if df is not None and not df.empty:
                df.to_csv(out, index=False, encoding="utf-8-sig")
                ok += 1
                print(f"[{i}/{len(codes)}] {code} ok {len(df)} 根")
            else:
                fail += 1
                print(f"[{i}/{len(codes)}] {code} 空数据")
        except Exception as exc:  # noqa: BLE001 —— 单只失败重试一次
            try:
                time.sleep(args.sleep * 3)
                df = fetch_one(code, args.klt, args.fqt, args.lmt)
                if df is not None and not df.empty:
                    df.to_csv(out, index=False, encoding="utf-8-sig")
                    ok += 1
                    print(f"[{i}/{len(codes)}] {code} ok(重试) {len(df)} 根")
                    if args.sleep:
                        time.sleep(args.sleep)
                    continue
                fail += 1
            except Exception:  # noqa: BLE001
                fail += 1
            print(f"[{i}/{len(codes)}] {code} FAIL {type(exc).__name__}")
        if args.sleep:
            time.sleep(args.sleep)
    print(f"[kline] 完成: 成功 {ok} / 跳过 {skip} / 失败 {fail}")
    return 0 if fail == 0 else 1


def main() -> int:
    p = argparse.ArgumentParser(description="批量拉取涨停股历史日K（回测数据）")
    p.add_argument("--lmt", type=int, default=1000, help="每只返回 K 线根数（默认 1000，约 4 年）")
    p.add_argument("--klt", type=int, default=101, help="周期 101=日线 102=周线 103=月线")
    p.add_argument("--fqt", type=int, default=0, help="复权 0=不复权 1=前复权 2=后复权")
    p.add_argument("--sleep", type=float, default=0.4, help="每只间隔秒数（防限流）")
    p.add_argument("--codes", default="", help="指定代码逗号分隔（缺省聚合全部涨停股）")
    p.add_argument("--force", action="store_true", help="覆盖已有文件")
    args = p.parse_args()
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
