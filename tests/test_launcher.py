"""启动器纯核心测试（离线，不 import tkinter，不 spawn 子进程）。"""

from __future__ import annotations

import base64
import os
import subprocess
import sys

import pytest

from daily_review import launcher


# ---------------------------------------------------------------- 环境解析


def test_resolve_interpreter_pythonw_uses_sibling_python(tmp_path, monkeypatch):
    pyw = tmp_path / "pythonw.exe"
    pyw.write_bytes(b"")
    (tmp_path / "python.exe").write_bytes(b"")
    monkeypatch.setattr(sys, "executable", str(pyw))
    assert launcher.resolve_interpreter() == str(tmp_path / "python.exe")


def test_resolve_interpreter_pythonw_without_sibling_keeps_pyw(tmp_path, monkeypatch):
    pyw = tmp_path / "pythonw.exe"
    pyw.write_bytes(b"")
    monkeypatch.setattr(sys, "executable", str(pyw))
    assert launcher.resolve_interpreter() == str(pyw)


def test_resolve_interpreter_env_override(tmp_path, monkeypatch):
    py = tmp_path / "python.exe"
    py.write_bytes(b"")
    monkeypatch.setenv("DAILY_REVIEW_PY", str(py))
    assert launcher.resolve_interpreter() == str(py.resolve())


def test_resolve_interpreter_missing_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("DAILY_REVIEW_PY", str(tmp_path / "nope.exe"))
    with pytest.raises(launcher.LauncherError):
        launcher.resolve_interpreter()


def test_resolve_runtime_sets_env(tmp_path, monkeypatch):
    py = tmp_path / "python.exe"
    py.write_bytes(b"")
    monkeypatch.setenv("DAILY_REVIEW_PY", str(py))
    monkeypatch.setenv("DAILY_REVIEW_ROOT", str(tmp_path))
    runtime = launcher.resolve_runtime()
    assert runtime["interpreter"] == str(py.resolve())
    assert runtime["root"] == str(tmp_path.resolve())
    assert runtime["env"]["PYTHONPATH"] == str(tmp_path / "src")
    assert runtime["env"]["PYTHONIOENCODING"] == "utf-8"
    assert runtime["env"]["PYTHONUNBUFFERED"] == "1"


def test_resolve_runtime_missing_interpreter(tmp_path, monkeypatch):
    monkeypatch.setenv("DAILY_REVIEW_PY", str(tmp_path / "nope.exe"))
    monkeypatch.setenv("DAILY_REVIEW_ROOT", str(tmp_path))
    with pytest.raises(launcher.LauncherError):
        launcher.resolve_runtime()


# ---------------------------------------------------------------- argv 构造


@pytest.fixture
def runtime(tmp_path):
    # 提供 pythonw.exe 兄弟，供 build_shortcut_ps 的 _pythonw_of 切换到 pythonw 目标
    (tmp_path / "python.exe").write_bytes(b"")
    (tmp_path / "pythonw.exe").write_bytes(b"")
    return {"interpreter": str(tmp_path / "python.exe"), "root": str(tmp_path), "env": {}}


def test_build_review_argv(runtime):
    argv = launcher.build_review_argv(runtime)
    assert argv[:4] == [runtime["interpreter"], "-m", "daily_review", "review"]
    assert "--date" not in argv
    argv = launcher.build_review_argv(runtime, date="20260806", no_llm=True, strategy="strategy.user-abc")
    assert argv == [
        runtime["interpreter"], "-m", "daily_review", "review",
        "--date", "20260806", "--no-llm", "--strategy", "strategy.user-abc",
    ]


def test_build_dashboard_argv(runtime):
    argv = launcher.build_dashboard_argv(runtime, date="20260806", no_llm=True, open_=True, days=10)
    assert argv == [
        runtime["interpreter"], "-m", "daily_review", "dashboard",
        "--date", "20260806", "--days", "10", "--no-llm", "--open",
    ]
    # days 钳位到 [2, 30]：低于 2 拉回 2，超出（spinbox 上限）拉回 30
    def days_of(d):
        a = launcher.build_dashboard_argv(runtime, days=d)
        return a[a.index("--days") + 1]

    assert days_of(1) == "2"
    assert days_of(999) == "30"
    assert days_of(0) == "2"
    # 空日期不传 --date；open 可选
    argv2 = launcher.build_dashboard_argv(runtime, open_=False)
    assert "--date" not in argv2 and "--open" not in argv2


