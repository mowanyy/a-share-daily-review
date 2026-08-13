"""每日复盘 · 图形启动器（纯核心层，不依赖 tkinter）。

GUI 窗口在 `daily_review/launcher_gui.py`（唯一 import tkinter 的文件）；
本模块只提供可离线单测的纯函数：
  - resolve_runtime()：解析子进程运行所需的 解释器 / 项目根 / 环境变量
  - build_*_argv()：为 web / review / dashboard / qa 构造完整 argv（含 -m daily_review）
  - validate_date() / probe_recent_date() / list_strategies()：GUI 辅助
  - build_shortcut_ps() / create_shortcut()：桌面快捷方式（PowerShell COM）
  - print_dry_run()：--dry-run 自检（打印解析出的命令，不弹窗，CI/无头可用）

子进程约定（对齐 docs/开发环境.md）：
  - 统一用**控制台解释器** python.exe（pythonw 子进程无 stdout，print 会挂）
  - 环境变量强制 PYTHONIOENCODING=utf-8（Windows 管道默认 GBK，父进程 decode 会崩）
    与 PYTHONUNBUFFERED=1（逐行即时输出）；PYTHONPATH=src
  - GUI 侧用 CREATE_NO_WINDOW 不闪窗、PIPE 流式捕获；qa REPL 用 CREATE_NEW_CONSOLE
"""

from __future__ import annotations

import base64
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from daily_review.config import PROJECT_ROOT

# 项目固定解释器（docs/开发环境.md 唯一允许；可用 DAILY_REVIEW_PY 覆盖，供测试/他机注入）
DEFAULT_INTERPRETER = r"E:\conda_envs\envs\mowan_dm\python.exe"

# 根目录入口脚本（桌面快捷方式 Arguments 指向它：自插入 src 到 sys.path）
BOOTSTRAP_FILE = "launcher.py"
# 桌面快捷方式文件名（用户可见，中文）
SHORTCUT_NAME = "每日复盘.lnk"

_DATE_RE = re.compile(r"^\d{8}$")


class LauncherError(Exception):
    """启动器配置/环境错误（解释器缺失等），GUI 用 messagebox 展示。"""


# ---------------------------------------------------------------- 环境解析


def _interpreter_candidates() -> list[str]:
    """按优先级给出候选解释器：DAILY_REVIEW_PY 覆盖 → sys.executable（pythonw→兄弟 python.exe）。"""
    override = os.environ.get("DAILY_REVIEW_PY")
    if override:
        return [str(Path(override).resolve())]
    exe = Path(sys.executable).resolve()
    if exe.name.lower().startswith("pythonw"):
        alt = exe.with_name(exe.name[:-5] + ".exe")  # pythonw.exe → python.exe（[:-5] 剥掉 w.exe）
        return [str(alt), str(exe)]
    return [str(exe)]


def resolve_interpreter() -> str:
    """返回用于子进程的控制台解释器路径（第一个存在者；无则报清晰错误）。"""
    for cand in _interpreter_candidates():
        if Path(cand).exists():
            return cand
    raise LauncherError(
        "未找到可用的控制台解释器。\n"
        f"检查项: {os.environ.get('DAILY_REVIEW_PY') or sys.executable}\n"
        "请确认 mowan_dm conda 环境已创建（见 docs/开发环境.md）"
    )


def _pythonw_of(interpreter: str) -> str:
    """python.exe → 兄弟 pythonw.exe（GUI 无控制台闪窗）；不存在则原样返回。"""
    p = Path(interpreter)
    if p.name.lower().startswith("pythonw"):
        return str(p)
    alt = p.with_name(p.name[:-4] + "w.exe")
    return str(alt) if alt.exists() else str(p)


def resolve_runtime() -> dict:
    """返回子进程运行参数：{interpreter, root, env}。env 已含 PYTHONPATH/PYTHONIOENCODING 等。"""
    interp = resolve_interpreter()
    root = Path(os.environ.get("DAILY_REVIEW_ROOT") or PROJECT_ROOT).resolve()
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "src")
    env["PYTHONIOENCODING"] = "utf-8"   # 子进程管道输出强制 UTF-8（Windows 默认 GBK）
    env["PYTHONUNBUFFERED"] = "1"       # 逐行即时输出
    return {"interpreter": interp, "root": str(root), "env": env}


# ---------------------------------------------------------------- argv 构造


def _base(runtime: dict) -> list[str]:
    return [runtime["interpreter"], "-m", "daily_review"]


