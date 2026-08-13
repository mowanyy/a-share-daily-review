"""基金经理分析 agent（v0.18）：Web 问答页「基金经理分析」栏目后端。

- `list_managers()` / `get_manager(id)`：读取 `skills/fund-styles/*.md` 档案
  （front-matter `name`=id，文件 stem=显示名，`description`=触发说明）。
- `analyze(manager_id, question, *, klt)`：按所选基金经理风格档案生成 **system prompt**
  + 从问题里抠 6 位股票代码（去重、限 3 只）注入真实 周K/月K 数据
  （`eastmoney.fetch_kline` klt=102/103，遵循 v0.17.1 周期方法论：不用日K、数据不足明说），
  单次 `llm.client.chat` 回复。无状态、不维护会话历史；LLM 失败降级返回 error 字段（与 QA 一致）。
"""

from __future__ import annotations

import re
from pathlib import Path

from daily_review.config import get_settings
from daily_review.prompts import _FRONT_MATTER_RE, _parse_front_matter

# 基金经理档案目录（项目内 skills/fund-styles/，v0.17 起随项目维护）
_MANAGERS_DIR = "skills/fund-styles"

# 股票代码提取：先剔除「日期形」整体（20YYMMDD / 19YYMMDD），再取 6 位代码（60/00/30/68/8 开头）
_CODE_DAYS_RE = re.compile(r"\b(20\d{6}|19\d{6})\b")
_CODE_RE = re.compile(r"\b([0368]\d{5})\b")
_MAX_CODES = 3

_KLINE_LMT = 36       # 周K/月K 各取 36 条（周≈九个多月、月≈三年，够看趋势/分位）
_KLINE_COLS = ["trade_date", "open", "close", "high", "low", "volume", "pct_change"]
_CYCLE_LABEL = {102: "周K", 103: "月K"}

_ROLE_HEAD = (
    "你是本系统『基金经理分析 · {name}』的独立 agent。\n"
    "只按下面的风格档案规则分析；回复用中文 Markdown；数据不足要明说，禁止编造数据与结论。\n"
    "本次所看周期：{cycle}（klt={klt}）\n"
)
_DISCIPLINE = (
    "\n# 输出纪律\n"
    "- 严格按档案「输出格式建议」章节组织回答，标注所看周期与关键数据日期。\n"
    "- 档案第 0 节写明：只用 {cycle} 判断，不用日K 做风格决策；无周/月K 数据时明说「数据不足」，禁止用日K 冒充。\n"
    "- 若注入数据区含「拉取失败」提示，诚实承认数据缺失，按档案规则给基于可核实信息的倾向。\n"
)


class ManagerNotFound(ValueError):
    """未知基金经理 → HTTP 404。"""


# ---------------------------------------------------------------- 档案读取


def _skills_dir() -> Path:
    return get_settings().project_root / _MANAGERS_DIR


def list_managers() -> list[dict]:
    """扫描全部基金风格档案：[{id, name, description, file}]。"""
    d = _skills_dir()
    if not d.exists():
        return []
    out: list[dict] = []
    for p in sorted(d.glob("*.md")):
        meta = _parse_front_matter(p.read_text(encoding="utf-8"))
        if not meta or not meta.get("name"):
            continue
        out.append(
            {
                "id": str(meta["name"]),
                "name": p.stem,
                "description": str(meta.get("description", "")),
                "file": p.name,
            }
        )
    return out


def get_manager(manager_id: str) -> dict | None:
    for m in list_managers():
        if m["id"] == manager_id:
            return m
    return None


def _load_body(manager_id: str) -> str:
    """读取档案正文（剥离 front-matter）；未知经理抛 ManagerNotFound。"""
    d = _skills_dir()
    if not d.exists():
        raise ManagerNotFound(f"未知基金经理：{manager_id}")
    for p in sorted(d.glob("*.md")):
        text = p.read_text(encoding="utf-8")
        meta = _parse_front_matter(text)
        if meta and meta.get("name") == manager_id:
            return _FRONT_MATTER_RE.sub("", text).strip()
    raise ManagerNotFound(f"未知基金经理：{manager_id}")


# ---------------------------------------------------------------- 数据注入


def _extract_codes(question: str) -> list[str]:
    """从问题里抠股票代码；日期形数字剔除；去重、限 _MAX_CODES 只。"""
    cleaned = _CODE_DAYS_RE.sub(" ", question)
    seen: set[str] = set()
    out: list[str] = []
    for code in _CODE_RE.findall(cleaned):
        if code not in seen:
            seen.add(code)
            out.append(code)
        if len(out) >= _MAX_CODES:
            break
    return out


def _fetch_data(codes: list[str], *, klt: int) -> tuple[str, list[str]]:
    """逐只拉周K/月K → (注入文本块, 数据说明)。失败一律按「数据不足」处理（网络/空数据）。"""
    from daily_review.data import eastmoney

    if not codes:
        return "", []
    cycle = _CYCLE_LABEL.get(klt, f"klt={klt}")
    notes: list[str] = []
    blocks: list[str] = []
    for code in codes:
        try:
            df = eastmoney.fetch_kline(code, klt=klt, lmt=_KLINE_LMT)
            cols = [c for c in _KLINE_COLS if c in df.columns]
            df = df[cols]
            blocks.append(f"### {code}（{cycle}，{len(df)} 条）\n{df.to_string(index=False)}")
            notes.append(f"{code}：{cycle} {len(df)} 条已注入")
        except Exception as exc:  # noqa: BLE001 —— 网络/空数据一律按数据不足处理
            err = f"{type(exc).__name__}: {exc}"
            notes.append(f"{code}：{cycle} 拉取失败（{type(exc).__name__}），按数据不足处理")
            blocks.append(f"### {code}（{cycle}）\n**{cycle} 数据拉取失败：{err}，按数据不足处理**")
    return "\n\n".join(blocks), notes


# ---------------------------------------------------------------- agent


def analyze(manager_id: str, question: str, *, klt: int = 102) -> dict:
    """按基金经理风格 + 周K/月K 数据分析。

    返回 {answer, data_notes, error}；参数/未知经理抛 ValueError（ManagerNotFound → 404）。
    """
    from daily_review.llm.client import LLMError, chat

    question = (question or "").strip()
    if not question:
        raise ValueError("问题不能为空")
    if klt not in _CYCLE_LABEL:
        raise ValueError("klt 仅支持 102(周K) / 103(月K)")
    manager = get_manager(manager_id)
    if manager is None:
        raise ManagerNotFound(f"未知基金经理：{manager_id}")
    body = _load_body(manager_id)
    cycle = _CYCLE_LABEL[klt]

    data_text, data_notes = _fetch_data(_extract_codes(question), klt=klt)
    system = (
        _ROLE_HEAD.format(name=manager["name"], cycle=cycle, klt=klt)
        + body
        + _DISCIPLINE.format(cycle=cycle)
    )
    user = question
    if data_text:
        user += f"\n\n【已注入 {cycle} 数据】\n{data_text}"
    else:
        user += "\n\n（未注入 K 线数据：问题里没有 6 位股票代码。若需数据化分析，请给出 6 位代码。）"

    try:
        answer = chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}]
        )
        error = ""
    except LLMError as exc:
        answer = f"（LLM 调用失败：{exc}）"
        error = str(exc)
    return {"answer": answer, "data_notes": data_notes, "error": error}