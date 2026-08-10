"""web/app.py + routes 测试：页面 / 零外链 / 战法 CRUD / 复盘任务 / 问答（全离线）。"""

from __future__ import annotations

import time

import pytest

TRACKED_MD = """---
id: strategy.template
name: 战法模板
role: strategy
status: draft
version: 0.1.0
---

## 1. 概述
模板示例
"""


@pytest.fixture
def app(tmp_path, monkeypatch):
    """注入 tmp 目录为 data/prompts 根（含一个 tracked 战法），新建独立 Flask app。"""
    from daily_review.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "data_dir", tmp_path / "data")
    monkeypatch.setattr(s, "prompts_dir", tmp_path / "prompts")
    tdir = tmp_path / "prompts" / "strategies"
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "战法模板.md").write_text(TRACKED_MD, encoding="utf-8")

    from daily_review.web.app import create_app

    app = create_app()
    app.config["TESTING"] = True
    return app


def test_pages_render_and_zero_cdn(app):
    c = app.test_client()
    for path in ["/", "/strategies", "/review", "/qa", "/dashboard"]:
        r = c.get(path)
        assert r.status_code == 200, f"{path} -> {r.status_code}"
        html = r.get_data(as_text=True)
        for bad in ("http://", "https://", "<script src", "<link"):
            assert bad not in html, (path, bad)


def test_strategy_crud_flow(app):
    c = app.test_client()
    body = "## 1. 概述\n正文"
    r = c.post("/api/strategies", json={"name": "低吸", "markdown": body})
    assert r.status_code == 201, r.get_data(as_text=True)
    s = r.get_json()
    assert s["id"].startswith("strategy.user-")
    sid = s["id"]

    r = c.get("/api/strategies")
    assert sid in [x["id"] for x in r.get_json()["strategies"]]

    r = c.get(f"/api/strategies/{sid}")
    assert r.status_code == 200 and r.get_json()["body"] == body

    r = c.put(f"/api/strategies/{sid}", json={"name": "低吸", "markdown": body + "\n补充"})
    assert r.status_code == 200 and "补充" in r.get_json()["body"]

    r = c.post(f"/api/strategies/{sid}/status", json={"status": "active"})
    assert r.status_code == 200 and r.get_json()["status"] == "active"

    r = c.delete(f"/api/strategies/{sid}")
    assert r.status_code == 200
    assert c.get(f"/api/strategies/{sid}").status_code == 404


def test_strategy_validation_missing_name(app):
    c = app.test_client()
    r = c.post("/api/strategies", json={"markdown": "## 1. 概述\nx"})
    assert r.status_code == 400
    r = c.post("/api/strategies", json={"name": "空正文"})
    assert r.status_code == 400


def test_tracked_strategy_readonly_via_api(app):
    c = app.test_client()
    r = c.delete("/api/strategies/strategy.template")
    assert r.status_code == 403
    r = c.post("/api/strategies/strategy.template/status", json={"status": "active"})
    assert r.status_code == 403
    r = c.put(
        "/api/strategies/strategy.template",
        json={"name": "改", "markdown": "## 1. 概述\nx"},
    )
    assert r.status_code == 403


def test_review_recent_date(app, monkeypatch):
    from daily_review.data import eastmoney_pool

    monkeypatch.setattr(
        eastmoney_pool, "resolve_recent_trade_dates", lambda today, n_days=1: ["20260806"]
    )
    r = app.test_client().get("/api/review/recent_date")
    assert r.get_json()["date"] == "20260806"


def test_review_start_and_status(app, monkeypatch):
    import daily_review.llm.reporter as reporter_mod
    import daily_review.pipeline as pipeline_mod

    monkeypatch.setattr(pipeline_mod, "collect", lambda d, n_days=10: {"date": d})
    monkeypatch.setattr(pipeline_mod, "compute", lambda c: {"emotion": {"available": False}})
    monkeypatch.setattr(
        reporter_mod,
        "generate_report",
        lambda ind, d, **kw: "## 七、次日预案\n明日计划",
    )
    c = app.test_client()
    r = c.post("/api/review/start", json={"trade_date": "20260806"})
    assert r.status_code == 202
    jid = r.get_json()["job_id"]
    j = {}
    for _ in range(100):
        j = c.get(f"/api/review/status/{jid}").get_json()
        if j["status"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert j["status"] == "done", j
    assert "明日计划" in j["plan_html"]
    assert j["report_html"]


def test_review_start_invalid_date(app):
    r = app.test_client().post("/api/review/start", json={"trade_date": "2026-08-06"})
    assert r.status_code == 400


def test_qa_ask(app, monkeypatch, kb_root):
    import daily_review.kb.qa as qa_mod
    import daily_review.web.routes as routes_mod
    from daily_review.kb.index import KnowledgeIndex

    idx = KnowledgeIndex(kb_root, use_embedding=False)
    idx.ensure_ready(force=True)
    monkeypatch.setattr(routes_mod, "_get_index", lambda: idx)

    def fake_chat_tools(messages, **kw):
        class R:
            content = "炸板率=炸板家数/（涨停+炸板）"
            tool_calls = []
            raw_tool_calls = None
            reasoning_content = None

        return R()

    monkeypatch.setattr(qa_mod, "chat_tools", fake_chat_tools)
    r = app.test_client().post("/api/qa/ask", json={"question": "什么是炸板率？"})
    assert r.status_code == 200, r.get_data(as_text=True)
    data = r.get_json()
    assert "炸板率" in data["answer"]
    assert data["answer_html"]
    assert any("术语表.md" in s["source_rel"] for s in data["sources"])


def test_qa_ask_empty_question(app):
    r = app.test_client().post("/api/qa/ask", json={"question": "   "})
    assert r.status_code == 400
