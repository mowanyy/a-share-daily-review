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
情绪温度 66 分，修复期。

## 三、连板梯队
连板 14 家，最高 4 板。

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
        # v0.21.2：推 4 个核心章节，含次日预案（操作建议）
        assert "二、情绪温度" in text
        assert "三、连板梯队" in text
        assert "七、次日预案" in text
        assert "关注低开修复" in text

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


# ---------------------------------------------------------------- 状态提示（v0.21.7 / v0.22）


class TestStatusNotice:
    """状态提示语义：v0.21.7 非 sent 也提示；v0.22 仅周末完全静默（休市/失败仍提示）。"""

    def _make(self, monkeypatch, *, weekend=False, gen_exc=None, send_impl=None):
        import daily_review.push as push

        monkeypatch.setattr(push, "beijing_now", _weekend_now if weekend else _workday_now)
        if gen_exc is not None:
            monkeypatch.setattr(push, "generate", lambda *a, **kw: (_ for _ in ()).throw(gen_exc))
        else:
            monkeypatch.setattr(push, "generate", lambda report_type, date: "# 标题\n\n正文")
        if send_impl is None:
            send_impl = lambda text, **kw: {"code": 0}
        monkeypatch.setattr(push, "send_feishu", send_impl)
        return push

    def test_weekend_skip_is_silent(self, monkeypatch):
        """v0.22：双休日完全静默——0 次 send_feishu（连 ⏭ 也不发，零打扰）。"""
        calls: list[str] = []
        push = self._make(monkeypatch, weekend=True, send_impl=lambda text, **kw: calls.append(text) or {"code": 0})
        result = push.push_report("review")
        assert result["status"] == "skipped"
        assert len(calls) == 0, "周末不得发送任何飞书消息"

    def test_no_data_skip_sends_notice(self, monkeypatch):
        """工作日休市（节假日）仍推 ⏭ 提示，让用户知道为什么没推送。"""
        calls: list[str] = []
        push = self._make(
            monkeypatch,
            gen_exc=NoDataError("20260817 无涨停数据（非交易日）"),
            send_impl=lambda text, **kw: calls.append(text) or {"code": 0},
        )
        result = push.push_report("review", date="20260817")
        assert result["status"] == "skipped"
        assert len(calls) == 1
        assert "⏭" in calls[0]
        assert "非交易日" in calls[0]

    def test_generate_error_sends_notice(self, monkeypatch):
        calls: list[str] = []
        push = self._make(
            monkeypatch,
            gen_exc=RuntimeError("东财网络断"),
            send_impl=lambda text, **kw: calls.append(text) or {"code": 0},
        )
        result = push.push_report("review", date="20260817")
        assert result["status"] == "error"
        assert "❌" in calls[0]
        assert "东财网络断" in calls[0]

    def test_primary_send_failure_sends_notice(self, monkeypatch):
        """主推送失败 → 补发状态提示；提示也失败 → 静默记录，不二次抛错。"""
        from daily_review.notify import FeishuError

        calls: list[str] = []

        def send_impl(text, **kw):
            calls.append(text)
            raise FeishuError("飞书 webhook 失败")

        push = self._make(monkeypatch, send_impl=send_impl)
        result = push.push_report("review", date="20260817")
        assert result["status"] == "error"
        assert len(calls) == 2  # 主报告 + 状态提示各一次
        assert "飞书 webhook 失败" in calls[1]
        assert "状态提示发送失败" in result["message"]

    def test_notice_failure_keeps_skip_status(self, monkeypatch):
        """跳过分支：状态提示本身发送失败 → 主状态保持 skipped，不抛异常。"""
        from daily_review.notify import FeishuError

        push = self._make(
            monkeypatch,
            gen_exc=NoDataError("20260817 无涨停数据"),
            send_impl=lambda text, **kw: (_ for _ in ()).throw(FeishuError("webhook 未配置")),
        )
        result = push.push_report("review", date="20260817")
        assert result["status"] == "skipped"
        assert "状态提示发送失败" in result["message"]

    def test_sent_does_not_send_extra_notice(self, monkeypatch):
        calls: list[str] = []
        push = self._make(monkeypatch, send_impl=lambda text, **kw: calls.append(text) or {"code": 0})
        result = push.push_report("review", date="20260817")
        assert result["status"] == "sent"
        assert len(calls) == 1, "成功时只发主报告，不发状态提示"


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