def build_review_argv(runtime: dict, *, date: str = "", no_llm: bool = False,
                      strategy: str = "") -> list[str]:
    argv = _base(runtime) + ["review"]
    if date:
        argv += ["--date", date]
    if no_llm:
        argv.append("--no-llm")
    if strategy:
        argv += ["--strategy", strategy]
    return argv


def build_dashboard_argv(runtime: dict, *, date: str = "", no_llm: bool = False,
                         open_: bool = True, days: int = 10) -> list[str]:
    argv = _base(runtime) + ["dashboard"]
    if date:
        argv += ["--date", date]
    argv += ["--days", str(max(2, min(int(days), 30)))]  # 钳位到 [2,30]（对齐 GUI spinbox 范围）
    if no_llm:
        argv.append("--no-llm")
    if open_:
        argv.append("--open")
    return argv


def build_web_argv(runtime: dict, *, open_: bool = True, port: int = 5000, host: str = "127.0.0.1") -> list[str]:
    port = max(1, min(int(port), 65535))
    argv = _base(runtime) + ["web", "--host", host, "--port", str(port)]
    if host == "0.0.0.0":
        argv.append("--lan")
    if open_:
        argv.append("--open")
    return argv


def build_qa_argv(runtime: dict, *, date: str = "") -> list[str]:
    argv = _base(runtime) + ["qa"]
    if date:
        argv += ["--date", date]
    return argv


def build_all_commands(runtime: dict, *, date: str = "", no_llm: bool = False,
                       days: int = 10, port: int = 5000, open_: bool = True) -> list[dict]:
    """四种工具的完整命令清单（--dry-run 与测试共用）。"""
    return [
        {"tool": "web", "desc": "Web 工作台（Flask，常驻服务；用「停止」关闭）",
         "argv": build_web_argv(runtime, open_=open_, port=port)},
        {"tool": "review", "desc": "端到端复盘（采集→指标→LLM 报告）",
         "argv": build_review_argv(runtime, date=date, no_llm=no_llm)},
        {"tool": "dashboard", "desc": "数据看板（近 N 日趋势，生成后自动打开）",
         "argv": build_dashboard_argv(runtime, date=date, no_llm=no_llm, open_=open_, days=days)},
        {"tool": "qa", "desc": "交互问答（新控制台 REPL）",
         "argv": build_qa_argv(runtime, date=date)},
    ]


# ---------------------------------------------------------------- 输入校验 / 探测


def validate_date(s: str) -> bool:
    """日期校验：空串=自动探测（True）；否则必须 YYYYMMDD 且是真实存在的日历日期。

    用 datetime.strptime 拒掉 20260230 / 20260431 之类不存在的日期
    （仅 1<=月<=12、1<=日<=31 会放行不可能日期，进而让 review 跑空数据日）。
    """
    s = s.strip()
    if not s:
        return True
    if not _DATE_RE.fullmatch(s):
        return False
    try:
        datetime.strptime(s, "%Y%m%d")
        return True
    except ValueError:
        return False


def probe_recent_date() -> str:
    """探测最近交易日（网络）。失败返回 ''（GUI 留空→CLI 缺省自动探测）。"""
    try:
        from datetime import datetime

        from daily_review.data import eastmoney_pool

        today = datetime.today().strftime("%Y%m%d")
        dates = eastmoney_pool.resolve_recent_trade_dates(today, n_days=1)
        return dates[0] if dates else today
    except Exception:  # noqa: BLE001 —— 探测失败不影响启动器，交给 CLI 缺省
        return ""


def list_strategies() -> list[dict]:
    """个人战法清单（GUI 战法下拉；读取失败返回空表不影响启动器）。"""
    try:
        from daily_review.web.strategy import iter_all

        return [{"id": p.id, "name": p.name, "status": p.status} for p in iter_all()]
    except Exception:  # noqa: BLE001
        return []


# ---------------------------------------------------------------- 桌面快捷方式


def _ps_quote(s: str) -> str:
    """PowerShell 单引号字符串字面量（内部单引号翻倍；反斜杠原样）。"""
    return "'" + s.replace("'", "''") + "'"


