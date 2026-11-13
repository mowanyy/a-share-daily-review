"""eval 包测试（v0.37）：L0 确定性校验 + L1 快照回比，离线、零 LLM、零网络。

覆盖：数字抽取与约数归一化、L0 规则（章节/交易日/纪律/合规/长度）、L1 回比通过/篡改失败、
快照缺失 skip 降级、plan/open 昨日情绪温度、审计入库、golden set（真实 20260806 报告）、
零成本断言（评估过程无 LLM 调用、无网络请求）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daily_review.eval import evaluate_report
from daily_review.eval import extract as extract_mod
from daily_review.eval.models import EvalReport
from daily_review.web.audit import AuditDB

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 离线交易日历夹具（避免触碰真实 data/trade_calendar.csv）
_CAL = {"20260805", "20260806", "20260908", "20260909"}


@pytest.fixture(autouse=True)
def _offline_calendar(monkeypatch):
    monkeypatch.setattr("daily_review.eval.checks.read_trade_calendar_offline", lambda: set(_CAL))


# ---------------------------------------------------------------- 测试数据


def _review_text(zt: int = 79, emotion: float = 73.3, max_lb: int = 10,
                 break_rate: float = 20.2, *, has_plan: bool = True) -> str:
    plan = "## 七、次日预案\n\n（正文略）\n" if has_plan else ""
    return f"""## 一、总览

情绪温度 {emotion} 分 / 周期 退潮期。核心数据：涨停 {zt} 家，连板 22 家，首板 57 家，最高板 {max_lb} 板（爱丽家居），炸板率 {break_rate}%。

## 二、情绪温度

（正文略）

## 三、连板梯队

## 四、题材运行周期与归类

## 五、炸板与资金

## 六、龙虎榜与游资

