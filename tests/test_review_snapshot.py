"""复盘指标快照（v0.30）——跨消息数值一致性测试。

背景：预案「昨日情绪温度」此前每次重算会漂移（8/11 曾算 59、后算 66）。
本测试锁定：完整复盘落盘权威快照 → 预案/复盘引用快照值，缺失才回退重算并标注来源。
"""

from __future__ import annotations

import inspect


class _FakeSettings:
    """data_dir 指向 tmp_path，杜绝测试写真实 data/。"""

    def __init__(self, data_dir):
        self.data_dir = data_dir


def _snapshot_indicators() -> dict:
    """构造 `昨日` 指标（ladder.trade_date = 20260818），series 最新在前（emotion.py 输出）。"""
    return {
        "ladder": {"trade_date": "20260818", "zt_count": 50, "max_lb": 4},
        "emotion": {
            "available": True,
            "score": 58.4,
            "stage": "退潮期",
            "stage_reason": "近6日情绪温度 66→78→53→37→71→58，今日 58 分（较昨日 -13），判定退潮期",
            "series": [
                {"date": "20260818", "score": 58.4},
                {"date": "20260817", "score": 71.0},
                {"date": "20260814", "score": 37.0},
                {"date": "20260813", "score": 53.0},
                {"date": "20260812", "score": 78.0},
                {"date": "20260811", "score": 66.0},
            ],
        },
        "themes": [],
    }


# ---------------------------------------------------------------- 快照存取

class TestSnapshotStore:
    def test_roundtrip(self, monkeypatch, tmp_path):
        import daily_review.analysis.review_snapshot as rs

        monkeypatch.setattr(rs, "get_settings", lambda: _FakeSettings(tmp_path))
        ind = _snapshot_indicators()
        path = rs.save_review_snapshot(ind, "20260818")
        assert path is not None and path.exists()
        assert rs.load_review_snapshot("20260818") == ind

    def test_missing_returns_none(self, monkeypatch, tmp_path):
        import daily_review.analysis.review_snapshot as rs

        monkeypatch.setattr(rs, "get_settings", lambda: _FakeSettings(tmp_path))
        assert rs.load_review_snapshot("20260899") is None

    def test_corrupt_json_returns_none(self, monkeypatch, tmp_path):
        import daily_review.analysis.review_snapshot as rs

        monkeypatch.setattr(rs, "get_settings", lambda: _FakeSettings(tmp_path))
        p = tmp_path / "review_snapshots" / "20260818.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{ bad json", encoding="utf-8")
        assert rs.load_review_snapshot("20260818") is None

    def test_save_non_json_value_does_not_break(self, monkeypatch, tmp_path):
        """indicators 含非 JSON 原生类型（set）时 default=str 兜底，不中断复盘。"""
        import daily_review.analysis.review_snapshot as rs

        monkeypatch.setattr(rs, "get_settings", lambda: _FakeSettings(tmp_path))
        assert rs.save_review_snapshot({"a": {1, 2}}, "20260818") is not None


# ---------------------------------------------------------------- digest 快照优先（②）

