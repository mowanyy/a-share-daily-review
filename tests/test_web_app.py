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
    """注入 tmp 目录为 data/prompts/output 根（含一个 tracked 战法），新建独立 Flask app。"""
    from daily_review.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "data_dir", tmp_path / "data")
    monkeypatch.setattr(s, "prompts_dir", tmp_path / "prompts")
    monkeypatch.setattr(s, "output_dir", tmp_path / "output")
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
    import daily_review.dashboard as dash_mod

    monkeypatch.setattr(pipeline_mod, "collect", lambda d, n_days=10: {"date": d})
    monkeypatch.setattr(pipeline_mod, "compute", lambda c: {"emotion": {"available": False}})
    monkeypatch.setattr(dash_mod, "try_pregenerate_dashboard", lambda d, **kw: True)
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

    def fake_gen(trade_date, n_days):
        calls["n"] += 1
        return f"<html>dash-{trade_date}-{n_days}</html>"

    monkeypatch.setattr(routes_mod, "_generate_dashboard_html", fake_gen)
    c = app.test_client()
    r1 = c.get("/api/dashboard/view?date=20260730&days=10")
    assert r1.status_code == 200
    r2 = c.get("/api/dashboard/view?date=20260730&days=10")
    assert r2.status_code == 200
    assert calls["n"] == 1, "第二次请求不应重新生成"
    assert r2.get_data(as_text=True) == r1.get_data(as_text=True)


def _dash_file(tmp_path, *, n_days=10, body="<html>file-dash</html>"):
    """写一个与请求可匹配的 output/{date}_看板.html：含 n_days + dash_ver 标记。"""
    from daily_review.config import get_settings

    s = get_settings()
    od = tmp_path / "output"
    od.mkdir(exist_ok=True)
    (od / "20260730_看板.html").write_text(
        f'const DATA = {{ "n_days": {n_days}, "dash_ver": 2 }}; {body}', encoding="utf-8"
    )
    return od


def test_dashboard_view_serves_existing_file_without_collect(app, monkeypatch, tmp_path):
    """CLI/复盘已预写 output/{date}_看板.html 且参数匹配 → web 直接复用，秒开、不联网。"""
    from daily_review.config import get_settings
    import daily_review.web.routes as routes_mod

    s = get_settings()
    od = _dash_file(tmp_path, n_days=10)
    monkeypatch.setattr(s, "output_dir", od)

    def fake_gen(trade_date, n_days):
        raise AssertionError("有已生成文件时不应触发联网重新生成")

    monkeypatch.setattr(routes_mod, "_generate_dashboard_html", fake_gen)
    c = app.test_client()
    r = c.get("/api/dashboard/view?date=20260730&days=10")
    assert r.status_code == 200
    assert "<html>file-dash</html>" in r.get_data(as_text=True)


def test_dashboard_view_file_reuse_only_default_days(app, monkeypatch, tmp_path):
    """文件 n_days=10，请求 days=20 → 数据窗口不匹配，不复用文件，走重新生成。"""
    from daily_review.config import get_settings
    import daily_review.web.routes as routes_mod

    s = get_settings()
    od = _dash_file(tmp_path, n_days=10)
    monkeypatch.setattr(s, "output_dir", od)
    monkeypatch.setattr(routes_mod, "_generate_dashboard_html",
                        lambda d, n: f"<html>gen-{n}</html>")
    r = app.test_client().get("/api/dashboard/view?date=20260730&days=20")
    assert r.status_code == 200
    assert "gen-20" in r.get_data(as_text=True)


def test_file_matches_request():
    """文件内容核对：n_days 匹配 + 结构版本（dash_ver）>= 2；旧版文件不复用。"""
    import daily_review.web.routes as routes_mod

    ok = 'const DATA = { "n_days": 10, "dash_ver": 2 }; <html>ok</html>'
    assert routes_mod._file_matches_request(ok, "20260730", 10)
    assert not routes_mod._file_matches_request(ok, "20260730", 20)
    # 旧版文件（无 dash_ver 标记）即使 n_days 匹配也不复用 → 触发重新生成新版
    old = 'const DATA = { "n_days": 10 }; <html>old</html>'
    assert not routes_mod._file_matches_request(old, "20260730", 10)
    assert not routes_mod._file_matches_request("<html>无标记</html>", "20260730", 10)


