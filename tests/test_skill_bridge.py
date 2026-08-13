"""web/skill_bridge.py 测试：SKILL.md ↔ 战法双向转换 + Web API + 知识库收录（全离线）。"""

from __future__ import annotations

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

SAMPLE_SKILL = """---
name: 深度价值-张坤型
description: 以「深度价值（张坤型）」风格分析 A 股个股/板块/基金持仓。当用户要你用张坤/深度价值/白马价值视角分析时触发。
---

## 1. 风格画像
优质公司 + 合理价格 + 长期持有。

## 2. 核心判断
ROE 常年 ≥15%。
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    """注入 tmp 目录为 data/prompts 根（含一个 tracked 战法），返回 (tmp_path, flask_app)。"""
    from daily_review.config import get_settings
    from daily_review.web.app import create_app

    s = get_settings()
    monkeypatch.setattr(s, "data_dir", tmp_path / "data")
    monkeypatch.setattr(s, "prompts_dir", tmp_path / "prompts")
    tdir = tmp_path / "prompts" / "strategies"
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "战法模板.md").write_text(TRACKED_MD, encoding="utf-8")

    app = create_app()
    app.config["TESTING"] = True
    return tmp_path, app


# ---------------------------------------------------------------- import_skill


def test_import_skill_creates_strategy(env):
    from daily_review.web.skill_bridge import import_skill
    from daily_review.web.strategy import make_id

    tmp, _ = env
    result = import_skill(SAMPLE_SKILL)
    assert result["id"] == make_id("深度价值-张坤型")
    assert result["status"] == "draft"
    assert result["source"] == "user"
    # description 首句过长 → applies_to 回退标识
    assert result["missing_sections"]  # skill 正文没有战法 8 节 → 缺节告警

    path = tmp / "data" / "strategies" / "战法-深度价值-张坤型.md"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert f"id: {result['id']}" in text
    assert "role: strategy" in text
    assert "status: draft" in text
    assert "## 1. 风格画像" in text          # 正文原样保留
    assert "## 2. 核心判断" in text
    assert "由 ZCode skill 导入" in text


def test_import_skill_short_description_applies_to(env):
    from daily_review.web.skill_bridge import import_skill
    from daily_review.web.strategy import get_strategy

    _, _ = env
    r = import_skill("---\nname: 低吸2\ndescription: 修复期使用\n---\n\n## 规则\n均线低吸")
    assert get_strategy(r["id"]).applies_to == "修复期使用"
    r2 = import_skill("---\nname: 低吸3\n---\n\n## 规则\n均线低吸")
    assert get_strategy(r2["id"]).applies_to == "（未指定，见正文）"


def test_import_skill_missing_name_raises(env):
    from daily_review.web.skill_bridge import import_skill

    _, _ = env
    with pytest.raises(ValueError):
        import_skill("---\ndescription: 只有说明没有名字\n---\n\n正文")


def test_import_skill_empty_raises(env):
    from daily_review.web.skill_bridge import import_skill

    _, _ = env
    with pytest.raises(ValueError):
        import_skill("   ")
    with pytest.raises(ValueError):
        import_skill("---\nname: x\n---")  # 正文为空


def test_import_skill_same_name_overwrites(env):
    """同名（同 id）重复导入 → 覆盖同一文件，不新增重复。"""
    from daily_review.web.skill_bridge import import_skill

    tmp, _ = env
    r1 = import_skill("---\nname: 同名\n---\n\nv1")
    r2 = import_skill("---\nname: 同名\n---\n\nv2")
    assert r1["id"] == r2["id"]
    files = list((tmp / "data" / "strategies").glob("*.md"))
    assert len(files) == 1
    assert files[0].read_text(encoding="utf-8").rstrip().endswith("v2")


# ---------------------------------------------------------------- export_strategy


def test_export_user_strategy(env):
    from daily_review.web.skill_bridge import export_strategy, import_skill

    _, _ = env
    result = import_skill(SAMPLE_SKILL)
    skill_md = export_strategy(result["id"])
    assert "name: 深度价值-张坤型" in skill_md
    assert "description:" in skill_md
    assert "## 1. 风格画像" in skill_md
    assert f"来源于每日复盘战法 {result['id']}" in skill_md


def test_export_tracked_strategy(env):
    from daily_review.web.skill_bridge import export_strategy

    _, _ = env
    skill_md = export_strategy("strategy.template")
    assert "name: 战法模板" in skill_md
    assert "## 1. 概述" in skill_md


def test_export_unknown_raises_404(env):
    from daily_review.web.skill_bridge import export_strategy
    from daily_review.web.strategy import StrategyError

    _, _ = env
    with pytest.raises(StrategyError) as ei:
        export_strategy("strategy.user-0000000000")
    assert ei.value.code == 404


def test_roundtrip_export_import(env):
    """export(import(x)) 后再 import 仍是同一 id，正文可再导出。"""
    from daily_review.web.skill_bridge import export_strategy, import_skill

    _, _ = env
    r1 = import_skill(SAMPLE_SKILL)
    skill_md = export_strategy(r1["id"])
    r2 = import_skill(skill_md)
    assert r1["id"] == r2["id"]
    assert "## 1. 风格画像" in export_strategy(r2["id"])


# ---------------------------------------------------------------- Web API


def test_api_import_skill(env):
    _, app = env
    r = app.test_client().post("/api/strategies/import-skill", json={"markdown": SAMPLE_SKILL})
    assert r.status_code == 201
    d = r.get_json()
    assert d["name"] == "深度价值-张坤型"
    assert d["id"].startswith("strategy.user-")


def test_api_import_skill_empty_400(env):
    _, app = env
    assert app.test_client().post("/api/strategies/import-skill", json={"markdown": "  "}).status_code == 400
    # 无 name 的 front-matter → ValueError → 400
    r = app.test_client().post(
        "/api/strategies/import-skill", json={"markdown": "---\ndescription: x\n---\n\n正文"}
    )
    assert r.status_code == 400


def test_api_export_skill(env):
    from daily_review.web.skill_bridge import import_skill

    _, app = env
    r1 = import_skill(SAMPLE_SKILL)
    r = app.test_client().get(f"/api/strategies/{r1['id']}/export-skill")
    assert r.status_code == 200
    d = r.get_json()
    assert d["strategy_id"] == r1["id"]
    assert "name: 深度价值-张坤型" in d["skill_markdown"]


def test_api_export_skill_unknown_404(env):
    _, app = env
    r = app.test_client().get("/api/strategies/strategy.user-0000000000/export-skill")
    assert r.status_code == 404


# ---------------------------------------------------------------- 知识库收录


def test_corpus_discovers_skills(tmp_path):
    from daily_review.kb.corpus import chunk_file, discover_sources

    sp = tmp_path / "proj" / "skills" / "fund-styles"
    sp.mkdir(parents=True)
    f = sp / "深度价值-张坤型.md"
    f.write_text(SAMPLE_SKILL, encoding="utf-8")

    rels = [p.relative_to(tmp_path / "proj").as_posix() for p in discover_sources(tmp_path / "proj")]
    assert "skills/fund-styles/深度价值-张坤型.md" in rels

    chunks = chunk_file(f, root=tmp_path / "proj")
    assert chunks
    assert any("优质公司" in ch.text for ch in chunks)  # front-matter 剥离后正文可检索