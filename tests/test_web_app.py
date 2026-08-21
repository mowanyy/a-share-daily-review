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


# ---------------------------------------------------------------- 数据看板：缓存 / 文件复用 / 错误兜底


def test_dashboard_view_cached_second_call_skips_generation(app, monkeypatch):
    """首次生成（慢）→ 进程内缓存 → 第二次同参请求秒开，不重复联网采集。"""
    import daily_review.web.routes as routes_mod

    calls = {"n": 0}

    def fake_gen(trade_date, n_days, no_llm):
        calls["n"] += 1
        return f"<html>dash-{trade_date}-{n_days}</html>"

    monkeypatch.setattr(routes_mod, "_generate_dashboard_html", fake_gen)
    # 历史日期定稿 → 缓存不失效（不依赖当前时钟）
    c = app.test_client()
    r1 = c.get("/api/dashboard/view?date=20260730&days=10&no_llm=1")
    assert r1.status_code == 200
    r2 = c.get("/api/dashboard/view?date=20260730&days=10&no_llm=1")
    assert r2.status_code == 200
    assert calls["n"] == 1, "第二次请求不应重新生成"
    assert r2.get_data(as_text=True) == r1.get_data(as_text=True)


def _dash_file(tmp_path, *, n_days=10, no_llm=True, body="<html>file-dash</html>"):
    """写一个与请求可匹配的 output/{date}_看板.html：含 n_days 标记；no_llm 文件带「未生成解读」。"""
    from daily_review.config import get_settings

    s = get_settings()
    od = tmp_path / "output"
    od.mkdir(exist_ok=True)
    marker = "（未生成解读）" if no_llm else "<div>LLM 解读内容</div>"
    (od / "20260730_看板.html").write_text(
        f'const DATA = {{ "n_days": {n_days} }}; {marker}{body}', encoding="utf-8"
    )
    return od


def test_dashboard_view_serves_existing_file_without_collect(app, monkeypatch, tmp_path):
    """CLI/启动器已生成 output/{date}_看板.html 且参数匹配 → web 直接复用，秒开、不联网。"""
    from daily_review.config import get_settings
    import daily_review.web.routes as routes_mod

    s = get_settings()
    od = _dash_file(tmp_path, n_days=10, no_llm=True)
    monkeypatch.setattr(s, "output_dir", od)

    def fake_gen(trade_date, n_days, no_llm):
        raise AssertionError("有已生成文件时不应触发联网重新生成")

    monkeypatch.setattr(routes_mod, "_generate_dashboard_html", fake_gen)
    c = app.test_client()
    r = c.get("/api/dashboard/view?date=20260730&days=10&no_llm=1")
    assert r.status_code == 200
    assert "<html>file-dash</html>" in r.get_data(as_text=True)


def test_dashboard_view_file_reuse_only_default_days(app, monkeypatch, tmp_path):
    """文件 n_days=10，请求 days=20 → 数据窗口不匹配，不复用文件，走重新生成。"""
    from daily_review.config import get_settings
    import daily_review.web.routes as routes_mod

    s = get_settings()
    od = _dash_file(tmp_path, n_days=10, no_llm=True)
    monkeypatch.setattr(s, "output_dir", od)
    monkeypatch.setattr(routes_mod, "_generate_dashboard_html",
                        lambda d, n, no_llm: f"<html>gen-{n}</html>")
    r = app.test_client().get("/api/dashboard/view?date=20260730&days=20&no_llm=1")
    assert r.status_code == 200
    assert "gen-20" in r.get_data(as_text=True)


def test_dashboard_view_file_reuse_no_llm_mismatch_not_served(app, monkeypatch, tmp_path):
    """LLM 开关与文件不一致 → 不复用文件（用户要解读文件却无 / 用户不要解读文件却有）。"""
    from daily_review.config import get_settings
    import daily_review.web.routes as routes_mod

    s = get_settings()
    monkeypatch.setattr(s, "output_dir", _dash_file(tmp_path, n_days=10, no_llm=True))
    calls = {"n": 0}
    monkeypatch.setattr(routes_mod, "_generate_dashboard_html",
                        lambda d, n, no_llm: (calls.__setitem__("n", calls["n"] + 1), "<html>gen</html>")[1])
    # 用户要解读（no_llm=0），文件却是「未生成解读」→ 不复用
    r = app.test_client().get("/api/dashboard/view?date=20260730&days=10&no_llm=0")
    assert r.status_code == 200 and "gen" in r.get_data(as_text=True) and calls["n"] == 1