def test_generation_lock_same_key_same_lock():
    """单飞：同 (date, days) 并发请求拿到同一把锁；不同 key 不同锁。"""
    import daily_review.web.routes as routes_mod

    k1 = ("20260730", 10)
    k2 = ("20260730", 20)
    assert routes_mod._generation_lock(k1) is routes_mod._generation_lock(k1)
    assert routes_mod._generation_lock(k1) is not routes_mod._generation_lock(k2)


def test_dashboard_view_error_falls_back_clean_page(app, monkeypatch):
    """联网采集/指标失败 → 自包含错误页进 iframe（HTTP 200），不裸 500。"""
    import daily_review.web.routes as routes_mod

    def boom(trade_date, n_days):
        raise RuntimeError("collect failed")

    monkeypatch.setattr(routes_mod, "_generate_dashboard_html", boom)
    monkeypatch.setattr(routes_mod, "_serve_existing_dashboard_file", lambda *a: None)
    c = app.test_client()
    r = c.get("/api/dashboard/view?date=20260730&days=10")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "数据看板加载失败" in body
    assert "collect failed" in body
    r2 = c.get("/api/dashboard/view?date=20260730&days=10")
    assert "数据看板加载失败" in r2.get_data(as_text=True)


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
    """缓存/文件有效期：历史日期定稿；今日盘中 10 分钟；今日 15:00 后须 15:00 后生成。"""
    import datetime

    import daily_review.web.routes as routes_mod

    intraday = datetime.datetime(2026, 8, 11, 14, 0)
    monkeypatch.setattr(routes_mod, "_clock", lambda: intraday)
    assert routes_mod._dashboard_cache_is_fresh("20260730", 0.0) is True
    assert routes_mod._dashboard_cache_is_fresh("20260811", intraday.timestamp() - 100) is True
    assert routes_mod._dashboard_cache_is_fresh("20260811", intraday.timestamp() - 700) is False
    # 15:00 后：15:00 前生成的盘中快照过期，15:00 后生成的有效
    after = datetime.datetime(2026, 8, 11, 15, 30)
    monkeypatch.setattr(routes_mod, "_clock", lambda: after)
    assert routes_mod._dashboard_cache_is_fresh("20260811",
                                                datetime.datetime(2026, 8, 11, 14, 50).timestamp()) is False
    assert routes_mod._dashboard_cache_is_fresh("20260811",
                                                datetime.datetime(2026, 8, 11, 15, 1).timestamp()) is True


def test_dashboard_cache_evicts_oldest(monkeypatch):
    import datetime

    import daily_review.web.routes as routes_mod
    from daily_review.web.routes import DashboardCache

    base = datetime.datetime(2026, 8, 11, 10, 0)
    monkeypatch.setattr(routes_mod, "_clock", lambda: base)
    c = DashboardCache()
    for i in range(20):
        c.set(("20260730", i), f"h{i}")
    assert c.get(("20260730", 0), "20260730") is None
    assert c.get(("20260730", 19), "20260730") == "h19"


def test_dashboard_refresh_invalidates_cache(app, monkeypatch):
    """强制刷新：清进程内缓存后下次请求重新生成（重新采集/读盘）。"""
    import daily_review.web.routes as routes_mod

    calls = {"n": 0}

    def fake_gen(trade_date, n_days):
        calls["n"] += 1
        return f"<html>dash-{calls['n']}</html>"

    monkeypatch.setattr(routes_mod, "_generate_dashboard_html", fake_gen)
    c = app.test_client()
    assert "dash-1" in c.get("/api/dashboard/view?date=20260730&days=10").get_data(as_text=True)
    assert calls["n"] == 1
    r = c.post("/api/dashboard/refresh", json={"date": "20260730", "days": 10})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert "dash-2" in c.get("/api/dashboard/view?date=20260730&days=10").get_data(as_text=True)
    assert calls["n"] == 2
    # 非默认窗口同样失效
    assert "dash-3" in c.get("/api/dashboard/view?date=20260730&days=20").get_data(as_text=True)


def test_dashboard_refresh_invalid_date(app):
    r = app.test_client().post("/api/dashboard/refresh", json={"date": "2026-07-30"})
    assert r.status_code == 400


