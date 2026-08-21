"""Web 工作台路由：5 个页面 + JSON API。

重模块（kb.index / kb.qa / dashboard / pipeline）全部在 handler 内惰性 import，
避免 import daily_review.web 时加载额外依赖（对齐 cli.py 的模式）。
"""

from __future__ import annotations

import re
import threading
from datetime import datetime

from flask import Blueprint, current_app, jsonify, render_template, request

from daily_review.web.jobs import JobBusy
from daily_review.web.md import md_to_html
from daily_review.web.strategy import (
    StrategyError,
    create as strategy_create,
    delete as strategy_delete,
    iter_all,
    set_status as strategy_set_status,
    to_dict,
    update as strategy_update,
)

pages_bp = Blueprint("pages", __name__)
api_bp = Blueprint("api", __name__)

_DATE_RE = re.compile(r"^\d{8}$")


def _recent_date() -> str:
    """缺省交易日：探测最近有涨停数据的交易日（空则退化为今天）。"""
    from daily_review.data import eastmoney_pool

    today = datetime.today().strftime("%Y%m%d")
    dates = eastmoney_pool.resolve_recent_trade_dates(today, n_days=1)
    return dates[0] if dates else today


# ---------------------------------------------------------------- 页面


@pages_bp.get("/")
def index():
    return render_template("index.html")


@pages_bp.get("/strategies")
def strategies_page():
    return render_template("strategies.html")


@pages_bp.get("/review")
def review_page():
    return render_template("review.html")


@pages_bp.get("/qa")
def qa_page():
    return render_template("qa.html")


@pages_bp.get("/dashboard")
def dashboard_page():
    return render_template("dashboard.html")


@pages_bp.get("/concepts")
def concepts_page():
    return render_template("concepts.html")


@pages_bp.get("/audit")
def audit_page():
    return render_template("audit.html")


# ---------------------------------------------------------------- 战法 API


@api_bp.get("/api/strategies")
def api_list_strategies():
    return jsonify({"strategies": [to_dict(p) for p in iter_all()]})


@api_bp.get("/api/strategies/<strategy_id>")
def api_get_strategy(strategy_id: str):
    from daily_review.web.strategy import get_strategy

    p = get_strategy(strategy_id)
    if p is None:
        return jsonify({"error": "未找到战法"}), 404
    data = to_dict(p)
    data["body"] = p.body
    return jsonify(data)


@api_bp.post("/api/strategies")
def api_create_strategy():
    data = request.get_json(silent=True) or {}
    markdown = str(data.get("markdown", "")).strip()
    if not markdown:
        return jsonify({"error": "缺少战法正文"}), 400
    try:
        pr = strategy_create(
            markdown,
            name=str(data.get("name", "")),
            author=str(data.get("author", "")),
            applies_to=str(data.get("applies_to", "")),
            status=str(data.get("status", "draft")),
        )
    except StrategyError as exc:
        return jsonify({"error": str(exc)}), exc.code
    return jsonify(to_dict(pr)), 201


@api_bp.put("/api/strategies/<strategy_id>")
def api_update_strategy(strategy_id: str):
    data = request.get_json(silent=True) or {}
    markdown = str(data.get("markdown", "")).strip()
    if not markdown:
        return jsonify({"error": "缺少战法正文"}), 400
    try:
        pr = strategy_update(
            strategy_id,
            markdown,
            name=str(data.get("name", "")),
            author=str(data.get("author", "")),
            applies_to=str(data.get("applies_to", "")),
            status=str(data.get("status", "")),
        )
    except StrategyError as exc:
        return jsonify({"error": str(exc)}), exc.code
    data_dict = to_dict(pr)
    data_dict["body"] = pr.body
    return jsonify(data_dict)


@api_bp.delete("/api/strategies/<strategy_id>")
def api_delete_strategy(strategy_id: str):
    try:
        strategy_delete(strategy_id)
    except StrategyError as exc:
        return jsonify({"error": str(exc)}), exc.code
    return jsonify({"ok": True})


@api_bp.post("/api/strategies/<strategy_id>/status")
def api_set_strategy_status(strategy_id: str):
    data = request.get_json(silent=True) or {}
    status = str(data.get("status", ""))
    try:
        pr = strategy_set_status(strategy_id, status)
    except StrategyError as exc:
        return jsonify({"error": str(exc)}), exc.code
    return jsonify(to_dict(pr))


