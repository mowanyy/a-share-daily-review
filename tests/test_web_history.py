"""web/history.py 历史报告服务测试：扫描/渲染/404/路径穿越（全离线，注入临时 output 目录）。"""

from __future__ import annotations

import pytest

REVIEW_MD = """# 复盘报告

## 一、总览
市场情绪回暖。

## 七、次日预案
关注低开修复的个股。

## 附录
其他内容
"""


@pytest.fixture
def out(tmp_path, monkeypatch):
    """注入 tmp output 目录，并写入若干历史产物。"""
    from daily_review.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "output_dir", tmp_path)
    (tmp_path / "20260810_复盘.md").write_text(REVIEW_MD, encoding="utf-8")
    (tmp_path / "20260810_隔夜预案.md").write_text("# 隔夜\n消息面", encoding="utf-8")
    (tmp_path / "20260811_复盘.md").write_text("## 一、总览\n无预案。", encoding="utf-8")
    (tmp_path / "20260812_看板.html").write_text("<html>看板</html>", encoding="utf-8")
    return tmp_path


def test_list_reports_sorts_desc_and_flags(out):
    from daily_review.web.history import list_reports

    reports = list_reports()
    assert [r["date"] for r in reports] == ["20260812", "20260811", "20260810"]
    by_date = {r["date"]: r for r in reports}
    assert by_date["20260812"]["has_dashboard"] is True
    assert by_date["20260812"]["has_review"] is False
    assert by_date["20260811"]["has_review"] is True
    assert by_date["20260810"] == {
        "date": "20260810", "has_review": True, "has_overnight": True,
        "has_open": False, "has_dashboard": False,
    }


def test_list_reports_empty_dir(tmp_path, monkeypatch):
    from daily_review.config import get_settings
    from daily_review.web.history import list_reports

    monkeypatch.setattr(get_settings(), "output_dir", tmp_path)
    assert list_reports() == []


def test_list_reports_ignores_unmatched(out):
    from daily_review.web.history import list_reports

    (out / "20260813_其他.md").write_text("x", encoding="utf-8")
    (out / "杂项.txt").write_text("x", encoding="utf-8")
    assert all(r["date"] != "20260813" for r in list_reports())


def test_load_report_renders_full_and_plan(out):
    from daily_review.web.history import load_report

    r = load_report("20260810")
    assert r is not None
    assert r["trade_date"] == "20260810"
    assert "市场情绪回暖" in r["report_html"]
    assert "关注低开修复" in r["plan_html"]
    assert "附录" not in r["plan_html"]  # 预案章节止于下一同级标题


def test_load_report_missing_date_returns_none(out):
    from daily_review.web.history import load_report

    assert load_report("20260813") is None  # 有看板无复盘 → None
    assert load_report("20260901") is None


def test_load_report_invalid_date(out):
    from daily_review.web.history import load_report

    with pytest.raises(ValueError):
        load_report("2026-08-10")
    with pytest.raises(ValueError):
        load_report("abc")


def test_load_report_path_traversal_blocked(out):
    """日期参数仅允许 8 位数字：../../ 等穿越写法在正则处即被拒绝（ValueError → API 400）。"""
    from daily_review.web.history import load_report

    with pytest.raises(ValueError):
        load_report("../../20260810")
    with pytest.raises(ValueError):
        load_report("20260810/../20260809")


# ---------------------------------------------------------------- API 层


@pytest.fixture
def api_client(out):
    """隔离的 Flask app：output 注入 tmp，历史 API 用真实路径读取。"""
    from daily_review.web.app import create_app

    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def test_api_history_list(api_client):
    r = api_client.get("/api/review/history")
    assert r.status_code == 200
    data = r.get_json()
    assert [x["date"] for x in data["reports"]] == ["20260812", "20260811", "20260810"]


def test_api_history_day_ok(api_client):
    r = api_client.get("/api/review/history/20260810")
    assert r.status_code == 200
    data = r.get_json()
    assert data["trade_date"] == "20260810"
    assert "市场情绪回暖" in data["report_html"]
    assert "关注低开修复" in data["plan_html"]


def test_api_history_day_404(api_client):
    r = api_client.get("/api/review/history/20260901")
    assert r.status_code == 404
    assert "复盘" in r.get_json()["error"]


def test_api_history_day_invalid(api_client):
    r = api_client.get("/api/review/history/2026-08-10")
    assert r.status_code == 400