def test_file_matches_request():
    """文件内容核对：n_days 与 LLM 开关都须与请求一致。"""
    import daily_review.web.routes as routes_mod

    no_llm_file = 'const DATA = { "n_days": 10 }; （未生成解读）'
    llm_file = 'const DATA = { "n_days": 10 }; <div>近5日温度回升</div>'
    # n_days 匹配 + 无解读文件 → 只服务 no_llm=True 请求
    assert routes_mod._file_matches_request(no_llm_file, "20260730", 10, True)
    assert not routes_mod._file_matches_request(no_llm_file, "20260730", 10, False)
    # 带解读文件 → 只服务 no_llm=False 请求
    assert routes_mod._file_matches_request(llm_file, "20260730", 10, False)
    assert not routes_mod._file_matches_request(llm_file, "20260730", 10, True)
    # 天数不匹配 / 无 n_days 标记 → 不复用
    assert not routes_mod._file_matches_request(no_llm_file, "20260730", 20, True)
    assert not routes_mod._file_matches_request("<html>无标记</html>", "20260730", 10, True)


def test_generation_lock_same_key_same_lock():
    """单飞：同 (date, days, no_llm) 并发请求拿到同一把锁；不同 key 不同锁。"""
    import daily_review.web.routes as routes_mod

    k1 = ("20260730", 10, True)
    k2 = ("20260730", 10, False)
    assert routes_mod._generation_lock(k1) is routes_mod._generation_lock(k1)
    assert routes_mod._generation_lock(k1) is not routes_mod._generation_lock(k2)


def test_dashboard_view_error_falls_back_clean_page(app, monkeypatch):
    """联网采集/指标失败 → 自包含错误页进 iframe（HTTP 200），不裸 500。"""
    import daily_review.web.routes as routes_mod

    def boom(trade_date, n_days, no_llm):
        raise RuntimeError("collect failed")

    monkeypatch.setattr(routes_mod, "_generate_dashboard_html", boom)
    monkeypatch.setattr(routes_mod, "_serve_existing_dashboard_file", lambda *a: None)
    c = app.test_client()
    r = c.get("/api/dashboard/view?date=20260730&days=10&no_llm=1")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "数据看板生成失败" in body
    assert "collect failed" in body
    # 错误不缓存：下次请求重试，而非一直显示错误
    r2 = c.get("/api/dashboard/view?date=20260730&days=10&no_llm=1")
    assert "数据看板生成失败" in r2.get_data(as_text=True)


def test_dashboard_view_invalid_date(app):
    r = app.test_client().get("/api/dashboard/view?date=2026-07-30")
    assert r.status_code == 400


def test_config_llm_endpoint(app, monkeypatch):
    from daily_review.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "llm_api_key", "sk-xxx")
    assert app.test_client().get("/api/config/llm").get_json()["configured"] is True
    monkeypatch.setattr(s, "llm_api_key", "")
    assert app.test_client().get("/api/config/llm").get_json()["configured"] is False


def test_dashboard_cache_freshness_rules(monkeypatch):
    """缓存/文件有效期：历史日期定稿；今日盘中 10 分钟；今日 18:00（龙虎榜齐）后须 18:00 后生成。"""
    import datetime

    import daily_review.web.routes as routes_mod

    intraday = datetime.datetime(2026, 8, 11, 14, 0)
    monkeypatch.setattr(routes_mod, "_clock", lambda: intraday)
    # 历史日期：永不失效
    assert routes_mod._dashboard_cache_is_fresh("20260730", 0.0) is True
    # 今日盘中：10 分钟内有效
    assert routes_mod._dashboard_cache_is_fresh("20260811", intraday.timestamp() - 100) is True
    assert routes_mod._dashboard_cache_is_fresh("20260811", intraday.timestamp() - 700) is False
    # 定稿边界 18:00：18:00 前仍是盘中 TTL（15:01 生成、15:30 看已超 10 分钟 → 过期）
    mid = datetime.datetime(2026, 8, 11, 15, 30)
    monkeypatch.setattr(routes_mod, "_clock", lambda: mid)
    assert routes_mod._dashboard_cache_is_fresh("20260811",
                                                datetime.datetime(2026, 8, 11, 15, 1).timestamp()) is False
    # 18:00 后：18:00 前生成的盘中快照过期（龙虎榜空），18:00 后生成的有效
    after = datetime.datetime(2026, 8, 11, 18, 30)
    monkeypatch.setattr(routes_mod, "_clock", lambda: after)
    assert routes_mod._dashboard_cache_is_fresh("20260811",
                                                datetime.datetime(2026, 8, 11, 16, 0).timestamp()) is False
    assert routes_mod._dashboard_cache_is_fresh("20260811",
                                                datetime.datetime(2026, 8, 11, 18, 1).timestamp()) is True