@api_bp.post("/api/strategies/import-skill")
def api_import_skill():
    """导入 SKILL.md 为个人战法（v0.17 战法↔skill 桥）。"""
    from daily_review.web.skill_bridge import import_skill

    data = request.get_json(silent=True) or {}
    markdown = str(data.get("markdown", "")).strip()
    if not markdown:
        return jsonify({"error": "缺少 SKILL.md 内容"}), 400
    try:
        result = import_skill(markdown, status=str(data.get("status", "draft")))
    except (ValueError, StrategyError) as exc:
        code = exc.code if isinstance(exc, StrategyError) else 400
        return jsonify({"error": str(exc)}), code
    return jsonify(result), 201


@api_bp.get("/api/strategies/<strategy_id>/export-skill")
def api_export_skill(strategy_id: str):
    """导出战法为 SKILL.md 文本（tracked/示例与用户战法均支持）。"""
    from daily_review.web.skill_bridge import export_strategy

    try:
        skill_markdown = export_strategy(strategy_id)
    except StrategyError as exc:
        return jsonify({"error": str(exc)}), exc.code
    return jsonify({"strategy_id": strategy_id, "skill_markdown": skill_markdown})


# ---------------------------------------------------------------- 复盘 API


@api_bp.get("/api/review/recent_date")
def api_recent_date():
    return jsonify({"date": _recent_date()})


@api_bp.post("/api/review/start")
def api_review_start():
    data = request.get_json(silent=True) or {}
    trade_date = str(data.get("trade_date", "")).strip()
    if not _DATE_RE.fullmatch(trade_date):
        return jsonify({"error": "trade_date 需为 YYYYMMDD"}), 400
    strategy_id = str(data.get("strategy_id", "")).strip()
    no_llm = bool(data.get("no_llm", False))
    jobs = current_app.extensions["jobs"]
    try:
        job = jobs.start_review(trade_date=trade_date, strategy_id=strategy_id, no_llm=no_llm)
    except JobBusy as exc:
        return jsonify({"error": str(exc)}), 409
    return jsonify({"job_id": job.id}), 202


@api_bp.post("/api/plan/start")
def api_plan_start():
    """隔夜预案（9:00 前）：昨日复盘 + 东财7x24隔夜消息 → 今日关注方向。"""
    data = request.get_json(silent=True) or {}
    trade_date = str(data.get("trade_date", "")).strip()
    if not _DATE_RE.fullmatch(trade_date):
        return jsonify({"error": "trade_date 需为 YYYYMMDD"}), 400
    jobs = current_app.extensions["jobs"]
    try:
        job = jobs.start_plan(trade_date=trade_date)
    except JobBusy as exc:
        return jsonify({"error": str(exc)}), 409
    return jsonify({"job_id": job.id}), 202


@api_bp.post("/api/open/start")
def api_open_start():
    """开盘策略（9:25-9:30）：竞价数据 + 隔夜预案 → 有机会的个股。"""
    data = request.get_json(silent=True) or {}
    trade_date = str(data.get("trade_date", "")).strip()
    if not _DATE_RE.fullmatch(trade_date):
        return jsonify({"error": "trade_date 需为 YYYYMMDD"}), 400
    jobs = current_app.extensions["jobs"]
    try:
        job = jobs.start_open(trade_date=trade_date)
    except JobBusy as exc:
        return jsonify({"error": str(exc)}), 409
    return jsonify({"job_id": job.id}), 202


# ---------------------------------------------------------------- 数据更新 API


@api_bp.post("/api/data/update")
def api_data_update():
    """手动一键更新数据：刷新静态缓存 + 重采 N 天。"""
    data = request.get_json(silent=True) or {}
    trade_date = str(data.get("trade_date", "")).strip() or _recent_date()
    if not _DATE_RE.fullmatch(trade_date):
        return jsonify({"error": "trade_date 需为 YYYYMMDD"}), 400
    days = max(1, int(data.get("days", 6)))
    force = bool(data.get("force", False))
    jobs = current_app.extensions["jobs"]
    try:
        job = jobs.start_data_update(trade_date=trade_date, days=days, force=force)
    except JobBusy as exc:
        return jsonify({"error": str(exc)}), 409
    return jsonify({"job_id": job.id}), 202


