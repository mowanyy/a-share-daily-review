"""报告数字抽取（v0.37 L1 回比前置）：章节无关的全文本抽取 + 约数归一化。

设计约定（docs/Agent内容评估方案.md 4.2 节）：
- 数字在「一、总览」等 LLM 生成部分首次出现（代码拼装章节恒与快照一致），
  故用全文本**首次出现**抽取即可，不必做严格章节定位；
- 「约 80 只」「近 20%」等约数前缀剥离后再比对；
- 百分比（20.2%）与小数（0.202）单位换算在 verify.py 里按字段定义。
"""

from __future__ import annotations

import re

# 约数前缀
_APPROX_PREFIX = re.compile(r"^(约|近|大约|约莫|差不多|大概|接近|约计)\s*")
_FLOAT = re.compile(r"^-?(\d+\.?\d*|\.\d+)$")
_PERCENT = re.compile(r"^-?(\d+\.?\d*|\.\d+)\s*%$")

# 各字段抽取模式
_EMOTION_SCORE = re.compile(r"(\d+(?:\.\d+)?)\s*分")                 # 情绪温度 73.3 分
_ZT_COUNT = re.compile(r"涨停\s*(\d+)\s*家")                          # 涨停 79 家
_LIANBAN_COUNT = re.compile(r"连板\s*(\d+)\s*家")                     # 连板 22 家
_FIRST_BOARD_COUNT = re.compile(r"首板\s*(\d+)\s*家")                 # 首板 57 家
_MAX_LB = re.compile(r"(?:最高板|空间板)[^（）(\d]*(\d+)\s*板")        # 最高板 10 板 / 空间板 10 板
_MAX_LB_STOCK = re.compile(
    r"(?:最高板|空间板)[^（）(]*\d+\s*板[（(]\s*(?:\d{6}\s*)?([^（）)]+)[）)]"
)                                                                     # …10 板（爱丽家居）/（603221 爱丽家居）
_BREAK_RATE = re.compile(r"炸板率\s*(\d+(?:\.\d+)?)\s*%")             # 炸板率 20.2%
_PROMO_PAIR = re.compile(r"(\d+)进(\d+)")                             # 3进4
_PERCENT_IN = re.compile(r"(\d+(?:\.\d+)?)\s*%")                      # 句内百分比
_PREV_EMOTION = re.compile(r"昨日情绪温度\s*(\d+(?:\.\d+)?)")         # 昨日情绪温度 54.8


def strip_approx(s: str) -> str:
    """剥离「约/近/大约…」前缀。"""
    s = s.strip()
    m = _APPROX_PREFIX.match(s)
    return s[m.end():] if m else s


def to_number(s: str) -> float | None:
    """把字符串归一成数值：支持整数/小数/百分比；约数前缀忽略；非法返回 None。"""
    s = strip_approx(s)
    if _PERCENT.fullmatch(s):
        return float(s[:-1].strip())
    if _FLOAT.fullmatch(s):
        return float(s)
    return None


def extract_emotion_score(text: str) -> float | None:
    m = _EMOTION_SCORE.search(text)
    return float(m.group(1)) if m else None


def extract_zt_count(text: str) -> int | None:
    m = _ZT_COUNT.search(text)
    return int(m.group(1)) if m else None


def extract_lianban_count(text: str) -> int | None:
    m = _LIANBAN_COUNT.search(text)
    return int(m.group(1)) if m else None


def extract_first_board_count(text: str) -> int | None:
    m = _FIRST_BOARD_COUNT.search(text)
    return int(m.group(1)) if m else None


def extract_max_lb(text: str) -> int | None:
    m = _MAX_LB.search(text)
    return int(m.group(1)) if m else None


def extract_max_lb_stock(text: str) -> str | None:
    m = _MAX_LB_STOCK.search(text)
    return m.group(1).strip() if m else None


def extract_break_rate(text: str) -> float | None:
    m = _BREAK_RATE.search(text)
    return float(m.group(1)) if m else None


def extract_promotions(text: str) -> list[tuple[str, float]]:
    """抽取「3进4 12.68%」类晋级率，返回 [(key, 百分比), …]，同 key 取首次出现。"""
    out: list[tuple[str, float]] = []
    seen: set[str] = set()
    for m in _PROMO_PAIR.finditer(text):
        key = f"{m.group(1)}进{m.group(2)}"
        line_end = text.find("\n", m.end())
        line = text[m.start(): line_end if line_end != -1 else len(text)]
        pm = _PERCENT_IN.search(line)
        if pm and key not in seen:
            seen.add(key)
            out.append((key, float(pm.group(1))))
    return out


def extract_prev_emotion(text: str) -> float | None:
    m = _PREV_EMOTION.search(text)
    return float(m.group(1)) if m else None