"""web/strategy.py 战法服务测试：CRUD / tracked 只读 / 校验告警 / 路径防护 / id 确定性。"""

from __future__ import annotations

import pytest

from daily_review.web.strategy import (
    StrategyError,
    create,
    delete,
    get_strategy,
    iter_all,
    make_id,
    set_status,
    to_dict,
    update,
)

BODY_8 = """## 1. 概述
赚晋级预期差

## 2. 适用情绪阶段
修复期/高潮期

## 3. 选股 / 触发条件
2-4 板，首封 10:30 前

## 4. 买入规则
竞价高开 3-6%

## 5. 卖出与止损
跌破 -5% 止损

## 6. 仓位管理
单票 30%

## 7. 规避与风险
退潮期不接力

## 8. 复盘记录
每日记录
"""

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
def sdirs(tmp_path, monkeypatch):
    """注入 tmp 目录为 data/prompts 根，并造一个 tracked 模板（只读）。"""
    from daily_review.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "data_dir", tmp_path / "data")
    monkeypatch.setattr(s, "prompts_dir", tmp_path / "prompts")
    tdir = tmp_path / "prompts" / "strategies"
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "战法模板.md").write_text(TRACKED_MD, encoding="utf-8")
    return tmp_path


def test_create_roundtrip(sdirs):
    p = create(BODY_8, name="低吸战法", author="我", applies_to="修复期")
    assert p.id.startswith("strategy.user-")
    assert p.name == "低吸战法"
    assert p.author == "我"
    assert p.applies_to == "修复期"
    assert p.status == "draft"
    # 落盘 gitignored data/strategies/
    assert p.path is not None and p.path.is_relative_to((sdirs / "data" / "strategies").resolve())
    assert p.path.exists()
    d = to_dict(p)
    assert d["source"] == "user"
    assert d["missing_sections"] == []
    p2 = get_strategy(p.id)
    assert p2 is not None and p2.name == p.name


def test_create_deterministic_id_no_duplicate(sdirs):
    p1 = create(BODY_8, name="低吸战法")
    p2 = create(BODY_8, name="低吸战法")  # 同名 → 覆盖原文件，保持 1:1
    assert p1.id == p2.id
    assert p2.path == p1.path
    assert len([s for s in iter_all() if s.id == p1.id]) == 1
    assert make_id("低吸战法") == p1.id


def test_create_from_frontmatter_metadata(sdirs):
    md = (
        "---\nname: 粘贴战法\nauthor: A\napplies_to: 高潮期\nstatus: active\n---\n\n"
        "## 1. 概述\n正文"
    )
    p = create(md)
    assert p.name == "粘贴战法"
    assert p.author == "A"
    assert p.applies_to == "高潮期"
    assert p.status == "active"
    assert p.id.startswith("strategy.user-")  # id 不信任用户 front-matter


def test_create_requires_name(sdirs):
    with pytest.raises(StrategyError):
        create("## 1. 概述\n正文", name="   ")


def test_tracked_readonly(sdirs):
    t = get_strategy("strategy.template")
    assert t is not None and to_dict(t)["source"] == "tracked"
    with pytest.raises(StrategyError) as e1:
        update("strategy.template", BODY_8, name="改")
    assert e1.value.code == 403
    with pytest.raises(StrategyError) as e2:
        delete("strategy.template")
    assert e2.value.code == 403
    with pytest.raises(StrategyError) as e3:
        set_status("strategy.template", "active")
    assert e3.value.code == 403


def test_update_keeps_id_changes_body(sdirs):
    p = create(BODY_8, name="低吸战法")
    p2 = update(p.id, BODY_8.replace("每日记录", "双日记录"), name="低吸战法", status="active")
    assert p2.id == p.id
    assert p2.status == "active"
    assert "双日记录" in p2.body


def test_set_status_validates(sdirs):
    p = create(BODY_8, name="低吸战法")
    assert set_status(p.id, "active").status == "active"
    with pytest.raises(StrategyError):
        set_status(p.id, "weird")


def test_missing_sections_only_warns(sdirs):
    p = create("## 1. 概述\n只有一节", name="缺节战法")
    ms = to_dict(p)["missing_sections"]
    assert ms
    assert "概述" not in ms and "买入规则" in ms


def test_filename_sanitize_blocks_traversal(sdirs):
    p = create(BODY_8, name='a/b\\c:*?"<>|d')
    assert p.path is not None
    assert "/" not in p.path.name and "\\" not in p.path.name
    assert p.path.is_relative_to((sdirs / "data" / "strategies").resolve())


def test_iter_merges_tracked_and_user(sdirs):
    create(BODY_8, name="低吸战法")
    ids = [p.id for p in iter_all()]
    assert "strategy.template" in ids
    assert any(i.startswith("strategy.user-") for i in ids)


def test_delete_removes(sdirs):
    p = create(BODY_8, name="低吸战法")
    delete(p.id)
    assert get_strategy(p.id) is None
