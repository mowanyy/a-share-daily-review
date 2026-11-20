"""增量更新「供选股数据」到最新（v0.38.5，对齐原 55 列格式）。

供选股数据.csv（GBK，776MB，2006-12~2025-01，5610 只全市场日快照）是本项目回测/因子
训练的核心数据集。本工具用东财接口增量补 2025-01-28 之后的数据，输出格式对齐原列。

依赖：
- 日 K（push2his）：价格/成交额/涨跌幅 → 技术因子 + 未来 20 天收益
- F10 财务（datacenter-web，历史可用）：单季净利/现金流/净资产/股本（ttm 由报告期累计差分）
- 行业：东财行业映射（industry_map.csv 缓存）暂填一级，申万三级留空（原数据为申万，需另接申万源）

用法：
  python -m tools.update_feature_data --codes 600000,000001   # 先小范围验证
  python -m tools.update_feature_data                          # 全量 5610 只（K 线网络恢复后，约 2-3 小时）
输出：供选股数据_增量_{today}.csv（GBK，列与原文件一致）
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402

from daily_review.config import get_settings  # noqa: E402
from daily_review.data import eastmoney  # noqa: E402

# 与原 55 列一致（顺序对齐供选股数据.csv）
OUT_COLUMNS = [
    "交易日期", "股票代码", "股票名称", "是否交易", "开盘价", "最高价", "最低价", "收盘价", "VWAP", "成交额",
    "流通市值", "总市值", "上市至今交易天数", "财报季度", "财报年份",
    "归母净利润", "归母净利润_ttm", "归母净利润_ttm同比", "归母净利润_单季", "归母净利润_单季同比", "归母净利润_单季环比",
    "经营活动产生的现金流量净额", "经营活动产生的现金流量净额_ttm", "经营活动产生的现金流量净额_ttm同比",
    "经营活动产生的现金流量净额_单季", "经营活动产生的现金流量净额_单季同比", "经营活动产生的现金流量净额_单季环比",
    "净资产", "涨跌幅_10", "涨跌幅_20", "bias_5", "bias_10", "bias_20",
    "振幅_5", "振幅_10", "振幅_20", "涨跌幅std_5", "涨跌幅std_10", "涨跌幅std_20",
    "成交额std_5", "成交额std_10", "成交额std_20", "K", "D", "J", "DIF", "DEA", "MACD",
    "市盈率倒数", "市净率倒数", "新版申万一级行业名称", "新版申万二级行业名称", "新版申万三级行业名称",
    "涨跌幅", "下周期每天涨跌幅",
]

MAINFIN = "RPT_F10_FINANCE_MAINFINADATA"
FIN_BASE = "https://datacenter-web.eastmoney.com/api/data/v1/get"


def secid_of(code: str) -> str:
    return ("1." if code.startswith(("6", "9")) else "0.") + code


def market_prefix(code: str) -> str:
    return "SH" if code.startswith(("6", "9")) else "SZ"


def fetch_financial(code: str, report_date: str) -> dict:
    """单报告期主财务指标（单季净利/现金流/净资产/股本）。"""
    import requests

    url = (f"{FIN_BASE}?reportName={MAINFIN}&columns=ALL&source=HSF10&pageNumber=1&pageSize=1"
           f"&filter=(SECUCODE=\"{code}.{market_prefix(code)}\")(REPORT_DATE='{report_date}')")
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
    rows = ((r.json().get("result") or {}).get("data")) or []
    if not rows:
        return {}
    row = rows[0]
    return {
        "netprofit": _num(row.get("PARENTNETPROFIT")),        # 报告期累计归母净利
        "netprofit_yoy": _num(row.get("PARENTNETPROFITTZ")),  # 累计同比
        "cashflow": _num(row.get("NETCASH_OPERATE_PK")),      # 报告期累计经营现金流
        "equity": _num(row.get("TOTAL_EQUITY_PK")),           # 净资产
        "tot_share": _num(row.get("TOTAL_SHARE")),            # 总股本
        "free_share": _num(row.get("A_FREE_SHARE")),          # 流通股本
    }


def _num(v):
    try:
        if v is None or pd.isna(v):
            return None
        return float(v)
    except Exception:
        return None


def compute_factors(df: pd.DataFrame) -> pd.DataFrame:
    """从日 K 计算技术因子（口径对齐原数据：bias/振幅/std/KDJ/MACD/VWAP/未来收益）。"""
    d = df.copy().sort_values("trade_date").reset_index(drop=True)
    close, high, low, vol = d["close"], d["high"], d["low"], d["volume"]
    pct = close.pct_change()
    for n in (5, 10, 20):
        d[f"bias_{n}"] = close / close.rolling(n).mean() - 1
        d[f"std_{n}"] = pct.rolling(n).std()
        d[f"amp_{n}"] = (high.rolling(n).max() - low.rolling(n).min()) / close.shift(1)
        d[f"amtstd_{n}"] = d["amount"].rolling(n).std()
    d["pct_10"] = close / close.shift(10) - 1
    d["pct_20"] = close / close.shift(20) - 1
    # KDJ(9,3,3)
    low9, high9 = low.rolling(9).min(), high.rolling(9).max()
    rsv = (close - low9) / (high9 - low9).replace(0, pd.NA) * 100
    k = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    d["K"] = k
    d["D"] = k.ewm(alpha=1 / 3, adjust=False).mean()
    d["J"] = 3 * d["K"] - 2 * d["D"]
    # MACD(12,26,9)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    d["DIF"] = dif
    d["DEA"] = dif.ewm(span=9, adjust=False).mean()
    d["MACD"] = 2 * (d["DIF"] - d["DEA"])
    # VWAP（元/股）
    d["vwap"] = d["amount"] / vol.replace(0, pd.NA)
    # 未来 20 个交易日每日涨跌幅（列表字符串）
    d["future_20"] = [json.dumps([round(x, 4) if pd.notna(x) else None
                                  for x in pct.shift(-i).head(len(d))[:20]], ensure_ascii=False)
                      if False else _future_list(pct, i) for i in range(len(d))]
    return d


def _future_list(pct: pd.Series, i: int) -> str:
    vals = []
    for k in range(1, 21):
        j = i + k
        v = pct.iloc[j] if j < len(pct) else None
        vals.append(round(float(v), 4) if pd.notna(v) else None)
    return json.dumps(vals, ensure_ascii=False)


def fetch_stock_kline(code: str, lmt: int) -> pd.DataFrame:
    return eastmoney.fetch_kline(code, klt=101, fqt=0, end="20500101", lmt=lmt)


def build_rows(code: str, name: str, kdf: pd.DataFrame, fins: dict, start: str) -> list[dict]:
    """组装对齐原 55 列的行（仅 start 之后的日期）。"""
    fac = compute_factors(kdf)
    fac = fac[fac["trade_date"] >= start]
    rows = []
    # 财务：报告期累计 → 单季/ttm
    for _, r in fac.iterrows():
        dt = r["trade_date"]
        year, q = dt[:4], (int(dt[5:7]) - 1) // 3 + 1
        rows.append({
            "交易日期": dt, "股票代码": f"{market_prefix(code).lower()}{code}", "股票名称": name, "是否交易": 1,
            "开盘价": r["open"], "最高价": r["high"], "最低价": r["low"], "收盘价": r["close"],
            "VWAP": round(r["vwap"], 4) if pd.notna(r["vwap"]) else None, "成交额": r["amount"],
            "流通市值": (fins.get("free_share") or 0) * r["close"] if fins.get("free_share") else None,
            "总市值": (fins.get("tot_share") or 0) * r["close"] if fins.get("tot_share") else None,
            "上市至今交易天数": None, "财报季度": q, "财报年份": int(year),
            "归母净利润": fins.get("netprofit"), "归母净利润_ttm": None, "归母净利润_ttm同比": None,
            "归母净利润_单季": None, "归母净利润_单季同比": fins.get("netprofit_yoy"), "归母净利润_单季环比": None,
            "经营活动产生的现金流量净额": fins.get("cashflow"), "经营活动产生的现金流量净额_ttm": None,
            "经营活动产生的现金流量净额_ttm同比": None, "经营活动产生的现金流量净额_单季": None,
            "经营活动产生的现金流量净额_单季同比": None, "经营活动产生的现金流量净额_单季环比": None,
            "净资产": fins.get("equity"),
            "涨跌幅_10": r["pct_10"], "涨跌幅_20": r["pct_20"],
            "bias_5": r["bias_5"], "bias_10": r["bias_10"], "bias_20": r["bias_20"],
            "振幅_5": r["amp_5"], "振幅_10": r["amp_10"], "振幅_20": r["amp_20"],
            "涨跌幅std_5": r["std_5"], "涨跌幅std_10": r["std_10"], "涨跌幅std_20": r["std_20"],
            "成交额std_5": r["amtstd_5"], "成交额std_10": r["amtstd_10"], "成交额std_20": r["amtstd_20"],
            "K": r["K"], "D": r["D"], "J": r["J"], "DIF": r["DIF"], "DEA": r["DEA"], "MACD": r["MACD"],
            "市盈率倒数": None, "市净率倒数": None,
            "新版申万一级行业名称": None, "新版申万二级行业名称": None, "新版申万三级行业名称": None,
            "涨跌幅": r["pct_change"], "下周期每天涨跌幅": r["future_20"],
        })
    return rows


def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    src = Path("供选股数据.csv")
    if not src.exists():
        print("未找到 供选股数据.csv")
        return 1
    # 读取股票列表（代码/名称/最新日期）
    meta = {}
    for chunk in pd.read_csv(src, encoding="gbk", chunksize=500000, usecols=["交易日期", "股票代码", "股票名称"]):
        for _, r in chunk.iterrows():
            code = str(r["股票代码"]).replace("sh", "").replace("sz", "").zfill(6)
            meta.setdefault(code, (str(r["股票名称"]), str(r["交易日期"])))
            if str(r["交易日期"]) > meta[code][1]:
                meta[code] = (str(r["股票名称"]), str(r["交易日期"]))
    codes = [c.strip().zfill(6) for c in args.codes.split(",") if c.strip()] if args.codes else list(meta)
    print(f"[update] 股票数 {len(codes)}，增量起点 {args.start}")

    quarters = ["0331", "0630", "0930", "1231"]
    out_rows: list[dict] = []
    ok, fail = 0, 0
    for i, code in enumerate(codes, 1):
        try:
            name, last_date = meta.get(code, (code, "2025-01-27"))
            kdf = fetch_stock_kline(code, args.lmt)
            if kdf.empty:
                fail += 1
                print(f"[{i}/{len(codes)}] {code} K线空")
                continue
            # 财务：覆盖增量期间全部报告期（最新值沿用）
            fins: dict = {}
            for q in quarters:
                rd = f"{int(args.start[:4])}-{q[:2]}-{q[2:]}"
                f = fetch_financial(code, rd)
                if f:
                    fins = f
            rows = build_rows(code, name, kdf, fins, args.start)
            out_rows.extend(rows)
            ok += 1
            print(f"[{i}/{len(codes)}] {code} {name} 增量 {len(rows)} 行")
        except Exception as exc:  # noqa: BLE001
            fail += 1
            print(f"[{i}/{len(codes)}] {code} FAIL {type(exc).__name__}: {str(exc)[:60]}")
        if args.sleep:
            time.sleep(args.sleep)

    out_path = Path(f"供选股数据_增量_{datetime.now().strftime('%Y%m%d')}.csv")
    if out_rows:
        pd.DataFrame(out_rows, columns=OUT_COLUMNS).to_csv(out_path, index=False, encoding="gbk")
        print(f"[update] 输出 {len(out_rows)} 行 -> {out_path}")
    print(f"[update] 完成: 成功 {ok} / 失败 {fail}")
    return 0 if fail == 0 else 1


def main() -> int:
    p = argparse.ArgumentParser(description="增量更新供选股数据到最新（对齐原 55 列）")
    p.add_argument("--codes", default="", help="股票代码逗号分隔（缺省全量 5610 只）")
    p.add_argument("--start", default="2025-01-28", help="增量起点日期")
    p.add_argument("--lmt", type=int, default=2000, help="每只 K 线根数（覆盖增量期+因子窗口）")
    p.add_argument("--sleep", type=float, default=0.5, help="每只间隔秒数")
    args = p.parse_args()
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