# ---------------------------------------------------------------- 概念池 API


@api_bp.get("/api/concept-pools")
def api_concept_pool_list():
    from daily_review.web.concept_pool import list_pools

    return jsonify({"concept_pools": list_pools()})


@api_bp.post("/api/concept-pools")
def api_concept_pool_create():
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()
    if not name:
        return jsonify({"error": "概念池名称不能为空"}), 400
    from daily_review.web.concept_pool import create_pool

    try:
        result = create_pool(name, description=str(data.get("description", "")))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result), 201


@api_bp.delete("/api/concept-pools/<name>")
def api_concept_pool_delete(name: str):
    from daily_review.web.concept_pool import delete_pool, query_pool

    pool = query_pool(name)
    if pool is None:
        return jsonify({"error": f"概念池「{name}」不存在"}), 404
    result = delete_pool(name)
    return jsonify(result)


@api_bp.get("/api/concept-pools/<name>")
def api_concept_pool_query(name: str):
    from daily_review.web.concept_pool import list_pools, query_pool

    stocks = query_pool(name)
    if stocks is None:
        return jsonify({"error": f"概念池「{name}」不存在", "available": [p["name"] for p in list_pools()]}), 404
    return jsonify({"name": name, "stock_count": len(stocks), "stocks": stocks})


@api_bp.post("/api/concept-pools/<name>/stocks")
def api_concept_pool_add_stocks(name: str):
    data = request.get_json(silent=True) or {}
    stocks = data.get("stocks", [])
    if not stocks:
        return jsonify({"error": "缺少 stocks 参数"}), 400
    if isinstance(stocks, dict):
        stocks = [{"code": c, "name": n} for c, n in stocks.items()]
    from daily_review.web.concept_pool import add_stocks

    try:
        result = add_stocks(name, stocks)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)


@api_bp.delete("/api/concept-pools/<name>/stocks")
def api_concept_pool_remove_stocks(name: str):
    data = request.get_json(silent=True) or {}
    codes = str(data.get("codes", "")).strip()
    codes_list = [c.strip() for c in codes.replace("，", ",").split(",") if c.strip()]
    if not codes_list:
        return jsonify({"error": "缺少 codes 参数"}), 400
    from daily_review.web.concept_pool import remove_stocks

    result = remove_stocks(name, codes_list)
    return jsonify(result)


@api_bp.get("/api/review/status/<job_id>")
def api_review_status(job_id: str):
    job = current_app.extensions["jobs"].status(job_id)
    if job is None:
        return jsonify({"error": "任务不存在"}), 404
    return jsonify(job.to_dict())


@api_bp.get("/api/review/history")
def api_review_history():
    """历史报告列表：扫描 output/ 已有复盘/隔夜预案/开盘策略/看板，按日倒序。"""
    from daily_review.web.history import list_reports

    return jsonify({"reports": list_reports()})


@api_bp.get("/api/review/history/<date>")
def api_review_history_day(date: str):
    """历史报告读回（多日复盘用）：{date} 的全文 + 次日预案章节；支持 ?type=review|plan|open。

    默认 type=review（复盘）；plan=隔夜预案；open=开盘策略。无对应文件 → 404。
    """
    from daily_review.web.history import load_artifact

    artifact_type = str(request.args.get("type", "review")).strip()
    if artifact_type not in ("review", "plan", "open"):
        return jsonify({"error": f"type 需为 review/plan/open，收到：{artifact_type}"}), 400
    try:
        report = load_artifact(date, artifact_type)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if report is None:
        label = {"review": "复盘报告", "plan": "隔夜预案", "open": "开盘策略"}.get(artifact_type, "产物")
        return jsonify({"error": f"未找到 {date} 的{label}（先运行 review/plan/open 生成）"}), 404
    return jsonify(report)


# ---------------------------------------------------------------- 基金经理分析 API


@api_bp.get("/api/fund/managers")
def api_fund_managers():
    """基金经理风格清单（skills/fund-styles/*.md，前端下拉用）。"""
    from daily_review.web.fund_agent import list_managers

    return jsonify({"managers": list_managers()})


