"""东方财富涨跌停池 / 资金流 / 概念板块接口（v0.3）。

已实测（2026-08-07）确认：
- 池子端点 `push2ex.eastmoney.com/getTopic{ZT,ZB,DT}Pool`，需参数
  `ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&Pageindex=0&pagesize=N&sort=fbt:asc&date=YYYYMMDD`
  —— 支持历史日期，返回 `data.pool[]`。
- 单股历史资金流 `push2his.eastmoney.com/api/qt/stock/fflow/kline/get`，行格式：
  `日期,主力净流入,小单,中单,大单,超大单`（主力 = 大单 + 超大单，已核对）。
- 板块/全市场列表 `push2.eastmoney.com/api/qt/clist/get`，host 偶发断连，做主机轮换。

字段映射（涨停池 pool 项）：
  c=代码 n=名称 lbc=连板数 fbt=首封时间(HHMMSS) lbt=末封 zbc=炸板次数
  fund=封单资金(元) hs=换手率% amount=成交额 hybk=行业(可能截断) zdp=涨跌幅%
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from urllib.parse import urlencode

import pandas as pd

from daily_review.config import get_settings
from daily_review.data.http_client import get, get_json


def _throttle() -> None:
    """请求最小间隔（防封控，见 docs/东财接口清单.md：≥1s/请求）。"""
    time.sleep(get_settings().request_interval)

# 东财接口专用头（覆盖 http_client 默认的 sina Referer）
EM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
}

POOL_BASE = "https://push2ex.eastmoney.com"
POOL_UT = "7eea3edcaed734bea9cbfc24409ed989"
POOL_DPT = "wz.ztzt"

# push2 / push2his 主机轮换（东财主机偶发断连/限流，delay 主机作为兜底）
CLIST_HOSTS = [
    "https://push2.eastmoney.com",
    "https://1.push2.eastmoney.com",
    "https://2.push2.eastmoney.com",
    "https://push2delay.eastmoney.com",
]
FFLOW_HOSTS = [
    "https://push2his.eastmoney.com",
    "https://1.push2his.eastmoney.com",
    "https://2.push2his.eastmoney.com",
    "https://push2delay.eastmoney.com",
    "https://1.push2delay.eastmoney.com",
]


def _get_json_rotated(path_template: str, hosts: list[str], params: dict) -> dict:
    """按主机列表轮换发 GET+JSON：单 host 失败即换下一个（配合 http_client 内部重试）。"""
    from urllib.parse import urlencode
    _throttle()
    last_err: Exception | None = None
    for host in hosts:
        try:
            url = f"{host}/{path_template}?{urlencode(params)}"
            return get_json(url, headers=EM_HEADERS)
        except Exception as exc:  # noqa: BLE001
            last_err = exc
    if last_err is not None:
        raise last_err
    raise RuntimeError(f"请求失败（所有 host 均不可用）: {path_template}")

# 涨跌停池字段 → DataFrame 列
ZT_COLUMNS = [
    "trade_date", "code", "name", "lb_num", "first_limit_time",
    "last_limit_time", "open_times", "seal_amount", "turnover", "amount", "industry",
]
ZB_COLUMNS = [
    "trade_date", "code", "name", "break_times", "first_seal_time",
    "up_pct", "industry",
]
DT_COLUMNS = ["trade_date", "code", "name", "up_pct"]
FFLOW_COLUMNS = [
    "trade_date", "main_net_inflow", "small_net_inflow",
    "mid_net_inflow", "big_net_inflow", "super_net_inflow",
]


# ---------------------------------------------------------------- 基础工具

def _num(v) -> float | None:
    """安全转 float；空/None/异常返回 None。"""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _time_of(v) -> str:
    """HHMMSS 整数（如 92500 → 09:25）→ '%H:%M' 字符串；空则返回空串。"""
    if v is None or v == "":
        return ""
    try:
        s = str(int(float(v))).zfill(6)
        return f"{s[0:2]}:{s[2:4]}"
    except (TypeError, ValueError):
        return ""


def _prev_date(ymd: str) -> str:
    """YYYYMMDD → 前一天的 YYYYMMDD。"""
    d = datetime.strptime(ymd, "%Y%m%d") - timedelta(days=1)
    return d.strftime("%Y%m%d")


def _pool_url(endpoint: str, trade_date: str, pagesize: int = 300) -> str:
    params = {
        "ut": POOL_UT,
        "dpt": POOL_DPT,
        "Pageindex": 0,
        "pagesize": pagesize,
        "sort": "fbt:asc",
        "date": trade_date,
    }
    return f"{POOL_BASE}/{endpoint}?{urlencode(params)}"


def _pool_json(endpoint: str, trade_date: str, pagesize: int = 300) -> list[dict]:
    _throttle()
    url = _pool_url(endpoint, trade_date, pagesize=pagesize)
    payload = get_json(url, headers=EM_HEADERS)
    data = payload.get("data") or {}
    return data.get("pool") or []


def _clist_json(params: dict) -> dict:
    """clist 请求：按 CLIST_HOSTS 轮换。"""
    base_params = {
        "po": 1, "pn": 1, "np": 1, "fltt": 2, "invt": 2,
    }
    base_params.update(params)
    payload = _get_json_rotated("api/qt/clist/get", CLIST_HOSTS, base_params)
    if payload.get("rc") == 0 and payload.get("data"):
        return payload
    raise ValueError(f"clist rc={payload.get('rc')} data 为空")


# ---------------------------------------------------------------- 涨跌停池

def fetch_zt_pool(trade_date: str) -> pd.DataFrame:
    """当日涨停池。列对齐 LimitUpStock（缺 concepts，由管道补）。"""
    rows = []
    for item in _pool_json("getTopicZTPool", trade_date):
        rows.append({
            "trade_date": trade_date,
            "code": str(item.get("c", "")),
            "name": str(item.get("n", "")),
            "lb_num": int(_num(item.get("lbc")) or 0),
            "first_limit_time": _time_of(item.get("fbt")),
            "last_limit_time": _time_of(item.get("lbt")),
            "open_times": int(_num(item.get("zbc")) or 0),
            "seal_amount": _num(item.get("fund")),
            "turnover": _num(item.get("hs")),
            "amount": _num(item.get("amount")),
            "industry": str(item.get("hybk") or ""),
        })
    df = pd.DataFrame(rows, columns=ZT_COLUMNS)
    df["seal_amount"] = pd.to_numeric(df["seal_amount"], errors="coerce")
    df["turnover"] = pd.to_numeric(df["turnover"], errors="coerce")
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
    return df


def fetch_zb_pool(trade_date: str) -> pd.DataFrame:
    """当日炸板池。列对齐 BreakStock（last_break_time 接口未返回，置空）。"""
    rows = []
    for item in _pool_json("getTopicZBPool", trade_date):
        rows.append({
            "trade_date": trade_date,
            "code": str(item.get("c", "")),
            "name": str(item.get("n", "")),
            "break_times": int(_num(item.get("zbc")) or 0),
            "first_seal_time": _time_of(item.get("fbt")),
            "up_pct": _num(item.get("zdp")),
            "industry": str(item.get("hybk") or ""),
        })
    df = pd.DataFrame(rows, columns=ZB_COLUMNS)
    df["up_pct"] = pd.to_numeric(df["up_pct"], errors="coerce")
    return df


def fetch_dt_pool(trade_date: str) -> pd.DataFrame:
    """当日跌停池（情绪参考；可能为空）。"""
    rows = []
    for item in _pool_json("getTopicDTPool", trade_date):
        rows.append({
            "trade_date": trade_date,
            "code": str(item.get("c", "")),
            "name": str(item.get("n", "")),
            "up_pct": _num(item.get("zdp")),
        })
    df = pd.DataFrame(rows, columns=DT_COLUMNS)
    df["up_pct"] = pd.to_numeric(df["up_pct"], errors="coerce")
    return df


def resolve_recent_trade_dates(start: str, n_days: int = 5) -> list[str]:
    """探测式解析最近交易日：从 start 起逐日拉涨停池，空则回退，取 n 个非空日。

    返回**由近及远**（最新在前）的交易日列表。免维护交易日历。
    """
    dates: list[str] = []
    cur = start
    for _ in range(max(n_days * 4, 12)):  # 防死循环兜底
        if len(dates) >= n_days:
            break
        try:
            pool = _pool_json("getTopicZTPool", cur, pagesize=5)
        except Exception:
            pool = []
        if pool:
            dates.append(cur)
        cur = _prev_date(cur)
    return dates


# ---------------------------------------------------------------- 资金流

def fetch_fflow_kline(code: str) -> pd.DataFrame:
    """单股历史资金流日线。列：date + 主力/小单/中单/大单/超大单 净流入(元)。"""
    secid = ("1." if code.startswith(("6", "9")) else "0.") + code
    params = {
        "lmt": 0, "klt": 101, "secid": secid,
        "fields1": "f1,f2,f3,f7", "fields2": "f51,f52,f53,f54,f55,f56",
    }
    payload = _get_json_rotated("api/qt/stock/fflow/kline/get", FFLOW_HOSTS, params)
    data = payload.get("data") or {}
    klines = data.get("klines") or []
    rows = []
    for line in klines:
        parts = str(line).split(",")
        if len(parts) < 6:
            continue
        rows.append({
            "trade_date": parts[0].replace("-", ""),
            "main_net_inflow": _num(parts[1]),
            "small_net_inflow": _num(parts[2]),
            "mid_net_inflow": _num(parts[3]),
            "big_net_inflow": _num(parts[4]),
            "super_net_inflow": _num(parts[5]),
        })
    df = pd.DataFrame(rows, columns=FFLOW_COLUMNS)
    return df


_MONEYFLOW_COLUMNS = [
    "trade_date", "code", "name", "main_net_inflow", "super_net_inflow", "big_net_inflow",
]


def fetch_moneyflow_clist(codes: list[str], trade_date: str) -> pd.DataFrame:
    """全市场资金流 clist 批量取（仅限**当日**；f62=主力 f66=超大单 f72=大单，已核对）。"""
    payload = _clist_json({
        "fid": "f62", "pz": 6000,
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
        "fields": "f12,f14,f62,f66,f72",
    })
    diff = (payload.get("data") or {}).get("diff") or []
    want = {str(c).zfill(6) for c in codes}
    rows = [
        {
            "trade_date": trade_date,
            "code": str(item.get("f12", "")),
            "name": str(item.get("f14", "")),
            "main_net_inflow": _num(item.get("f62")),
            "super_net_inflow": _num(item.get("f66")),
            "big_net_inflow": _num(item.get("f72")),
        }
        for item in diff if str(item.get("f12", "")) in want
    ]
    df = pd.DataFrame(rows, columns=_MONEYFLOW_COLUMNS)
    for col in ("main_net_inflow", "super_net_inflow", "big_net_inflow"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _fetch_moneyflow_fflow(codes: list[str], trade_date: str, name_map: dict) -> pd.DataFrame:
    """单股历史资金流（push2his 全历史，任意日期可用）。

    连续 3 次失败即熔断（主机整体不可用时避免逐股重试拖死）。
    """
    rows = []
    fail_streak = 0
    for code in codes:
        code = str(code).zfill(6)
        try:
            kline = fetch_fflow_kline(code)
            hit = kline[kline["trade_date"] == trade_date]
            if hit.empty:
                continue
            r = hit.iloc[0]
            rows.append({
                "trade_date": trade_date,
                "code": code,
                "name": name_map.get(code, ""),
                "main_net_inflow": r["main_net_inflow"],
                "super_net_inflow": r["super_net_inflow"],
                "big_net_inflow": r["big_net_inflow"],
            })
            fail_streak = 0
        except Exception:
            fail_streak += 1
            if fail_streak >= 3:
                break
            continue
    df = pd.DataFrame(rows, columns=_MONEYFLOW_COLUMNS)
    for col in ("main_net_inflow", "super_net_inflow", "big_net_inflow"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def fetch_moneyflow(codes: list[str], trade_date: str, name_map: dict | None = None) -> pd.DataFrame:
    """取一组股票在指定交易日的主力/超大单/大单净流入。

    - 当日：clist 批量（1 请求，delay 主机兜底）**只返回按主力净流入 Top-100**
      （东财 clist 每页固定 100 行，实测 pz=6000 亦如此）——对 clist 拿不到的
      请求代码逐个走单股 fflow 补齐，避免「部分票没数据」；
    - 历史日期：单股 fflow（push2his 全历史）。
    """
    name_map = name_map or {}
    today = datetime.now().strftime("%Y%m%d")
    if trade_date == today:
        try:
            df = fetch_moneyflow_clist(codes, trade_date)
            if not df.empty:
                found = {str(c).zfill(6) for c in df["code"]}
                missing = [c for c in codes if str(c).zfill(6) not in found]
                if missing:
                    df_ff = _fetch_moneyflow_fflow(missing, trade_date, name_map)
                    if not df_ff.empty:
                        df = pd.concat([df, df_ff], ignore_index=True)
                return df
        except Exception:
            pass  # clist 整体失败 → 全部回退 fflow
    return _fetch_moneyflow_fflow(codes, trade_date, name_map)


# ---------------------------------------------------------------- 概念板块 / 行业

# 概念板块领涨字段（已实测 2026-08-11）：f128=领涨股名称、f140=领涨股代码、f136=领涨股涨跌幅%
CONCEPT_BOARD_COLUMNS = [
    "board_code", "board_name", "pct", "main_net_inflow",
    "leader_code", "leader_name", "leader_pct",
]


def fetch_concept_boards() -> pd.DataFrame:
    """概念板块行情（东财概念板块，按涨幅排序，含主力净流入与领涨股）。

    列：board_code/board_name/pct/main_net_inflow
        + 领涨股 leader_code/leader_name/leader_pct（接口未返回领涨字段时自动回退 4 列）。
    实时快照（clist 当前值），仅供当日采集——历史日期复盘不得引用（见 pipeline 守卫）。
    """
    payload = _clist_json({
        "fid": "f3", "pz": 600, "fs": "m:90+t:2",
        "fields": "f12,f14,f3,f62,f128,f140,f136",
    })
    diff = (payload.get("data") or {}).get("diff") or []
    rows = []
    for item in diff:
        rows.append({
            "board_code": str(item.get("f12", "")),
            "board_name": str(item.get("f14", "")),
            "pct": _num(item.get("f3")),
            "main_net_inflow": _num(item.get("f62")),
            "leader_code": str(item.get("f140", "") or ""),
            "leader_name": str(item.get("f128", "") or ""),
            "leader_pct": _num(item.get("f136")),
        })
    df = pd.DataFrame(rows, columns=CONCEPT_BOARD_COLUMNS)
    # 实测降级：接口未返回领涨字段（全空）→ 回退 4 列，下游契约稳定（空表保持 7 列）
    if not df.empty and df["leader_code"].astype(str).str.strip().eq("").all():
        df = df[["board_code", "board_name", "pct", "main_net_inflow"]]
    return df


def fetch_board_constituents(board_code: str) -> list[str]:
    """概念板块成分股代码列表。"""
    payload = _clist_json({
        "fid": "f3", "po": 1, "pz": 3000, "fs": f"b:{board_code}",
        "fields": "f12",
    })
    diff = (payload.get("data") or {}).get("diff") or []
    return [str(item.get("f12", "")) for item in diff if item.get("f12")]


def fetch_stock_industry_map() -> dict[str, str]:
    """全市场 代码→行业 全名映射（clist 全 A，分页拉取）。失败返回空 dict（管道回退池内 hybk）。"""
    result: dict[str, str] = {}
    page = 1
    for _ in range(60):  # 每页 200，60 页覆盖全市场
        payload = _clist_json({
            "fid": "f12", "po": 1, "pn": page, "pz": 200,
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
            "fields": "f12,f100",
        })
        diff = (payload.get("data") or {}).get("diff") or []
        if not diff:
            break
        for item in diff:
            code = str(item.get("f12", ""))
            ind = item.get("f100")
            if code and ind:
                result[code] = str(ind)
        page += 1
        if len(diff) < 200:
            break
    return result