{plan}"""


def _snapshot() -> dict:
    return {
        "emotion": {"available": True, "trade_date": "20260806", "score": 73.3, "stage": "退潮期"},
        "ladder": {
            "zt_count": 79, "lianban_count": 22, "first_board_count": 57,
            "max_lb": 10, "max_lb_stock": "爱丽家居", "break_count": 20,
            "break_rate": 0.202, "promotion": {"1进2": 0.1268},
        },
        "themes": [{
            "theme_name": "机器人", "member_count": 3, "max_lb": 3, "stage": "发酵",
            "leader": {"code": "600001", "name": "龙头一", "lb_num": 3},
        }],
    }


def test_review_all_pass(tmp_path):
    p = tmp_path / "_review.md"
    p.write_text(_review_text(), encoding="utf-8")
    rep = evaluate_report("20260806", "review", output_path=p, indicators=_snapshot())
    assert rep.fail_count == 0
    by = {c.id: c for c in rep.checks}
    assert by["EVAL-001"].passed
    assert by["EVAL-002"].passed
    assert by["EVAL-101"].passed      # 情绪温度 73.3 vs 73.3
    assert by["EVAL-102"].passed      # 涨停 79
    assert by["EVAL-105"].passed      # 最高板 10
    assert by["EVAL-107"].passed      # 炸板率 20.2% vs 0.202


def test_tampered_zt_count_fails(tmp_path):
    p = tmp_path / "_review.md"
    p.write_text(_review_text(zt=99), encoding="utf-8")
    rep = evaluate_report("20260806", "review", output_path=p, indicators=_snapshot())
    by = {c.id: c for c in rep.checks}
    assert by["EVAL-102"].level == "error"
    assert "99" in by["EVAL-102"].message and "79" in by["EVAL-102"].message
    assert rep.fail_count == 1


def test_tampered_emotion_fails(tmp_path):
    p = tmp_path / "_review.md"
    p.write_text(_review_text(emotion=80.0), encoding="utf-8")
    rep = evaluate_report("20260806", "review", output_path=p, indicators=_snapshot())
    by = {c.id: c for c in rep.checks}
    assert by["EVAL-101"].level == "error"  # 绝对差 6.7 > 1.0
    assert rep.fail_count == 1


def test_missing_file_single_error(tmp_path):
    p = tmp_path / "not_exists.md"
    rep = evaluate_report("20260806", "review", output_path=p, indicators=_snapshot())
    assert len(rep.checks) == 1
    assert rep.checks[0].id == "EVAL-001"
    assert rep.checks[0].level == "error"
    assert rep.fail_count == 1


def test_missing_sections_error(tmp_path):
    p = tmp_path / "_review.md"
    p.write_text(_review_text(has_plan=False), encoding="utf-8")
    rep = evaluate_report("20260806", "review", output_path=p, indicators=_snapshot())
    by = {c.id: c for c in rep.checks}
    assert by["EVAL-002"].level == "error"
    assert "七、次日预案" in by["EVAL-002"].message


def test_no_snapshot_all_l1_skip(tmp_path):
    p = tmp_path / "_review.md"
    p.write_text(_review_text(), encoding="utf-8")
    rep = evaluate_report("20260806", "review", output_path=p, indicators=None)
    assert rep.fail_count == 0                        # 快照缺失 → 回比全 skip，不误报
    assert any(c.id == "EVAL-101" and c.level == "skip" for c in rep.checks)


def test_compliance_strong_error(tmp_path):
    text = _review_text() + "\n建议买入，稳健获利\n"
    p = tmp_path / "_review.md"
    p.write_text(text, encoding="utf-8")
    rep = evaluate_report("20260806", "review", output_path=p, indicators=_snapshot())
    by = {c.id: c for c in rep.checks}
    assert by["EVAL-005"].level == "error"


def test_compliance_data_word_not_warn(tmp_path):
    """数据章节的「净买入」等数据词不触发合规警告。"""
    text = _review_text() + "## 五、炸板与资金\n\n净买入排行（略）\n"
    p = tmp_path / "_review.md"
    p.write_text(text, encoding="utf-8")
    rep = evaluate_report("20260806", "review", output_path=p, indicators=_snapshot())
    by = {c.id: c for c in rep.checks}
    assert by["EVAL-005"].passed


def test_prev_emotion_plan_pass_and_fail(tmp_path):
    prev = {"emotion": {"available": True, "score": 54.8}}
    ok = "## 1. 隔夜消息面汇总\n\n昨日情绪温度 54.8 分。\n\n## 2. 消息-题材联动分析\n\n## 3. 今日关注方向\n"
    p = tmp_path / "_plan.md"
    p.write_text(ok, encoding="utf-8")
    rep = evaluate_report("20260909", "plan", output_path=p, prev_indicators=prev)
    assert rep.fail_count == 0
    assert {c.id: c for c in rep.checks}["EVAL-109"].passed

    p.write_text(ok.replace("54.8", "60.0"), encoding="utf-8")
    rep = evaluate_report("20260909", "plan", output_path=p, prev_indicators=prev)
    assert {c.id: c for c in rep.checks}["EVAL-109"].level == "error"


def test_promotions_verify(tmp_path):
    text = _review_text() + "\n其中 1进2 晋级率 12.68%。\n"
    p = tmp_path / "_review.md"
    p.write_text(text, encoding="utf-8")
    rep = evaluate_report("20260806", "review", output_path=p, indicators=_snapshot())
    by = {c.id: c for c in rep.checks}
    assert by["EVAL-108"].passed  # 报告 12.68% vs 快照 0.1268 → 一致

    p.write_text(_review_text() + "\n其中 1进2 晋级率 50.0%。\n", encoding="utf-8")
    rep = evaluate_report("20260806", "review", output_path=p, indicators=_snapshot())
    assert {c.id: c for c in rep.checks}["EVAL-108"].level == "error"

def test_audit_evaluations(tmp_path):
    db = AuditDB(tmp_path / "audit.db")
    db.log_evaluation("20260806", "review", '[{"id":"EVAL-001"}]', 1, 1, 2)
    rows = db.recent_evaluations()
    assert len(rows) == 1
    assert rows[0]["trade_date"] == "20260806"
    assert rows[0]["pass_count"] == 1 and rows[0]["fail_count"] == 1


def test_zero_llm_zero_network(tmp_path, monkeypatch):
    """评估过程不得调用 LLM、不得发出网络请求。"""
    def _boom(*a, **k):
        raise AssertionError("评估过程不应调用网络/LLM！")
    monkeypatch.setattr("requests.get", _boom)
    monkeypatch.setattr("requests.post", _boom)
    monkeypatch.setattr("daily_review.llm.client.chat", _boom)

    p = tmp_path / "_review.md"
    p.write_text(_review_text(), encoding="utf-8")
    rep = evaluate_report("20260806", "review", output_path=p, indicators=_snapshot())
    assert isinstance(rep, EvalReport)
    assert rep.fail_count == 0


def test_extract_approx_and_percent_norm():
    # to_number：剥离约数前缀 + 支持百分比；单位由字段级抽取器处理（如 涨停 (\d+) 家）
    assert extract_mod.to_number("约 80") == 80.0
    assert extract_mod.to_number("近 20%") == 20.0
    assert extract_mod.to_number("73.3") == 73.3
    assert extract_mod.to_number("abc") is None
    assert extract_mod.extract_emotion_score("情绪温度 73.3 分") == 73.3
    assert extract_mod.extract_zt_count("涨停 79 家") == 79
    assert extract_mod.extract_break_rate("炸板率 20.2%") == 20.2
    assert extract_mod.extract_max_lb("空间板高度升至 10 板") == 10
    assert extract_mod.extract_max_lb_stock("空间板 10 板（603221 爱丽家居）") == "爱丽家居"
    assert extract_mod.extract_promotions("1进2 晋级率 12.68%。3进4 晋级 100%.") == [("1进2", 12.68), ("3进4", 100.0)]
    assert extract_mod.extract_prev_emotion("昨日情绪温度 54.8") == 54.8


def test_golden_20260806(monkeypatch):
    """golden set：真实历史报告 output/20260806_复盘.md 跑通，无 error 级失败。"""
    report = PROJECT_ROOT / "output" / "20260806_复盘.md"
    if not report.exists():
        pytest.skip("golden 报告文件缺失（output/20260806_复盘.md）")
    # 与真实报告一致的权威快照（报告数值来源，防止本地测试快照假文件干扰）
    ind = {
        "emotion": {"available": True, "score": 73.3, "stage": "退潮期"},
        "ladder": {
            "zt_count": 79, "lianban_count": 22, "first_board_count": 57,
            "max_lb": 10, "max_lb_stock": "爱丽家居", "break_count": 20,
            "break_rate": 0.202, "promotion": {},
        },
        "themes": [{"leader": {"name": "欣天科技"}}, {"leader": {"name": "百合花"}},
                   {"leader": {"name": "昊华能源"}}],
    }
    rep = evaluate_report("20260806", "review", indicators=ind)
    assert rep.fail_count == 0
    by = {c.id: c for c in rep.checks}
    assert by["EVAL-001"].passed
    assert by["EVAL-002"].passed
    assert by["EVAL-101"].passed       # 情绪温度 73.3
    assert by["EVAL-102"].passed       # 涨停 79
    assert by["EVAL-106"].passed       # 空间板龙头 爱丽家居