@api_bp.post("/api/fund/analyze")
def api_fund_analyze():
    """基金经理分析 agent（v0.19：上下文记忆 + 中军自动识别）。"""
    from daily_review.web.fund_agent import ManagerNotFound, analyze as fund_analyze

    data = request.get_json(silent=True) or {}
    manager_id = str(data.get("manager_id", "")).strip()
    question = str(data.get("question", "")).strip()
    if not manager_id:
        return jsonify({"error": "缺少 manager_id"}), 400
    if not question:
        return jsonify({"error": "问题不能为空"}), 400
    try:
        klt = int(data.get("klt", 102) or 102)
    except (TypeError, ValueError):
        return jsonify({"error": "klt 需为 102(周K) 或 103(月K)"}), 400
    trade_date = str(data.get("trade_date", "")).strip() or None
    try:
        result = fund_analyze(manager_id, question, klt=klt, trade_date=trade_date)
    except ManagerNotFound as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(
        {
            "answer": result["answer"],
            "answer_html": md_to_html(result["answer"]),
            "data_notes": result["data_notes"],
            "error": result["error"],
            "history_length": result["history_length"],
            "zhongjun": result["zhongjun"],
        }
    )


@api_bp.post("/api/fund/clear/<manager_id>")
def api_fund_clear(manager_id: str):
    """清空该基金经理的会话历史与中军跟踪。"""
    from daily_review.web.fund_agent import clear_session

    clear_session(manager_id)
    return jsonify({"ok": True})


@api_bp.get("/api/fund/session/<manager_id>")
def api_fund_session(manager_id: str):
    """返回会话信息（history_length, zhongjun, updated_at）。"""
    from daily_review.web.fund_agent import get_session

    return jsonify(get_session(manager_id))


# ---------------------------------------------------------------- 多 Agent 通信 API（v0.20）


@api_bp.get("/api/agents/list")
def api_agents_list():
    """列出所有已注册 Agent。"""
    from daily_review.web.agent_registry import list_agents

    return jsonify({"agents": list_agents()})


@api_bp.post("/api/agents/consult")
def api_agents_consult():
    """多 Agent 会诊：并行调用多个 Agent，综合各方观点。"""
    from daily_review.web.agent_registry import call_agent, list_agents

    data = request.get_json(silent=True) or {}
    question = str(data.get("question", "")).strip()
    agent_ids = data.get("agent_ids", [])
    if not question:
        return jsonify({"error": "问题不能为空"}), 400
    if not agent_ids or not isinstance(agent_ids, list):
        return jsonify({"error": "请选择至少一个 Agent（agent_ids 列表）"}), 400

    # 去重（保持传入顺序）
    agent_ids = list(dict.fromkeys(agent_ids))

    # 校验 agent_ids
    registered = {a["id"] for a in list_agents()}
    invalid = [a for a in agent_ids if a not in registered]
    if invalid:
        return jsonify({"error": f"未知 Agent：{', '.join(invalid)}", "available": sorted(registered)}), 400

    # 顺序调用每个 Agent（不并行，避免 LLM 并发冲突）
    responses: dict[str, dict] = {}
    for aid in agent_ids:
        answer = call_agent(aid, question)
        responses[aid] = {"answer": answer, "answer_html": md_to_html(answer)}

    # 合成 LLM 调用
    synthesis = _synthesize_consult(question, responses)
    synthesis_html = md_to_html(synthesis)

    return jsonify({"responses": responses, "synthesis": synthesis, "synthesis_html": synthesis_html})


