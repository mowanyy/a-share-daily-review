"""本地计划任务（v0.22）：用 schtasks 准点触发 push，绕开 GitHub Actions 排程延迟。

09:25 开盘策略对时效要求极高（竞价后即时筛选），而 GitHub Actions 的 schedule 派发
实测迟到 30-60 分钟（见 docs/飞书推送说明.md「排程延迟与状态提示」），不可接受；
改由本机 Windows 计划任务**秒级准点**触发：
  - `/SC WEEKLY /D MON,TUE,WED,THU,FRI` 锁死仅工作日触发（周末连进程都不启动）
  - `/ST 09:25` 准点触发
  - `/TR` 走 `tools/scheduled_push.bat`（cd 项目根 + PYTHONPATH + 日志重定向到 output/）

周末/休市的业务判断仍在 `push.push_report` 内兜底（周末静默跳过）。

CLI：`python -m daily_review schedule install|remove|list`（install 支持 --dry-run 只打印）。
"""

from __future__ import annotations

import locale
import subprocess
from dataclasses import dataclass
from pathlib import Path

# 任务定义：任务名 → (报告类型, 触发时间 HH:MM)。后续加时段/任务只改这里。
SCHEDULED_TASKS: dict[str, tuple[str, str]] = {
    "DailyReview-开盘策略": ("open", "09:25"),
}

_PROJECT_ROOT = Path(__file__).resolve().parents[2]          # src/daily_review/schedule.py → 项目根
WRAPPER_BAT = _PROJECT_ROOT / "tools" / "scheduled_push.bat"
_WEEKDAYS = "MON,TUE,WED,THU,FRI"                            # schtasks: 仅周一~周五触发


@dataclass
class TaskResult:
    """单条计划任务操作结果。"""

    name: str
    ok: bool
    output: str = ""


def _decode_console(raw: bytes) -> str:
    """schtasks 输出走系统 ANSI 代码页（中文 Windows = GBK）；Git Bash 下 locale 是
    UTF-8 不可靠，故按 gbk → utf-8 依次尝试解码，防止终端/CI 乱码。"""
    for enc in ("gbk", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _run_schtasks(cmd: list[str]) -> str:
    """运行 schtasks 并返回输出；非 0 退出抛 RuntimeError（附 stderr）。"""
    proc = subprocess.run(cmd, capture_output=True, timeout=60)
    raw = proc.stdout or b""
    if proc.stderr:
        raw = raw + b"\n" + proc.stderr
    text = _decode_console(raw).strip()
    if proc.returncode != 0:
        raise RuntimeError(f"schtasks 失败 rc={proc.returncode}：{text}")
    return text


def build_create_command(bat: Path, task_type: str, name: str, start_time: str) -> list[str]:
    """构造 schtasks /Create 命令（TR 走 cmd /c wrapper，锁死仅工作日）。"""
    tr = f'cmd /c ""{bat}" {task_type}"'
    return [
        "schtasks",
        "/Create", "/TN", name,
        "/SC", "WEEKLY", "/D", _WEEKDAYS, "/ST", start_time,
        "/TR", tr,
        "/F",  # 已存在则覆盖重建（改时间/命令后重跑 install 即生效）
    ]


def install(dry_run: bool = False) -> list[TaskResult]:
    """创建全部计划任务；dry_run=True 只打印将执行的命令，不做任何系统改动。"""
    results: list[TaskResult] = []
    for name, (task_type, start_time) in SCHEDULED_TASKS.items():
        cmd = build_create_command(WRAPPER_BAT, task_type, name, start_time)
        if dry_run:
            results.append(TaskResult(name=name, ok=True, output="DRY-RUN: " + " ".join(cmd)))
            continue
        try:
            out = _run_schtasks(cmd)
        except RuntimeError as exc:
            results.append(TaskResult(name=name, ok=False, output=str(exc)))
            continue
        results.append(TaskResult(name=name, ok=True, output=out or "任务已创建"))
    return results


def remove() -> list[TaskResult]:
    """删除全部计划任务（未注册的报错不致命，逐条返回）。"""
    results: list[TaskResult] = []
    for name in SCHEDULED_TASKS:
        cmd = ["schtasks", "/Delete", "/TN", name, "/F"]
        try:
            out = _run_schtasks(cmd)
        except RuntimeError as exc:
            results.append(TaskResult(name=name, ok=False, output=str(exc)))
            continue
        results.append(TaskResult(name=name, ok=True, output=out or "任务已删除"))
    return results


def list_tasks() -> list[TaskResult]:
    """查询计划任务注册状态（schtasks /Query）。"""
    results: list[TaskResult] = []
    for name, (task_type, start_time) in SCHEDULED_TASKS.items():
        cmd = ["schtasks", "/Query", "/TN", name, "/FO", "LIST"]
        try:
            out = _run_schtasks(cmd)
        except RuntimeError as exc:
            results.append(TaskResult(name=name, ok=False, output=str(exc)))
            continue
        results.append(TaskResult(name=name, ok=True, output=out))
    return results