class TestDigestSnapshotPriority:
    def test_digest_prefers_snapshot(self, monkeypatch):
        import daily_review.llm.premarket as pm

        fake = {
            "emotion": {
                "score": 58.4,
                "stage": "退潮期",
                "stage_reason": "快照依据句",
                "series": [
                    {"date": "20260818", "score": 58.4},
                    {"date": "20260811", "score": 66.0},
                ],
            }
        }
        monkeypatch.setattr(pm, "load_review_snapshot", lambda d: fake if d == "20260818" else None)
        ind = _snapshot_indicators()
        ind["emotion"]["score"] = 25.0  # 重算值与快照不同 → 必须取快照
        block = pm._overnight_digest(ind)["情绪温度"]
        assert block["score"] == 58.4
        assert block["stage"] == "退潮期"
        assert block["来源"] == pm._SNAPSHOT_SOURCE
        # 历史序列转 旧→新
        hist = block["历史序列(旧→新)"]
        assert [h["date"] for h in hist] == ["20260811", "20260818"]

    def test_digest_fallback_when_snapshot_missing(self, monkeypatch):
        import daily_review.llm.premarket as pm

        monkeypatch.setattr(pm, "load_review_snapshot", lambda d: None)
        block = pm._overnight_digest(_snapshot_indicators())["情绪温度"]
        assert block["score"] == 58.4  # 回退 indicators 重算值（旧行为）
        assert block["来源"] == pm._RECOMPUTE_SOURCE
        dates = [h["date"] for h in block["历史序列(旧→新)"]]
        assert dates[0] == "20260811" and dates[-1] == "20260818"

    def test_open_strategy_digest_uses_snapshot(self, monkeypatch):
        import daily_review.llm.premarket as pm

        fake = {"emotion": {"score": 58.4, "stage": "退潮期", "stage_reason": "x",
                            "series": [{"date": "20260818", "score": 58.4}]}}
        monkeypatch.setattr(pm, "load_review_snapshot", lambda d: fake if d == "20260818" else None)
        block = pm._open_strategy_digest(_snapshot_indicators())["情绪温度"]
        assert block["score"] == 58.4
        assert "来源" in block

    def test_series_filters_none_scores(self, monkeypatch):
        """序列缺日的 None 分不进历史序列（与 report 的「有分日」口径一致）。"""
        import daily_review.llm.premarket as pm

        monkeypatch.setattr(pm, "load_review_snapshot", lambda d: None)
        ind = _snapshot_indicators()
        ind["emotion"]["series"].append({"date": "20260810", "score": None})
        block = pm._overnight_digest(ind)["情绪温度"]
        assert "20260810" not in [h["date"] for h in block["历史序列(旧→新)"]]


# ---------------------------------------------------------------- 引用纪律（②）

class TestDiscipline:
    def test_overnight_user_discipline(self, monkeypatch):
        import daily_review.llm.premarket as pm

        monkeypatch.setattr(pm, "load_review_snapshot", lambda d: None)
        user = pm._overnight_user(_snapshot_indicators(), [], "20260819")
        assert "逐字引用上方 JSON 数值" in user
        assert "禁止按记忆/推算" in user

    def test_open_strategy_user_discipline(self, monkeypatch):
        import daily_review.llm.premarket as pm

        monkeypatch.setattr(pm, "load_review_snapshot", lambda d: None)
        user = pm._open_strategy_user(_snapshot_indicators(), [], "预案", "20260819")
        assert "逐字引用上方 JSON 数值" in user
        assert "禁止按记忆/推算" in user

    def test_plan_open_paths_never_save_snapshot(self):
        """快照仅完整复盘落盘：预案/开盘策略生成路径不得写快照（防重算覆盖权威值）。"""
        import daily_review.llm.premarket as pm

        assert "save_review_snapshot" not in inspect.getsource(pm)


# ---------------------------------------------------------------- 复盘前日快照缺失提示（③）

class TestReporterPrevSnapshotNotice:
    def _indicators_with_timeline(self) -> dict:
        ind = _snapshot_indicators()
        ind["emotion"]["notes"] = ["盘中预览，数据未完整"]
        ind["timeline_dates"] = ["20260818", "20260817"]  # 最新在前
        return ind

    def test_prev_snapshot_missing_adds_notice(self, monkeypatch):
        import daily_review.analysis.review_snapshot as rs
        from daily_review.llm.reporter import _emotion_payload

        monkeypatch.setattr(rs, "load_review_snapshot", lambda d: None)
        payload = _emotion_payload(self._indicators_with_timeline())
        assert any("昨日复盘快照缺失" in n for n in payload["缺失说明"])
        assert payload["数据说明"] == "盘中预览，数据未完整"  # notes[0] 不受追加影响

    def test_prev_snapshot_exists_no_notice(self, monkeypatch):
        import daily_review.analysis.review_snapshot as rs
        from daily_review.llm.reporter import _emotion_payload

        monkeypatch.setattr(rs, "load_review_snapshot", lambda d: {"ok": True} if d == "20260817" else None)
        payload = _emotion_payload(self._indicators_with_timeline())
        assert not any("快照缺失" in n for n in payload["缺失说明"])