def _synthesize_consult(question: str, responses: dict[str, dict]) -> str:
    """综合多个 Agent 的观点，生成最终结论。"""
    from daily_review.llm.client import chat

    system = (
        "你是多 Agent 会诊分析师。用户的问题已由多位专家（Agent）分别分析，"
        "每个专家有各自的投资风格和视角。请综合他们的观点，给出一个全面、客观、结构化的综合结论。\n"
        "要求：\n"
        "1. 先指出各专家观点的共同点和分歧点\n"
        "2. 对不同观点给出你的分析判断\n"
        "3. 最终给出一个综合性的建议或结论\n"
        "4. 用中文 Markdown 格式输出，结构清晰\n"
        "5. 禁止编造数据，只基于下方各专家的回答进行综合"
    )
    parts = [f"## 用户问题\n\n{question}\n\n## 各专家观点\n"]
    for aid, resp in responses.items():
        parts.append(f"### {aid}\n\n{resp['answer']}\n")
    user = "\n".join(parts)
    try:
        return chat([{"role": "system", "content": system}, {"role": "user", "content": user}])
    except Exception:
        # 合成失败 → 直接拼接各专家观点
        lines = ["### 综合结论\n\n（合成 LLM 调用失败，以下为各专家原始观点）\n"]
        for aid, resp in responses.items():
            lines.append(f"---\n### {aid}\n\n{resp['answer']}\n")
        return "\n".join(lines)


# ---------------------------------------------------------------- 问答 API

_index_lock = threading.Lock()
_index = None  # 懒加载共享 KnowledgeIndex（threading.Lock 保护）


def _get_index():
    """懒加载共享索引：web QA 默认 use_embedding=True，与 CLI 共享 .kb_cache/（防重建震荡）。"""
    global _index
    with _index_lock:
        if _index is None:
            from daily_review.kb.index import KnowledgeIndex

            idx = KnowledgeIndex(use_embedding=True)
            idx.ensure_ready()
            _index = idx
        return _index


@api_bp.post("/api/qa/ask")
def api_qa_ask():
    data = request.get_json(silent=True) or {}
    question = str(data.get("question", "")).strip()
    if not question:
        return jsonify({"error": "问题不能为空"}), 400

    from daily_review.kb.qa import QASession

    index = _get_index()
    trade_date = str(data.get("date", "")).strip() or _recent_date()
    session = QASession(index, trade_date=trade_date, top_k=5, use_embedding=True)
    result = session.answer(question)
    resp = {
        "answer": result.answer,
        "answer_html": md_to_html(result.answer),
        "sources": [
            {
                "source_rel": h.source_rel,
                "section": h.section,
                "text": h.text,
                "score": round(float(h.score), 4),
                "date": h.date,
            }
            for h in result.sources
        ],
        "tool_rounds": result.tool_rounds,
        "error": result.error,
    }
    # v0.35：工具调用 Trace
    if result.trace is not None:
        trace_dict = result.trace.to_dict()
        resp["trace"] = trace_dict
        # 写入审计日志
        try:
            import json as _json
            audit_db = current_app.extensions.get("audit_db")
            if audit_db is not None:
                audit_db.log_trace("web", question, _json.dumps(trace_dict, ensure_ascii=False))
        except Exception:
            pass
    return jsonify(resp)


# ---------------------------------------------------------------- 数据看板 API

_CLOSE_TIME = datetime.strptime("18:00", "%H:%M").time()
# 盘中缓存 TTL：当日数据实时变动（涨跌停 15:00 收盘，龙虎榜盘后完整），缓存 10 分钟；
# 历史日期/收盘后（18:00 龙虎榜齐）定稿，进程内不失效
_INTRADAY_TTL_SECONDS = 600


def _clock() -> datetime:
    return datetime.now()


def _dashboard_cache_is_fresh(trade_date: str, stored_ts: float) -> bool:
    """看板缓存/文件是否仍有效：历史日期定稿；今日盘中 10 分钟 TTL；今日 18:00 后须 18:00 之后生成。

    定稿边界取 18:00 而非 15:00：看板含龙虎榜章节，榜单盘后 18:00 才完整，
    15:00–18:00 之间生成的快照龙虎榜为空，不能当最终版缓存。
    """
    now = _clock()
    if trade_date != now.strftime("%Y%m%d"):
        return True  # 历史日期数据已定稿
    if now.time() >= _CLOSE_TIME:
        close_ts = datetime.strptime(now.strftime("%Y%m%d") + "180000", "%Y%m%d%H%M%S").timestamp()
        return stored_ts >= close_ts  # 收盘后：18:00 前生成的盘中快照视为过期
    return (now.timestamp() - stored_ts) <= _INTRADAY_TTL_SECONDS  # 盘中：10 分钟