def test_build_web_argv(runtime):
    argv = launcher.build_web_argv(runtime, open_=True, port=5000)
    assert argv == [
        runtime["interpreter"], "-m", "daily_review", "web",
        "--host", "127.0.0.1", "--port", "5000", "--open",
    ]
    # 端口钳位：越界拉回合法区间（--port 的值在 --port 后面）
    def port_of(p):
        a = launcher.build_web_argv(runtime, port=p)
        return a[a.index("--port") + 1]

    assert port_of(0) == "1"
    assert port_of(70000) == "65535"


def test_build_qa_argv(runtime):
    argv = launcher.build_qa_argv(runtime)
    assert argv == [runtime["interpreter"], "-m", "daily_review", "qa"]
    assert "--date" not in argv
    assert launcher.build_qa_argv(runtime, date="20260806")[-2:] == ["--date", "20260806"]


def test_build_all_commands_has_four_tools(runtime):
    cmds = launcher.build_all_commands(runtime)
    assert [c["tool"] for c in cmds] == ["web", "review", "dashboard", "qa"]
    assert all(c["argv"][:4] == [runtime["interpreter"], "-m", "daily_review", c["tool"]] for c in cmds)


# ---------------------------------------------------------------- 日期 / 探测


def test_validate_date():
    assert launcher.validate_date("")
    assert launcher.validate_date("20260806")
    assert not launcher.validate_date("20261301")
    assert not launcher.validate_date("20260800")
    assert not launcher.validate_date("2026-08-06")
    assert not launcher.validate_date("abc")
    # 日历上不存在的日期必须拒绝（月/日范围合法 ≠ 真实日期）
    assert not launcher.validate_date("20260230")  # 2 月无 30 日
    assert not launcher.validate_date("20260431")  # 4 月无 31 日
    assert launcher.validate_date("20240229")      # 2024 闰年 2 月 29 合法
    assert not launcher.validate_date("20230229")  # 2023 非闰年 2 月 29 不存在


def test_probe_recent_date(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        "daily_review.data.eastmoney_pool.resolve_recent_trade_dates",
        lambda *a, **k: calls.update(probed=True) or ["20260806"],
    )
    assert launcher.probe_recent_date() == "20260806"

    def boom(*a, **k):
        raise RuntimeError("offline")

    monkeypatch.setattr("daily_review.data.eastmoney_pool.resolve_recent_trade_dates", boom)
    assert launcher.probe_recent_date() == ""


def test_list_strategies(monkeypatch):
    class FakePrompt:
        id = "strategy.user-x"
        name = "连板接力"
        status = "active"

    monkeypatch.setattr("daily_review.web.strategy.iter_all", lambda: [FakePrompt()])
    assert launcher.list_strategies() == [{"id": "strategy.user-x", "name": "连板接力", "status": "active"}]

    def boom():
        raise RuntimeError

    monkeypatch.setattr("daily_review.web.strategy.iter_all", boom)
    assert launcher.list_strategies() == []


# ---------------------------------------------------------------- 桌面快捷方式


def test_build_shortcut_ps(runtime):
    argv = launcher.build_shortcut_ps(runtime)
    assert argv[:3] == ["powershell", "-NoProfile", "-EncodedCommand"]
    script = base64.b64decode(argv[3]).decode("utf-16-le")
    assert launcher.SHORTCUT_NAME in script            # 中文 lnk 名原样进 UTF-16 script
    assert "WScript.Shell" in script
    assert "pythonw.exe" in script                     # python.exe → pythonw.exe 目标
    assert "launcher.py" in script                     # Arguments 指向根目录入口
    assert runtime["root"] in script                   # WorkingDirectory=项目根
    assert "$s.Save()" in script


def test_create_shortcut(runtime, monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kw"] = kwargs
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(launcher.subprocess, "run", fake_run)
    name = launcher.create_shortcut(runtime)
    assert name == launcher.SHORTCUT_NAME
    assert captured["argv"][:3] == ["powershell", "-NoProfile", "-EncodedCommand"]
    assert captured["kw"].get("creationflags") == getattr(subprocess, "CREATE_NO_WINDOW", 0)