def test_recent_date_time_aware(monkeypatch):
    """_recent_date 时间感知：今日开盘前/无涨停数据 → 回退前一交易日；有数据 → 今日。"""
    import daily_review.web.routes as routes_mod
    from daily_review.data import eastmoney_pool

    # 场景 A：今日是交易日但无 zt 数据（开盘前）→ 前一交易日
    monkeypatch.setattr(
        eastmoney_pool, "resolve_recent_trade_dates",
        lambda today, n_days=1: (["20260911", "20260910"] if n_days > 1 else ["20260911"]),
    )
    monkeypatch.setattr(routes_mod, "_has_zt_data", lambda d: False)
    assert routes_mod._recent_date() == "20260910"

    # 场景 B：今日已有涨停数据（已收盘）→ 今日
    monkeypatch.setattr(routes_mod, "_has_zt_data", lambda d: True)
    assert routes_mod._recent_date() == "20260911"

    # 场景 C：今日非交易日（resolve 只给最近交易日）→ 该最近交易日
    monkeypatch.setattr(eastmoney_pool, "resolve_recent_trade_dates", lambda today, n_days=1: ["20260910"])
    assert routes_mod._recent_date() == "20260910"

    # 场景 D：完全无法判定 → 退化为 today
    monkeypatch.setattr(eastmoney_pool, "resolve_recent_trade_dates", lambda today, n_days=1: [])
    assert routes_mod._recent_date() == "20260911"


def test_dashboard_dates_api(app, monkeypatch):
    """/api/dashboard/dates：返回最近交易日列表 + 时间感知默认日期。"""
    import daily_review.web.routes as routes_mod
    from daily_review.data import eastmoney_pool

    monkeypatch.setattr(
        eastmoney_pool, "resolve_recent_trade_dates",
        lambda today, n_days=1: ["20260911", "20260910", "20260909"][:n_days],
    )
    monkeypatch.setattr(routes_mod, "_has_zt_data", lambda d: False)
    r = app.test_client().get("/api/dashboard/dates")
    assert r.status_code == 200
    data = r.get_json()
    assert data["dates"] == ["20260911", "20260910", "20260909"]
    assert data["default"] == "20260910"  # 今日开盘前 → 前一交易日
    assert data["today"] == "20260911"


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


# ---------------------------------------------------------------- v0.36.2 安全加固


def test_audit_api_limit_guards_bad_input(app, tmp_path, monkeypatch):
    """limit 参数非数字/负数/超限均被钳制，不再 500 或变成无界查询。"""
    from daily_review.web.audit import AuditDB

    db = AuditDB(db_path=tmp_path / "test_audit.db")
    for i in range(3):
        db.log_message("chat_1", "user", f"q{i}")
    monkeypatch.setitem(app.extensions, "audit_db", db)
    c = app.test_client()
    assert c.get("/api/audit/messages?limit=abc").status_code == 200
    assert c.get("/api/audit/messages?limit=-5").status_code == 200
    assert c.get("/api/audit/messages?limit=999999").status_code == 200
    assert len(c.get("/api/audit/messages?limit=abc").get_json()["messages"]) == 3
    assert len(c.get("/api/audit/messages?limit=-5").get_json()["messages"]) == 1  # 负数→1


def test_host_header_guard(app):
    """非回环 Host 头被 400 拒绝（防 DNS rebinding）。"""
    c = app.test_client()
    assert c.get("/", headers={"Host": "evil.example.com"}).status_code == 400
    assert c.get("/", headers={"Host": "127.0.0.1:5000"}).status_code == 200
    assert c.get("/", headers={"Host": "localhost"}).status_code == 200
    assert c.get("/", headers={"Host": "[::1]:5000"}).status_code == 200


def test_app_has_secret_key(app):
    """SECRET_KEY 已配置（默认进程内随机，可用环境变量覆盖）。"""
    assert app.secret_key and len(app.secret_key) >= 16


# ---------------------------------------------------------------- v0.36.3 安全加固：跨源 / 限流 / 合规


