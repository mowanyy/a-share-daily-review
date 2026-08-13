"""基金经理分析 agent（v0.20）：上下文记忆 + 中军自动识别 + 风格分析 + 跨 Agent 通信。

- `list_managers()` / `get_manager(id)`：读取 `skills/fund-styles/*.md` 档案。
- `analyze(manager_id, question, *, klt, trade_date)`：按所选风格档案 + 上下文 +
  自动识别的中军（大市值涨停股）+ 周K/月K 数据，`chat_tools` 回复，支持 `query_qa` 工具
  （v0.20：向 QA Agent 查询市场概况）。
- `clear_session(manager_id)`：清空该经理的对话历史与中军跟踪。
- `get_session(manager_id)`：返回当前会话信息（history_length, zhongjun, updated_at）。

Session 持久化到 `data/fund_sessions/{manager_id}.json`（gitignored `data/*/` 已覆盖），
重启 Web 不丢失上下文。中军由系统从当前交易日涨停池按市值自动识别。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from daily_review.config import get_settings
from daily_review.llm.client import chat_tools
from daily_review.llm.reporter import _compact_json
from daily_review.prompts import _FRONT_MATTER_RE, _parse_front_matter

# 基金经理档案目录
_MANAGERS_DIR = "skills/fund-styles"
# 会话持久化目录（gitignored）
_SESSION_DIR = "data/fund_sessions"

# 股票代码提取
_CODE_DAYS_RE = re.compile(r"\b(20\d{6}|19\d{6})\b")
_CODE_RE = re.compile(r"\b([0368]\d{5})\b")
_MAX_CODES = 3

_KLINE_LMT = 36
_KLINE_COLS = ["trade_date", "open", "close", "high", "low", "volume", "pct_change"]
_CYCLE_LABEL = {102: "周K", 103: "月K"}

_HISTORY_MAX_ROUNDS = 10  # 保留最近 10 轮对话

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
    d = _skills_dir()
    if not d.exists():
        raise ManagerNotFound(f"未知基金经理：{manager_id}")
    for p in sorted(d.glob("*.md")):
        text = p.read_text(encoding="utf-8")
        meta = _parse_front_matter(text)
        if meta and meta.get("name") == manager_id:
            return _FRONT_MATTER_RE.sub("", text).strip()
    raise ManagerNotFound(f"未知基金经理：{manager_id}")


# ---------------------------------------------------------------- 会话管理


def _session_dir() -> Path:
    d = get_settings().data_dir / "fund_sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _session_path(manager_id: str) -> Path:
    return _session_dir() / f"{manager_id}.json"


def _default_session(manager_id: str) -> dict:
    return {
        "manager_id": manager_id,
        "messages": [],
        "meta": {"zhongjun": [], "zhongjun_date": None, "trade_date": None},
        "updated_at": datetime.now().isoformat(),
    }


def _load_session(manager_id: str) -> dict:
    """加载或创建会话。"""
    path = _session_path(manager_id)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            # 兼容旧格式
            if "meta" not in data:
                data["meta"] = {"zhongjun": [], "zhongjun_date": None, "trade_date": None}
            if "messages" not in data:
                data["messages"] = []
            return data
        except (json.JSONDecodeError, OSError):
            pass
    return _default_session(manager_id)


def _save_session(session: dict) -> None:
    session["updated_at"] = datetime.now().isoformat()
    _session_path(session["manager_id"]).write_text(
        json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def clear_session(manager_id: str) -> dict:
    """清空会话（删除文件，返回空 session）。"""
    path = _session_path(manager_id)
    if path.exists():
        path.unlink(missing_ok=True)
    return _default_session(manager_id)


def get_session(manager_id: str) -> dict:
    """返回会话信息（不含完整 messages，仅摘要）。"""
    s = _load_session(manager_id)
    return {
        "manager_id": s["manager_id"],
        "history_length": len([m for m in s["messages"] if m["role"] == "user"]),
        "zhongjun": s["meta"].get("zhongjun", []),
        "updated_at": s["updated_at"],
    }


# ---------------------------------------------------------------- 中军自动识别


def _zhongjun_label(entry: dict) -> str:
    """中军单条 → 易读文本。"""
    cap = entry.get("market_cap", 0)
    cap_str = f"{cap / 1e8:.1f}亿" if cap else "?亿"
    theme = entry.get("theme_name", "？")
    return f"{entry.get('code', '')} {entry.get('name', '')}（{theme}，市值{cap_str}）"


def _ensure_zhongjun(session: dict, trade_date: str | None) -> list[dict]:
    """从当日涨停池自动识别中军（大市值股），写入 session meta。

    跳过条件：session 已有中军且 zhongjun_date == trade_date。
    无涨停池 CSV → 空列表 + 不抛异常（agent 仍可正常回答）。
    """
    if not trade_date:
        session["meta"]["zhongjun"] = []
        session["meta"]["zhongjun_date"] = None
        return []
    prev = session["meta"].get("zhongjun", [])
    prev_date = session["meta"].get("zhongjun_date")
    if prev and prev_date == trade_date:
        return prev

    # 加载涨停池 CSV
    from daily_review.data import repo

    try:
        zt = repo.load_csv("zt_pool", trade_date)
    except (FileNotFoundError, OSError):
        # 未采集过 → 中军为空
        session["meta"]["zhongjun"] = []
        session["meta"]["zhongjun_date"] = trade_date
        return []

    if zt.empty:
        session["meta"]["zhongjun"] = []
        session["meta"]["zhongjun_date"] = trade_date
        return []

    # 批量查市值
    codes = [str(c) for c in zt["code"]]
    from daily_review.data import eastmoney_pool

    try:
        caps = eastmoney_pool.fetch_market_caps(codes)
    except (ValueError, Exception):  # 网络失败 → 中军为空
        session["meta"]["zhongjun"] = []
        session["meta"]["zhongjun_date"] = trade_date
        return []

    # 各题材选取市值最大
    zt = zt.copy()
    zt["market_cap"] = zt["code"].astype(str).map(caps)
    zt = zt.dropna(subset=["market_cap"])

    if zt.empty:
        session["meta"]["zhongjun"] = []
        session["meta"]["zhongjun_date"] = trade_date
        return []

    # 按 industry 分组，每组取市值最大
    zhongjun: list[dict] = []
    for _, group in zt.groupby("industry"):
        if group.empty:
            continue
        best = group.loc[group["market_cap"].idxmax()]
        zhongjun.append(
            {
                "code": str(best["code"]),
                "name": str(best["name"]),
                "market_cap": float(best["market_cap"]),
                "theme_name": str(best.get("industry", "")),
            }
        )
    # 按市值降序
    zhongjun.sort(key=lambda x: x["market_cap"], reverse=True)
    session["meta"]["zhongjun"] = zhongjun
    session["meta"]["zhongjun_date"] = trade_date
    return zhongjun


# ---------------------------------------------------------------- 数据注入


def _extract_codes(question: str) -> list[str]:
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
    """逐只拉周K/月K → (注入文本块, 数据说明)。"""
    if not codes:
        return "", []
    from daily_review.data import eastmoney

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
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            notes.append(f"{code}：{cycle} 拉取失败（{type(exc).__name__}），按数据不足处理")
            blocks.append(f"### {code}（{cycle}）\n**{cycle} 数据拉取失败：{err}，按数据不足处理**")
    return "\n\n".join(blocks), notes


# ---------------------------------------------------------------- 构建消息


def _build_system(manager: dict, body: str, cycle: str, klt: int, zhongjun: list[dict]) -> str:
    """构建 system prompt = 角色头 + 档案正文 + 纪律 + 中军摘要。"""
    sys = _ROLE_HEAD.format(name=manager["name"], cycle=cycle, klt=klt) + body + _DISCIPLINE.format(cycle=cycle)
    if zhongjun:
        labels = [_zhongjun_label(z) for z in zhongjun]
        sys += (
            "\n\n# 当前跟踪的中军（大市值趋势股，自动识别）\n"
            + "\n".join(f"- {lab}" for lab in labels)
            + "\n\n按档案规则对上述中军逐只分析走势；若用户询问某只中军，优先使用已注入的 K 线数据。"
        )
    return sys


def _build_messages(
    session: dict, question: str, manager: dict, body: str, cycle: str, klt: int, zhongjun: list[dict], data_text: str
) -> list[dict]:
    """system + 历史（最近 10 轮）+ 新 user。"""
    system = _build_system(manager, body, cycle, klt, zhongjun)
    messages = [{"role": "system", "content": system}]
    # 截取历史：保留最近 _HISTORY_MAX_ROUNDS 轮（每轮 1 user + 1 assistant）
    history = session.get("messages", [])
    if len(history) > _HISTORY_MAX_ROUNDS * 2:
        history = history[-(_HISTORY_MAX_ROUNDS * 2) :]
    messages.extend(history)
    user = question
    if data_text:
        user += f"\n\n【已注入 {cycle} 数据】\n{data_text}"
    else:
        user += "\n\n（未注入 K 线数据：问题里没有 6 位股票代码。若需数据化分析，请给出 6 位代码。）"
    messages.append({"role": "user", "content": user})
    return messages


# ---------------------------------------------------------------- query_qa 工具（v0.20）

_QA_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "query_qa",
        "description": "向知识问答 Agent 查询 A 股短线市场数据，如情绪温度、涨停家数、题材资金流等。获取当前市场概况后可用于辅助风格分析。",
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "查询问题，如「今日市场情绪如何？涨停家数？主要题材？」",
                },
            },
            "required": ["question"],
        },
    },
}

_MAX_TOOL_ROUNDS = 3  # 基金经理 tool 循环上限


def _execute_query_qa(args: dict) -> str:
    """执行 query_qa 工具：调用 QA Agent 并返回回答。"""
    question = str(args.get("question") or "").strip()
    if not question:
        return _compact_json({"error": "问题不能为空"})
    try:
        from daily_review.web.agent_registry import call_agent

        answer = call_agent("qa_general", question)
        return _compact_json({"answer": answer})
    except Exception as exc:
        return _compact_json({"error": f"调用 QA Agent 失败：{exc}"})


# ---------------------------------------------------------------- agent


def analyze(
    manager_id: str,
    question: str,
    *,
    klt: int = 102,
    trade_date: str | None = None,
) -> dict:
    """按基金经理风格 + 上下文 + 中军数据 + 周K/月K 分析，返回 {answer, data_notes, error, history_length, zhongjun}。

    支持 query_qa 工具调用（v0.20）：基金经理可向 QA Agent 查询市场概况。
    参数/未知经理抛 ValueError（ManagerNotFound → 404）。
    """
    from daily_review.llm.client import LLMError

    question = (question or "").strip()
    if not question:
        raise ValueError("问题不能为空")
    if klt not in _CYCLE_LABEL:
        raise ValueError("klt 仅支持 102(周K) / 103(月K)")
    if not manager_id:
        raise ValueError("manager_id 不能为空")
    manager = get_manager(manager_id)
    if manager is None:
        raise ManagerNotFound(f"未知基金经理：{manager_id}")
    body = _load_body(manager_id)
    cycle = _CYCLE_LABEL[klt]

    # 会话
    session = _load_session(manager_id)
    zhongjun = _ensure_zhongjun(session, trade_date)

    # 数据注入
    data_text, data_notes = _fetch_data(_extract_codes(question), klt=klt)

    # 构建消息 + 调用 LLM（v0.20：chat → chat_tools，支持 query_qa 工具）
    messages = _build_messages(session, question, manager, body, cycle, klt, zhongjun, data_text)
    try:
        answer, error = _run_tool_loop(messages, cycle)
        # 记录历史（只记录 user/assistant，不记录工具调用中间消息）
        session["messages"].append({"role": "user", "content": question, "timestamp": datetime.now().isoformat()})
        session["messages"].append({"role": "assistant", "content": answer, "timestamp": datetime.now().isoformat()})
        # 裁剪历史：保留最近 _HISTORY_MAX_ROUNDS 轮
        if len(session["messages"]) > _HISTORY_MAX_ROUNDS * 2:
            session["messages"] = session["messages"][-(_HISTORY_MAX_ROUNDS * 2) :]
    except LLMError as exc:
        answer = f"（LLM 调用失败：{exc}）"
        error = str(exc)
    # 保存会话（无论成功/失败都保存，失败时保留历史以便重试）
    _save_session(session)

    return {
        "answer": answer,
        "data_notes": data_notes,
        "error": error,
        "history_length": len([m for m in session["messages"] if m["role"] == "user"]),
        "zhongjun": session["meta"].get("zhongjun", []),
    }


def _run_tool_loop(messages: list[dict], cycle: str) -> tuple[str, str]:
    """工具循环：chat_tools → 若调用 query_qa 则执行并回传结果 → 再问，最多 3 轮。"""
    error = ""
    for _round in range(_MAX_TOOL_ROUNDS):
        result = chat_tools(messages, tools=[_QA_TOOL_SCHEMA], tool_choice="auto")
        if not result.tool_calls:
            return (result.content or "", error)
        # 执行工具调用
        assistant_msg: dict = {
            "role": "assistant",
            "content": result.content,
            "tool_calls": result.raw_tool_calls or [],
        }
        if result.reasoning_content:
            assistant_msg["reasoning_content"] = result.reasoning_content
        messages.append(assistant_msg)
        for tc in result.tool_calls:
            tool_result = _execute_query_qa(tc.arguments)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": tool_result})
    # 超限：取最后一次 content
    last = chat_tools(messages, tools=[_QA_TOOL_SCHEMA], tool_choice="auto")
    return (last.content or "", f"（已达 {_MAX_TOOL_ROUNDS} 轮工具调用上限）")