def test_dashboard_cache_evicts_oldest(monkeypatch):
    import datetime

    import daily_review.web.routes as routes_mod
    from daily_review.web.routes import DashboardCache

    base = datetime.datetime(2026, 8, 11, 10, 0)
    monkeypatch.setattr(routes_mod, "_clock", lambda: base)
    c = DashboardCache()
    for i in range(20):
        c.set(("20260730", i, True), f"h{i}")
    # 最多保留 MAX=16 条，逐出最旧
    assert c.get(("20260730", 0, True), "20260730") is None
    assert c.get(("20260730", 19, True), "20260730") == "h19"


# ---------------------------------------------------------------- 审计日志页面（v0.35）


def test_audit_page_renders(app):
    """审计日志页面可渲染，零外链。"""
    r = app.test_client().get("/audit")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Agent 日志" in html
    for bad in ("http://", "https://", "<script src", "<link"):
        assert bad not in html, f"audit page contains {bad}"


def test_audit_messages_api(app, tmp_path, monkeypatch):
    """审计消息 API 返回数据，不报错。"""
    from daily_review.web.audit import AuditDB

    db = AuditDB(db_path=tmp_path / "test_audit.db")
    db.log_message("chat_1", "user", "test question")
    db.log_message("chat_1", "assistant", "test answer")
    monkeypatch.setitem(app.extensions, "audit_db", db)
    c = app.test_client()
    r = c.get("/api/audit/messages")
    assert r.status_code == 200
    data = r.get_json()
    assert data["count"] == 2
    assert len(data["messages"]) == 2
    assert data["messages"][0]["role"] == "assistant"


def test_audit_anomalies_api(app, tmp_path, monkeypatch):
    """审计异常 API 返回数据。"""
    from daily_review.web.audit import AuditDB

    db = AuditDB(db_path=tmp_path / "test_audit.db")
    db.log_anomaly("炸板潮", "warning", "5只炸板", ["A", "B"])
    monkeypatch.setitem(app.extensions, "audit_db", db)
    c = app.test_client()
    r = c.get("/api/audit/anomalies")
    assert r.status_code == 200
    data = r.get_json()
    assert data["count"] == 1
    assert data["anomalies"][0]["type"] == "炸板潮"


def test_audit_traces_api(app, tmp_path, monkeypatch):
    """审计 trace API 返回数据。"""
    from daily_review.web.audit import AuditDB

    db = AuditDB(db_path=tmp_path / "test_audit.db")
    db.log_trace("web", "分析今日市场", '{"tool_calls":[],"total_rounds":0}')
    monkeypatch.setitem(app.extensions, "audit_db", db)
    c = app.test_client()
    r = c.get("/api/audit/traces")
    assert r.status_code == 200
    data = r.get_json()
    assert data["count"] == 1
    assert "分析今日市场" in data["traces"][0]["question"]


def test_audit_chat_ids_api(app, tmp_path, monkeypatch):
    """审计 chat_ids API 返回有消息记录的聊天 ID。"""
    from daily_review.web.audit import AuditDB

    db = AuditDB(db_path=tmp_path / "test_audit.db")
    db.log_message("chat_a", "user", "hi")
    db.log_message("chat_b", "user", "hello")
    monkeypatch.setitem(app.extensions, "audit_db", db)
    c = app.test_client()
    r = c.get("/api/audit/chat-ids")
    assert r.status_code == 200
    data = r.get_json()
    assert sorted(data["chat_ids"]) == ["chat_a", "chat_b"]