def build_shortcut_ps(runtime: dict, lnk_name: str = SHORTCUT_NAME) -> list[str]:
    """构造创建桌面快捷方式的 powershell 命令（-EncodedCommand UTF-16LE base64，最稳）。

    快捷方式指向根目录 launcher.py（pythonw 运行，自插入 src），WorkingDirectory=项目根，
    无控制台闪窗；.lnk 不能设环境变量，所以不依赖 pip install -e。
    """
    target = _pythonw_of(runtime["interpreter"])
    args = str(Path(runtime["root"]) / BOOTSTRAP_FILE)
    script = (
        "$d=[Environment]::GetFolderPath('Desktop');"
        f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d {_ps_quote(lnk_name)}));"
        f"$s.TargetPath={_ps_quote(target)};"
        f"$s.Arguments={_ps_quote('"' + args + '"')};"
        f"$s.WorkingDirectory={_ps_quote(runtime['root'])};"
        f"$s.IconLocation={_ps_quote(target + ',0')};"
        "$s.Save()"
    )
    enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    # 注意：不能写成 -Command -EncodedCommand（-Command 会把 -EncodedCommand 当命令字符串吞掉）
    return ["powershell", "-NoProfile", "-EncodedCommand", enc]


def create_shortcut(runtime: dict, lnk_name: str = SHORTCUT_NAME) -> str:
    """在桌面创建快捷方式（PowerShell COM）；返回 lnk 文件名。"""
    subprocess.run(
        build_shortcut_ps(runtime, lnk_name),
        check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return lnk_name


# ---------------------------------------------------------------- 自检（--dry-run）


def _safe_print(text: str) -> None:
    """GBK 控制台安全打印（不可编码字符替换为 ?，不抛 UnicodeEncodeError）。"""
    enc = sys.stdout.encoding or "utf-8"
    try:
        text.encode(enc)
    except UnicodeEncodeError:
        text = text.encode(enc, "replace").decode(enc)
    print(text)


def print_dry_run(*, date: str = "", no_llm: bool = False,
                  days: int = 10, port: int = 5000) -> int:
    """自检：打印解析出的运行环境与四种工具命令，不打开窗口（可无头/CI 断言）。"""
    try:
        runtime = resolve_runtime()
    except LauncherError as exc:
        _safe_print(str(exc))
        return 1
    _safe_print("每日复盘 · 启动器 自检")
    _safe_print(f"解释器 : {runtime['interpreter']}")
    _safe_print(f"项目目录: {runtime['root']}")
    _safe_print(f"PYTHONPATH: {runtime['env']['PYTHONPATH']}")
    for entry in build_all_commands(runtime, date=date, no_llm=no_llm, days=days, port=port):
        _safe_print(f"[{entry['tool']}] {entry['desc']}")
        _safe_print("  " + " ".join(shlex.quote(a) for a in entry["argv"]))
    _safe_print("桌面快捷方式: " + SHORTCUT_NAME)
    return 0


# ---------------------------------------------------------------- GUI 桥


def _redirect_stdio_to_log() -> None:
    """pythonw 下 sys.stdout/stderr 为 None，重定向到临时日志，避免 import 期 print 崩溃。"""
    try:
        fh = open(Path(os.environ.get("TEMP", ".")) / "daily_review_launcher.log", "a", encoding="utf-8")
    except Exception:  # noqa: BLE001
        return
    if sys.stdout is None:
        sys.stdout = fh
    if sys.stderr is None:
        sys.stderr = fh


def _report_fatal(exc: Exception) -> int:
    """启动失败兜底：写日志 + 尽力弹 messagebox（pythonw 无控制台，这是唯一可见渠道）。"""
    log_path = Path(os.environ.get("TEMP", ".")) / "daily_review_launcher.log"
    try:
        import traceback

        log_path.write_text("每日复盘启动器启动失败:\n" + traceback.format_exc(), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    try:
        import tkinter as tk
        from tkinter import messagebox

        r = tk.Tk()
        r.withdraw()
        messagebox.showerror(
            "每日复盘 · 启动器",
            f"启动失败：{type(exc).__name__}: {exc}\n详情见 {log_path}",
        )
        r.destroy()
    except Exception:  # noqa: BLE001 —— 无显示环境，只能落日志
        pass
    return 1


def run_gui(*, dry_run: bool = False, date: str = "", no_llm: bool = False,
            days: int = 10, port: int = 5000) -> int:
    """入口：--dry-run 打印自检；否则惰性打开 tkinter 窗口（失败兜底 messagebox）。"""
    if sys.stdout is None or sys.stderr is None:
        _redirect_stdio_to_log()
    if dry_run:
        return print_dry_run(date=date, no_llm=no_llm, days=days, port=port)
    try:
        from daily_review.launcher_gui import main_gui

        return main_gui()
    except Exception as exc:  # noqa: BLE001
        return _report_fatal(exc)