class DashboardCache:
    """进程内看板 HTML 缓存（每 app 一份，测试隔离；最多 MAX 条，逐出最旧）。"""

    MAX = 16

    def __init__(self):
        self._items: dict[tuple, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def get(self, key: tuple, trade_date: str) -> str | None:
        with self._lock:
            item = self._items.get(key)
            if item and _dashboard_cache_is_fresh(trade_date, item[1]):
                return item[0]
        return None

    def set(self, key: tuple, html: str) -> None:
        with self._lock:
            self._items[key] = (html, _clock().timestamp())
            if len(self._items) > self.MAX:
                oldest = min(self._items, key=lambda k: self._items[k][1])
                del self._items[oldest]


# 单飞：同 (date, days, no_llm) 并发首个请求只生成一次，其余等待缓存命中
_GENERATION_LOCKS: dict[tuple, threading.Lock] = {}
_GENERATION_LOCKS_GUARD = threading.Lock()


def _generation_lock(key: tuple) -> threading.Lock:
    """取/建 per-key 生成锁（并发重复请求串行化；key 空间=请求过的参数组合，量小）。"""
    with _GENERATION_LOCKS_GUARD:
        lock = _GENERATION_LOCKS.get(key)
        if lock is None:
            lock = _GENERATION_LOCKS.setdefault(key, threading.Lock())
        return lock


def _file_matches_request(text: str, trade_date: str, n_days: int, no_llm: bool) -> bool:
    """看板文件内容核对：文件名不编码 n_days/no_llm，复用前须确认窗口与 LLM 开关一致。

    - n_days：从 `const DATA` 里取 "n_days":N 核对；
    - LLM 开关：无解读时 HTML 含固定占位「（未生成解读）」，据此判断文件是否带解读。
    """
    m = re.search(r'"n_days":\s*(\d+)', text)
    if not m or int(m.group(1)) != n_days:
        return False
    file_has_llm = "（未生成解读）" not in text
    return file_has_llm != no_llm  # 用户要解读文件须有；用户不要解读文件须无


def _serve_existing_dashboard_file(trade_date: str, n_days: int, no_llm: bool) -> str | None:
    """复用已生成的 output/{date}_看板.html（历史日期/收盘后定稿才复用；内容与请求核对）。

    首次生成很慢（联网采集），若 CLI/启动器/Web 已生成过看板文件，web 直接秒开。
    """
    from daily_review.config import get_settings

    path = get_settings().output_dir / f"{trade_date}_看板.html"
    try:
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8")
        if not _file_matches_request(text, trade_date, n_days, no_llm):
            return None
        if not _dashboard_cache_is_fresh(trade_date, path.stat().st_mtime):
            return None
        return text
    except OSError:
        return None


def _generate_dashboard_html(trade_date: str, n_days: int, no_llm: bool) -> str:
    """联网采集→指标→渲染看板 HTML（慢；仅在缓存/文件均未命中时调用）。

    默认 10 日窗口成功生成后落盘 output/{date}_看板.html，进程重启后文件复用秒开
    （非默认窗口不落盘，避免覆盖默认命名文件；内容核对在复用侧兜底）。
    """
    from daily_review.config import get_settings
    from daily_review.dashboard import DEFAULT_N_DAYS, _assemble_payload, _dashboard_interpretation, build_trend, render_html
    from daily_review.pipeline import collect, compute

    collected = collect(trade_date, n_days=n_days)
    indicators = compute(collected)
    trend = build_trend(collected, indicators, n_days)
    payload = _assemble_payload(indicators, trend, collected)
    llm_text = "" if no_llm else _dashboard_interpretation(indicators, trend)
    html = render_html(payload, llm_text)
    if n_days == DEFAULT_N_DAYS:
        try:
            out = get_settings().output_dir / f"{trade_date}_看板.html"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(html, encoding="utf-8")
        except OSError:
            pass
    return html


@api_bp.get("/api/dashboard/view")
def api_dashboard_view():
    """数据看板 iframe 内容。缓存/文件秒开；首次联网生成慢；失败给自包含错误页（不裸 500）。"""
    trade_date = request.args.get("date", "").strip() or _recent_date()
    if not _DATE_RE.fullmatch(trade_date):
        return jsonify({"error": "date 需为 YYYYMMDD"}), 400
    try:
        n_days = int(request.args.get("days", "10") or "10")
    except ValueError:
        n_days = 10
    n_days = max(2, min(n_days, 60))
    no_llm = request.args.get("no_llm", "1") not in ("0", "false", "")

    cache: DashboardCache = current_app.extensions["dashboard_cache"]
    key = (trade_date, n_days, no_llm)

    html = cache.get(key, trade_date)
    if html is None:
        html = _serve_existing_dashboard_file(trade_date, n_days, no_llm)
    if html is None:
        with _generation_lock(key):  # 单飞：并发同参只生成一次，第二个等锁后命中缓存
            html = cache.get(key, trade_date)
            if html is None:
                html = _serve_existing_dashboard_file(trade_date, n_days, no_llm)
            if html is None:
                try:
                    html = _generate_dashboard_html(trade_date, n_days, no_llm)
                    cache.set(key, html)  # 成功才缓存；失败不缓存，下次请求重试
                except Exception as exc:  # noqa: BLE001 —— 看板兜底：错误页进 iframe，不裸 500
                    from daily_review.dashboard import render_error_html

                    html = render_error_html(trade_date, f"{type(exc).__name__}: {exc}")
    return current_app.response_class(html, mimetype="text/html")


@api_bp.get("/api/config/llm")
def api_config_llm():
    """前端默认勾选「LLM 多日解读」用：配置了 DEEPSEEK_API_KEY 才默认开。"""
    from daily_review.config import get_settings

    return jsonify({"configured": bool(get_settings().llm_api_key)})


# ---------------------------------------------------------------- 审计日志 API（v0.35）


def _get_audit_db():
    """获取进程内共享的 AuditDB 实例（从 app 扩展取）。"""
    from flask import current_app

    return current_app.extensions.get("audit_db")


@api_bp.get("/api/audit/messages")
def api_audit_messages():
    """消息记录列表。参数：chat_id（可选），limit（缺省 50）。"""
    audit_db = _get_audit_db()
    if audit_db is None:
        return jsonify({"error": "审计日志未启用"}), 400
    chat_id = request.args.get("chat_id", "").strip() or None
    limit = min(int(request.args.get("limit", "50")), 200)
    if chat_id:
        messages = audit_db.recent_messages(chat_id, limit=limit)
    else:
        messages = audit_db.recent_messages("", limit=limit) if hasattr(audit_db, 'recent_messages') else []
    return jsonify({"messages": messages, "count": len(messages)})


@api_bp.get("/api/audit/anomalies")
def api_audit_anomalies():
    """异常事件列表。参数：limit（缺省 50）。"""
    audit_db = _get_audit_db()
    if audit_db is None:
        return jsonify({"error": "审计日志未启用"}), 400
    limit = min(int(request.args.get("limit", "50")), 200)
    anomalies = audit_db.recent_anomalies(limit=limit)
    return jsonify({"anomalies": anomalies, "count": len(anomalies)})


@api_bp.get("/api/audit/errors")
def api_audit_errors():
    """错误日志列表。参数：limit（缺省 50）。"""
    audit_db = _get_audit_db()
    if audit_db is None:
        return jsonify({"error": "审计日志未启用"}), 400
    limit = min(int(request.args.get("limit", "50")), 200)
    errors = audit_db.recent_errors(limit=limit)
    return jsonify({"errors": errors, "count": len(errors)})


@api_bp.get("/api/audit/traces")
def api_audit_traces():
    """工具调用链列表。参数：limit（缺省 50）。"""
    audit_db = _get_audit_db()
    if audit_db is None:
        return jsonify({"error": "审计日志未启用"}), 400
    limit = min(int(request.args.get("limit", "50")), 200)
    traces = audit_db.recent_traces(limit=limit)
    return jsonify({"traces": traces, "count": len(traces)})


@api_bp.get("/api/audit/chat-ids")
def api_audit_chat_ids():
    """有消息记录的 chat_id 列表。"""
    audit_db = _get_audit_db()
    if audit_db is None:
        return jsonify({"error": "审计日志未启用"}), 400
    chat_ids = audit_db.list_chat_ids()
    return jsonify({"chat_ids": chat_ids})
