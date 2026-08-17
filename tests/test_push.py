"""push.py 定时推送测试：摘要提取 / 周末跳过 / 生成+推送（全离线 mock 生成与推送）。"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from daily_review.push import NoDataError, summarize

_REVIEW_MD = """# 📊 2026-08-17 复盘（周一）

## 一、总览
市场情绪回暖，涨停 50 家。

空间板高度 5 板，炸板率 18%。
## 二、情绪温度
...
## 七、次日预案
关注低开修复的个股。
"""

_PLAN_MD = """# 📊 2026-08-17 隔夜预案（周一）

消息面：隔夜美股收涨，A50 期指上行。

今日关注方向：AI 算力、医药。
"""

_OPEN_MD = """# 📊 2026-08-17 开盘策略（周一）

竞价总览：涨停股高开为主，量能温和。

个股清单：600519 高开 3%、000858 平开。
"""


# ---------------------------------------------------------------- 摘要提取


class TestSummarize:
    def test_review_extracts_title_and_overview(self):
        text = summarize(_REVIEW_MD, "review")
        assert "【每日复盘】复盘报告" in text
        assert "2026-08-17 复盘（周一）" in text
        assert "一、总览" in text
        assert "涨停 50 家" in text
        # 不包含预案章节（只取总览）
        assert "次日预案" not in text

    def test_plan_extracts_body(self):
        text = summarize(_PLAN_MD, "plan")
        assert "隔夜预案" in text
        assert "美股收涨" in text

    def test_open_extracts_body(self):
        text = summarize(_OPEN_MD, "open")
        assert "开盘策略" in text
        assert "竞价总览" in text or "个股清单" in text

    def test_markdown_cleaned(self):
        text = summarize(_REVIEW_MD, "review")
        assert "**" not in text  # 加粗已清理
        assert "## " not in text  # 标题符号已清理


# ---------------------------------------------------------------- 周末跳过


def _workday_now():
    return datetime(2026, 8, 17, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))  # 周一


def _weekend_now():
    return datetime(2026, 8, 15, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))  # 周六


class TestPushReport:
    def _patch(self, monkeypatch, *, weekend=False, gen_result=None, gen_exc=None, send_result=None):
        import daily_review.push as push

        monkeypatch.setattr(push, "beijing_now", _weekend_now if weekend else _workday_now)
        if gen_exc is not None:
            monkeypatch.setattr(push, "generate", lambda *a, **kw: (_ for _ in ()).throw(gen_exc))
        else:
            monkeypatch.setattr(push, "generate", lambda report_type, date: gen_result or "# 标题\n\n正文")
        if send_result is not None:
            monkeypatch.setattr(push, "send_feishu", lambda text, **kw: send_result)
        else:
            monkeypatch.setattr(push, "send_feishu", lambda text, **kw: {"code": 0})
        return push

    def test_weekend_skipped(self, monkeypatch):
        push = self._patch(monkeypatch, weekend=True)
        result = push.push_report("review")
        assert result["status"] == "skipped"
        assert "周末" in result["message"]

    def test_sent_flow(self, monkeypatch):
        push = self._patch(monkeypatch)
        result = push.push_report("review", date="20260817")
        assert result["status"] == "sent"
        assert result["report_type"] == "review"
        assert result["date"] == "20260817"

    def test_no_data_skipped(self, monkeypatch):
        push = self._patch(monkeypatch, gen_exc=NoDataError("20260817 无涨停数据（非交易日）"))
        result = push.push_report("review", date="20260817")
        assert result["status"] == "skipped"
        assert "非交易日" in result["message"]

    def test_generate_failure_error(self, monkeypatch):
        push = self._patch(monkeypatch, gen_exc=RuntimeError("网络断"))
        result = push.push_report("review", date="20260817")
        assert result["status"] == "error"

    def test_send_failure_error(self, monkeypatch):
        from daily_review.notify import FeishuError

        push = self._patch(monkeypatch, send_result=None)
        monkeypatch.setattr(push, "send_feishu", lambda text, **kw: (_ for _ in ()).throw(FeishuError("飞书失败")))
        result = push.push_report("review", date="20260817")
        assert result["status"] == "error"
        assert "飞书失败" in result["message"]

    def test_invalid_type_raises(self, monkeypatch):
        push = self._patch(monkeypatch)
        with pytest.raises(ValueError, match="report_type"):
            push.push_report("dashboard", date="20260817")


# ---------------------------------------------------------------- 生成（仅验证空数据守卫）


class TestGenerate:
    def test_invalid_type_rejected(self):
        import daily_review.push as push

        with pytest.raises(ValueError, match="review/plan/open"):
            push.generate("nope", "20260817")


def test_beijing_today_is_eight_digits():
    import daily_review.push as push

    assert len(push.beijing_today()) == 8
    assert push.beijing_today().isdigit()