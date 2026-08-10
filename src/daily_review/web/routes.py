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
        job = jobs.start(trade_date=trade_date, strategy_id=strategy_id, no_llm=no_llm)
    except JobBusy as exc:
        return jsonify({"error": str(exc)}), 409
    return jsonify({"job_id": job.id}), 202


@api_bp.get("/api/review/status/<job_id>")
def api_review_status(job_id: str):
    job = current_app.extensions["jobs"].status(job_id)
    if job is None:
        return jsonify({"error": "任务不存在"}), 404
    return jsonify(job.to_dict())


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
    return jsonify(
        {
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
    )


# ---------------------------------------------------------------- 数据看板 API


@api_bp.get("/api/dashboard/view")
def api_dashboard_view():
    from daily_review.dashboard import _assemble_payload, _dashboard_interpretation, build_trend, render_html
    from daily_review.pipeline import collect, compute

    trade_date = request.args.get("date", "").strip() or _recent_date()
    if not _DATE_RE.fullmatch(trade_date):
        return jsonify({"error": "date 需为 YYYYMMDD"}), 400
    try:
        n_days = int(request.args.get("days", "10") or "10")
    except ValueError:
        n_days = 10
    n_days = max(2, min(n_days, 60))
    no_llm = request.args.get("no_llm", "1") not in ("0", "false", "")

    collected = collect(trade_date, n_days=n_days)
    indicators = compute(collected)
    trend = build_trend(collected, indicators, n_days)
    payload = _assemble_payload(indicators, trend, collected)
    llm_text = ""
    if not no_llm:
        llm_text = _dashboard_interpretation(indicators, trend)
    html_text = render_html(payload, llm_text)
    return current_app.response_class(html_text, mimetype="text/html")