def test_cross_origin_guard(app):
    """恶意跨源 Origin/Referer 被 403；本机同源 Origin / 无头请求（curl/脚本）放行。"""
    c = app.test_client()
    # 恶意网页跨源 POST（Origin 为攻击站点）→ 403，handler 不执行
    r = c.post(
        "/api/strategies",
        json={"name": "evil", "markdown": "## 1\nx"},
        headers={"Origin": "https://evil.example.com"},
    )
    assert r.status_code == 403
    # Referer 同样校验
    r = c.post(
        "/api/strategies",
        json={"name": "evil", "markdown": "## 1\nx"},
        headers={"Referer": "https://evil.example.com/page"},
    )
    assert r.status_code == 403
    # 本机同源 Origin 放行
    r = c.post(
        "/api/strategies",
        json={"name": "同源", "markdown": "## 1\nx"},
        headers={"Origin": "http://127.0.0.1:5000"},
    )
    assert r.status_code == 201, r.get_data(as_text=True)
    # 无 Origin/Referer（curl / 本地脚本）放行——不破坏非浏览器调用
    r = c.post("/api/strategies", json={"name": "无头", "markdown": "## 1\nx"})
    assert r.status_code == 201
    # GET 带本机 Referer（页面自身请求）放行
    assert c.get("/", headers={"Referer": "http://localhost:5000/"}).status_code == 200


def test_llm_rate_limit_returns_429(app, monkeypatch):
    """LLM 端点窗口内超限 → 429（防本机脚本刷爆 DeepSeek 额度）；clear_limits 后恢复。"""
    import daily_review.web.ratelimit as rl_mod
    import daily_review.web.routes as routes_mod
    from daily_review.web.ratelimit import clear_limits

    # 注入限额=2 且重建限流器（测试确定性，不依赖环境变量/历史状态）
    monkeypatch.setattr(rl_mod, "default_limit", lambda: 2)
    monkeypatch.setattr(rl_mod, "_LIMITERS", {})

    # 打桩 consult 内部调用（避免真实 LLM/Agent/网络）——handler 内导入 call_agent，
    # 直接 patch agent_registry 模块属性
    import daily_review.web.agent_registry as ar_mod

    monkeypatch.setattr(ar_mod, "call_agent", lambda aid, q: "回答")
    monkeypatch.setattr(routes_mod, "_synthesize_consult", lambda q, r: "综合")

    c = app.test_client()
    payload = {"question": "市场如何？", "agent_ids": ["qa_general"]}
    assert c.post("/api/agents/consult", json=payload).status_code == 200
    assert c.post("/api/agents/consult", json=payload).status_code == 200
    assert c.post("/api/agents/consult", json=payload).status_code == 429
    # 清空限流状态后恢复
    clear_limits()
    assert c.post("/api/agents/consult", json=payload).status_code == 200


def test_ratelimit_sliding_window_unit():
    """滑动窗口限流器单元：窗口内限额、窗口滑动后自动恢复。"""
    from daily_review.web.ratelimit import SlidingWindowLimiter

    lim = SlidingWindowLimiter(limit=3, window_seconds=60)
    assert lim.allow() and lim.allow() and lim.allow()
    assert lim.allow() is False
    assert lim.remaining() == 0
    lim.reset()
    assert lim.allow() is True


def test_qa_compliance_refusal(app):
    """Web QA 命中交易建议关键词 → 合规拒绝话术（含免责声明），不调 LLM/不消耗限流。"""
    r = app.test_client().post("/api/qa/ask", json={"question": "推荐一只股票吧"})
    assert r.status_code == 200
    d = r.get_json()
    assert "无法给出具体建议" in d["answer"]
    assert "不构成任何投资建议" in d["answer"]
    assert d["sources"] == []
    assert d["tool_rounds"] == 0


def test_fund_compliance_refusal(app):
    """基金经理分析命中交易建议关键词 → 合规拒绝（不查经理/不调 LLM）。"""
    r = app.test_client().post(
        "/api/fund/analyze", json={"manager_id": "whatever", "question": "帮我推荐买入"}
    )
    assert r.status_code == 200
    d = r.get_json()
    assert "无法给出具体建议" in d["answer"]
    assert d["history_length"] == 0
    assert d["zhongjun"] == []


def test_consult_compliance_refusal(app):
    """多 Agent 会诊命中交易建议关键词 → 合规拒绝（不调任何 Agent）。"""
    r = app.test_client().post(
        "/api/agents/consult", json={"question": "推荐买入哪只？", "agent_ids": ["qa_general"]}
    )
    assert r.status_code == 200
    d = r.get_json()
    assert "无法给出具体建议" in d["synthesis"]
    assert d["responses"] == {}
