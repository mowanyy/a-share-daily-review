"""schedule.py 本地计划任务测试：命令构造（schtasks 参数完整）/ dry-run / remove / list。"""

from __future__ import annotations

from pathlib import Path

from daily_review import schedule as sched


def _fake_bat(tmp_path: Path) -> Path:
    bat = tmp_path / "scheduled_push.bat"
    bat.write_text("@echo off\n", encoding="utf-8")
    return bat


class TestBuildCreateCommand:
    def test_open_task_full_command(self, tmp_path):
        bat = _fake_bat(tmp_path)
        cmd = sched.build_create_command(bat, "open", "DailyReview-开盘策略", "09:25")
        assert cmd[0] == "schtasks"
        assert cmd[1:3] == ["/Create", "/TN"]
        assert "DailyReview-开盘策略" in cmd
        assert cmd[4:6] == ["/SC", "WEEKLY"]
        assert cmd[6:8] == ["/D", "MON,TUE,WED,THU,FRI"], "仅工作日触发（周末不启动进程）"
        assert cmd[8:10] == ["/ST", "09:25"]
        assert cmd[-1] == "/F", "已存在时覆盖重建"
        tr = cmd[cmd.index("/TR") + 1]
        assert tr.startswith("cmd /c ")
        assert str(bat) in tr
        assert tr.endswith('open"'), "TR 以报告类型结束"

    def test_every_task_has_weekdays_only(self, tmp_path):
        """所有任务都必须是 WEEKLY + 五工作日，防止周末打扰。"""
        bat = _fake_bat(tmp_path)
        for name, (task_type, start_time) in sched.SCHEDULED_TASKS.items():
            cmd = sched.build_create_command(bat, task_type, name, start_time)
            assert cmd[cmd.index("/D") + 1] == "MON,TUE,WED,THU,FRI"


class TestInstall:
    def test_dry_run_does_not_run_schtasks(self, monkeypatch):
        monkeypatch.setattr(sched, "_run_schtasks", lambda cmd: (_ for _ in ()).throw(AssertionError("不应执行 schtasks")))
        results = sched.install(dry_run=True)
        assert results
        assert all(r.ok for r in results)
        assert all(r.output.startswith("DRY-RUN: schtasks /Create") for r in results)

    def test_install_runs_create_per_task(self, monkeypatch):
        calls: list[list[str]] = []

        def fake_run(cmd):
            calls.append(cmd)
            return f"成功: {cmd[cmd.index('/TN') + 1]}"

        monkeypatch.setattr(sched, "_run_schtasks", fake_run)
        results = sched.install()
        assert len(results) == len(sched.SCHEDULED_TASKS)
        assert all(r.ok for r in results)
        assert all(c[1] == "/Create" for c in calls)

    def test_install_reports_failure_per_task(self, monkeypatch):
        def failing_run(cmd):
            raise RuntimeError("access denied")

        monkeypatch.setattr(sched, "_run_schtasks", failing_run)
        results = sched.install()
        assert len(results) == len(sched.SCHEDULED_TASKS)
        assert all(not r.ok for r in results)
        assert all("access denied" in r.output for r in results)


class TestRemoveAndList:
    def test_remove_runs_delete_per_task(self, monkeypatch):
        calls: list[list[str]] = []

        def fake_run(cmd):
            calls.append(cmd)
            return "删除成功"

        monkeypatch.setattr(sched, "_run_schtasks", fake_run)
        results = sched.remove()
        assert len(results) == len(sched.SCHEDULED_TASKS)
        assert all(r.ok for r in results)
        # schtasks /Delete /TN <name> /F
        assert all(c[1] == "/Delete" and c[2] == "/TN" and c[-1] == "/F" for c in calls)

    def test_list_queries_per_task(self, monkeypatch):
        calls: list[list[str]] = []

        def fake_run(cmd):
            calls.append(cmd)
            return "任务名: DailyReview-开盘策略"

        monkeypatch.setattr(sched, "_run_schtasks", fake_run)
        results = sched.list_tasks()
        assert len(results) == len(sched.SCHEDULED_TASKS)
        assert all(r.ok for r in results)
        assert all(c[1] == "/Query" for c in calls)