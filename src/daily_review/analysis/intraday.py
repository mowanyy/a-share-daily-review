"""盘中增量监控（v0.27 D1）：基准快照 + 增量 diff + 时间轴累计曲线。

盘中 9:30-15:00 期间涨停池实时变化：新涨停、炸板、回封、连板高度变动。
现有 `collect()` 的缓存机制不适合追踪变化——它收盘后才覆盖缓存，盘中
多次调用返回同一快照。本模块绕开缓存，直接 HTTP 拉取实时数据做 diff：

- take_baseline()：早盘/首次拉取作为基准（存入 data/intraday/{date}/baseline.json）
- snapshot()：拉取当前快照与基准 diff，结果写入 data/intraday/{date}/snapshots/{seq}.json
- load_snapshots()：读取当日所有增量，拼成累计曲线

数据存储与现有 `data/{date}/` 隔离，不会覆盖收盘数据。
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

from daily_review import config as _config
from daily_review.data import eastmoney_pool

_BASELINE_FILENAME = "baseline.json"
_SNAPSHOTS_DIR = "snapshots"
_SHANGHAI = "Asia/Shanghai"


# ---------------------------------------------------------------- 路径


def _intraday_dir(trade_date: str) -> Path:
    """data/intraday/{trade_date}/"""
    p = _config.get_settings().data_dir / "intraday" / trade_date
    p.mkdir(parents=True, exist_ok=True)
    return p


def _baseline_path(trade_date: str) -> Path:
    return _intraday_dir(trade_date) / _BASELINE_FILENAME


def _snapshots_dir(trade_date: str) -> Path:
    d = _intraday_dir(trade_date) / _SNAPSHOTS_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------- 基准


def _pool_to_dict(df) -> dict:
    """DataFrame → 可 JSON 序列化的 dict（只取关键字段）。"""
    if df is None or df.empty:
        return {"codes": [], "seal_map": {}, "open_times_map": {}}
    codes = list(df["code"].astype(str))
    seal_map = {}
    open_times_map = {}
    for _, r in df.iterrows():
        code = str(r["code"])
        if "seal_amount" in r.index and r["seal_amount"] is not None:
            seal_map[code] = float(r["seal_amount"])
        if "open_times" in r.index and r["open_times"] is not None:
            open_times_map[code] = int(r["open_times"])
    return {
        "codes": codes,
        "seal_map": seal_map,
        "open_times_map": open_times_map,
    }


def _save_json(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def take_baseline(trade_date: str, *, force: bool = False) -> dict:
    """拉取早盘基准快照并落盘。已有基准且非 force 时直接返回。"""
    path = _baseline_path(trade_date)
    if not force and path.exists():
        data = _load_json(path)
        if data is not None:
            return data
    zt = eastmoney_pool.fetch_zt_pool(trade_date)
    zb = eastmoney_pool.fetch_zb_pool(trade_date)
    baseline = {
        "trade_date": trade_date,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "zt": _pool_to_dict(zt),
        "zb": _pool_to_dict(zb),
        "zt_count": len(zt) if zt is not None else 0,
        "zb_count": len(zb) if zb is not None else 0,
    }
    _save_json(path, baseline)
    return baseline


# ---------------------------------------------------------------- diff


def diff(baseline: dict, current_zt, current_zb) -> dict:
    """纯函数：计算当前快照与基准的增量变化。

    参数：
        baseline: take_baseline() 返回的基准 dict
        current_zt/b: fetch_zt/b 返回的 DataFrame
    返回：
        { new_zt, broken, re_sealed, zt_count, zb_count, timestamp }
    """
    base_zt = set(baseline.get("zt", {}).get("codes", []))
    cur_zt = set(current_zt["code"].astype(str)) if current_zt is not None else set()
    base_open = baseline.get("zt", {}).get("open_times_map", {})

    new_zt = sorted(cur_zt - base_zt)
    broken = sorted(base_zt - cur_zt)

    # 回封：基准中已涨停的股，当前仍在涨停池且 open_times 增加
    re_sealed: list[str] = []
    if current_zt is not None:
        for _, r in current_zt.iterrows():
            code = str(r["code"])
            old_open = base_open.get(code)
            if old_open is not None:
                new_open = int(r["open_times"]) if "open_times" in r.index and r["open_times"] is not None else 0
                if new_open > old_open:
                    re_sealed.append(code)

    return {
        "new_zt": new_zt,
        "broken": broken,
        "re_sealed": re_sealed,
        "zt_count": len(cur_zt),
        "zb_count": len(current_zb) if current_zb is not None else 0,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------- 快照


def snapshot(trade_date: str, *, force_baseline: bool = False) -> dict:
    """拉取当前快照，与基准 diff，返回增量并落盘。"""
    baseline = take_baseline(trade_date, force=force_baseline)
    zt = eastmoney_pool.fetch_zt_pool(trade_date)
    zb = eastmoney_pool.fetch_zb_pool(trade_date)
    delta = diff(baseline, zt, zb)
    delta["trade_date"] = trade_date
    # 落盘（按时间戳顺序编号）
    seq = len(list(_snapshots_dir(trade_date).glob("*.json"))) + 1
    _save_json(_snapshots_dir(trade_date) / f"{seq:04d}.json", delta)
    return delta


# ---------------------------------------------------------------- 查询


def load_snapshots(trade_date: str) -> list[dict]:
    """读取当日所有增量记录，按时间升序。"""
    snap_dir = _snapshots_dir(trade_date)
    if not snap_dir.exists():
        return []
    records: list[dict] = []
    for p in sorted(snap_dir.glob("*.json")):
        data = _load_json(p)
        if data is not None:
            records.append(data)
    return records


def summary(trade_date: str) -> dict:
    """当日盘中增量摘要：基准 + 最新快照 + 累计变化。"""
    baseline = _load_json(_baseline_path(trade_date))
    records = load_snapshots(trade_date)
    if not baseline and not records:
        return {"trade_date": trade_date, "status": "no_data", "message": "当日无盘中增量数据"}
    latest = records[-1] if records else {}
    cum_new = set()
    cum_broken = set()
    cum_re_sealed = set()
    for r in records:
        cum_new.update(r.get("new_zt", []))
        cum_broken.update(r.get("broken", []))
        cum_re_sealed.update(r.get("re_sealed", []))
    return {
        "trade_date": trade_date,
        "status": "ok",
        "baseline_zt_count": (baseline or {}).get("zt_count", 0),
        "baseline_zb_count": (baseline or {}).get("zb_count", 0),
        "latest_zt_count": latest.get("zt_count", 0),
        "latest_zb_count": latest.get("zb_count", 0),
        "snapshot_count": len(records),
        "cumulative_new_zt": sorted(cum_new),
        "cumulative_broken": sorted(cum_broken),
        "cumulative_re_sealed": sorted(cum_re_sealed),
    }