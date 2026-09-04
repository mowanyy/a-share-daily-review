"""新浪实时行情接口（移植自共享脚本：hq.sinajs.cn，gbk 编码）。"""

from __future__ import annotations

import re

import pandas as pd

from daily_review.data.http_client import get_text

QUOTE_BASE = "https://hq.sinajs.cn/list="
_PREFIX_MAP = {"6": "sh", "9": "sh", "0": "sz", "3": "sz", "4": "bj", "8": "bj"}

# 新浪返回字段布局（0 基索引，与参考脚本 rename_dict 对应）
_FIELD_INDEX = {
    1: "open",
    2: "pre_close",
    3: "close",
    4: "high",
    5: "low",
    6: "buy1",
    7: "sell1",
    8: "volume",
    9: "amount",
    30: "candle_date",
    31: "candle_time",
    32: "status",
}
_COLUMN_ORDER = [
    "stock_code",
    "stock_name",
    "candle_end_time",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "volume",
    "amount",
    "buy1",
    "sell1",
    "status",
]


def prefix_of(code: str) -> str:
    """股票代码 → 新浪市场前缀：6/9→sh，0/3→sz，4/8→bj。"""
    code = code.strip()
    return _PREFIX_MAP.get(code[0], "sh")


def build_quote_url(codes: list[str]) -> str:
    """构造新浪实时行情 URL；过滤空串与非法项。"""
    parts = []
    for c in codes:
        c = c.strip()
        if not c or not re.fullmatch(r"\d{6}", c):
            continue
        parts.append(prefix_of(c) + c)
    return QUOTE_BASE + ",".join(parts)


def _num(value: str | None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _field(fields: list[str], idx: int) -> str | None:
    if idx < len(fields):
        return fields[idx].strip()
    return None


def _parse_line(line: str) -> dict | None:
    """解析一行 `var hq_str_sh600601="名称,...";`；停牌/空数据行返回 None。"""
    line = line.strip()
    if not line.startswith("var hq_str_"):
        return None
    line = line.replace("var hq_str_", "")
    if '="' not in line:
        return None
    head, _, payload = line.partition('="')
    fields = payload.rstrip('";').split(",")
    # 空数据（停牌/退市/不存在）: 名称后无有效行情
    if len(fields) < 2 or not fields[1].strip():
        return None

    m = re.match(r"[a-z]+(\d{6})", head)
    code = m.group(1) if m else head.strip()
    name = fields[0].strip()

    row = {"stock_code": code, "stock_name": name}
    # candle_end_time = 日期 + 时间
    date, time_ = _field(fields, 30), _field(fields, 31)
    if date and time_:
        row["candle_end_time"] = pd.Timestamp(f"{date} {time_}")
    else:
        row["candle_end_time"] = None
    for idx, col in _FIELD_INDEX.items():
        if col in {"candle_date", "candle_time"}:
            continue
        row[col] = _num(_field(fields, idx))
    row["status"] = _field(fields, 32)
    return row


def fetch_realtime(codes: list[str]) -> pd.DataFrame:
    """获取一组股票的实时行情，返回 DataFrame（列见 _COLUMN_ORDER）。"""
    url = build_quote_url(codes)
    if not url.endswith("list="):
        text = get_text(url)
        rows = [_parse_line(line) for line in text.splitlines()]
        rows = [r for r in rows if r is not None]
    else:
        raise ValueError("股票代码列表为空")
    if not rows:
        raise ValueError(f"实时行情数据为空（URL {url}）")
    return pd.DataFrame(rows, columns=_COLUMN_ORDER)
