"""每日复盘 · 图形启动器（tkinter 窗口，stdlib 零依赖）。

这是唯一 import tkinter 的文件；纯核心逻辑在 daily_review.launcher（可离线单测）。

线程模型（tkinter 非线程安全）：
  - worker 线程绝不碰控件，只把消息放进有界 queue.Queue
  - 主线程 widget.after(100) 轮询队列，是唯一改控件的地方
  - 队列消息协议（元组首元素 = 类型）：
      ("__DONE__", proc, code)  子进程结束；仅当 proc 是当前 self._proc 才处理（陈旧丢弃）
      ("__DATE__", d)           最近交易日探测结果 → 回填日期框
      ("__SHORTCUT__", ok, msg) 桌面快捷方式创建结果 → 日志 + messagebox
      其它（字符串）            子进程 stdout 一行 → 追加日志

子进程：
  - web / review / dashboard → 控制台解释器 python.exe + CREATE_NO_WINDOW（不闪窗）
    + PIPE 流式输出（text/utf-8/errors=replace）
  - qa（交互 REPL，需要真实 stdin）→ CREATE_NEW_CONSOLE 开新控制台
  - 「停止」对常驻 Web 服务终止整个进程树（taskkill /T /F 兜底）
"""

from __future__ import annotations

import queue
import subprocess
import threading

import tkinter as tk
from tkinter import messagebox, ttk

from daily_review import launcher

# 日志面板最多保留的行数（超出从顶部裁剪，防常驻 Web 服务日志无限膨胀）
MAX_LOG_LINES = 2000


class LauncherApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("每日复盘 · 启动器")
        self.geometry("880x640")
        self.minsize(700, 520)

        self.runtime = launcher.resolve_runtime()
        self._proc: subprocess.Popen | None = None
        self._running_label = ""
        self._stopping = False  # 用户主动停止 → _on_done 显示「任务已停止」而非「出错」
        # 有界队列：子进程输出超过主线程消费速度时阻塞（背压），内存有界
        self._queue: queue.Queue = queue.Queue(maxsize=2048)
        self._strategies: list[dict] = []

        self.date_var = tk.StringVar()
        self.no_llm_var = tk.BooleanVar(value=False)
        self.days_var = tk.IntVar(value=10)
        self.port_var = tk.IntVar(value=5000)
        self.strategy_var = tk.StringVar()

        self._build_ui()
        self._log(f"解释器 : {self.runtime['interpreter']}")
        self._log(f"项目目录: {self.runtime['root']}")
        self._log("就绪。选功能运行，输出实时显示；Web 工作台是常驻服务，用「停止」关闭。")
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll)
        # 后台探测最近交易日（网络），成功后回填日期框；失败留空→CLI 缺省
        threading.Thread(target=self._probe_background, daemon=True).start()

    # ------------------------------------------------------------ UI

    def _build_ui(self):
        header = ttk.Label(self, text="每日复盘 · 启动器", font=("Microsoft YaHei UI", 15, "bold"))
        header.pack(anchor="w", padx=12, pady=(12, 2))
        ttk.Label(self, text="一键启动各项功能；输出实时显示在下方面板。",
                  foreground="#666666").pack(anchor="w", padx=12)

        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=12, pady=(10, 6))
        self._btn_web = ttk.Button(btns, text="Web 工作台", command=self._on_web)
        self._btn_review = ttk.Button(btns, text="跑复盘", command=self._on_review)
        self._btn_dash = ttk.Button(btns, text="数据看板", command=self._on_dashboard)
        self._btn_qa = ttk.Button(btns, text="交互问答", command=self._on_qa)
        for i, b in enumerate((self._btn_web, self._btn_review, self._btn_dash, self._btn_qa)):
            b.grid(row=0, column=i, padx=4, sticky="ew", ipadx=12, ipady=8)
            btns.columnconfigure(i, weight=1)

        opts = ttk.LabelFrame(self, text="选项")
        opts.pack(fill="x", padx=12, pady=4)
        ttk.Label(opts, text="日期(YYYYMMDD)").grid(row=0, column=0, sticky="w", padx=(8, 2), pady=6)
        ttk.Entry(opts, textvariable=self.date_var, width=12).grid(row=0, column=1, padx=2)
        ttk.Checkbutton(opts, text="无 LLM（省 token）", variable=self.no_llm_var).grid(row=0, column=2, padx=12)
        ttk.Label(opts, text="天数").grid(row=0, column=3, padx=(4, 2))
        ttk.Spinbox(opts, from_=2, to=30, textvariable=self.days_var, width=5).grid(row=0, column=4)
        ttk.Label(opts, text="端口").grid(row=0, column=5, padx=(12, 2))
        ttk.Entry(opts, textvariable=self.port_var, width=7).grid(row=0, column=6)
        ttk.Label(opts, text="战法").grid(row=0, column=7, padx=(12, 2))
        self._strategy_box = ttk.Combobox(opts, textvariable=self.strategy_var,
                                          state="readonly", width=18)
        self._strategy_box.grid(row=0, column=8, padx=(2, 8), pady=6)
        opts.columnconfigure(9, weight=1)
        self._refresh_strategies()

        ctl = ttk.Frame(self)
        ctl.pack(fill="x", padx=12, pady=6)
        self._btn_stop = ttk.Button(ctl, text="停止", command=self._on_stop, state="disabled")
        self._btn_stop.pack(side="left", padx=(0, 8))
        ttk.Button(ctl, text="清空日志", command=self._clear_log).pack(side="left", padx=8)
        ttk.Button(ctl, text="创建桌面快捷方式", command=self._on_shortcut).pack(side="left", padx=8)

        logf = ttk.LabelFrame(self, text="日志")
        logf.pack(fill="both", expand=True, padx=12, pady=(4, 10))
        self._text = tk.Text(logf, state="disabled", wrap="char",
                             font=("Consolas", 10), bg="#f6f8fa", fg="#24292f",
                             insertbackground="#24292f")
        sb = ttk.Scrollbar(logf, command=self._text.yview)
        self._text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._text.pack(side="left", fill="both", expand=True)

        self._status = ttk.Label(self, text="空闲", anchor="w")
        self._status.pack(fill="x", side="bottom", padx=12, pady=(0, 8))

    def _refresh_strategies(self):
        self._strategies = launcher.list_strategies()
        names = ["（通用预案）"] + [
            ("★" if s["status"] == "active" else "  ") + s["name"] for s in self._strategies
        ]
        self._strategy_box["values"] = names
        self._strategy_box.current(0)

    def _selected_strategy_id(self) -> str:
        name = self.strategy_var.get()
        if not name or name == "（通用预案）":
            return ""
        for s in self._strategies:
            if ("★" if s["status"] == "active" else "  ") + s["name"] == name:
                return s["id"]
        return ""

    # ------------------------------------------------------------ 日志

    def _append(self, line: str):
        self._text.configure(state="normal")
        self._text.insert("end", line.rstrip("\n") + "\n")
        self._text.see("end")
        self._text.configure(state="disabled")
        self._trim_log()

    def _trim_log(self):
        """日志超 MAX_LOG_LINES 行则从顶部裁剪，防常驻服务日志无限膨胀。"""
        try:
            lines = int(self._text.index("end-1c").split(".")[0])
            if lines > MAX_LOG_LINES:
                self._text.configure(state="normal")
                self._text.delete("1.0", f"{lines - MAX_LOG_LINES}.0")
                self._text.configure(state="disabled")
        except tk.TclError:
            pass

    def _log(self, text: str):
        self._append(text)

    def _clear_log(self):
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        self._text.configure(state="disabled")

    # ------------------------------------------------------------ 动作

    def _check_date(self) -> bool:
        d = self.date_var.get().strip()
        if d and not launcher.validate_date(d):
            messagebox.showerror(
                "日期无效",
                f"「{d}」不是有效日期。请填 YYYYMMDD（如 20260810）；留空=自动探测最近交易日。",
            )
            return False
        return True

    def _on_web(self):
        try:
            port = int(self.port_var.get())
        except (ValueError, tk.TclError):
            port = 5000
        argv = launcher.build_web_argv(self.runtime, open_=True, port=port)
        self._start(argv, "Web 工作台")

    def _on_review(self):
        if not self._check_date():
            return
        argv = launcher.build_review_argv(
            self.runtime,
            date=self.date_var.get().strip(),
            no_llm=self.no_llm_var.get(),
            strategy=self._selected_strategy_id(),
        )
        self._start(argv, "跑复盘")

    def _on_dashboard(self):
        if not self._check_date():
            return
        try:
            days = int(self.days_var.get())
        except (ValueError, tk.TclError):
            days = 10
        argv = launcher.build_dashboard_argv(
            self.runtime,
            date=self.date_var.get().strip(),
            no_llm=self.no_llm_var.get(),
            open_=True,
            days=days,
        )
        self._start(argv, "数据看板")

    def _on_qa(self):
        argv = launcher.build_qa_argv(self.runtime, date=self.date_var.get().strip())
        self._start(argv, "交互问答", new_console=True)

    # ------------------------------------------------------------ 子进程

    def _start(self, argv: list[str], label: str, *, new_console: bool = False):
        if self._proc is not None and self._proc.poll() is None:
            messagebox.showwarning("已有任务", f"「{self._running_label}」正在运行，请先停止。")
            return
        self._stopping = False
        self._log("▶ " + label + "  →  " + " ".join(argv))
        self._set_busy(True, label)
        self._status.configure(text="运行中：" + label)
        try:
            if new_console:
                flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
                self._proc = subprocess.Popen(
                    argv, cwd=self.runtime["root"], env=self.runtime["env"],
                    creationflags=flags,
                )
                self._log("（已在新的控制台窗口打开交互问答，输入 exit 退出；完成后可关闭该窗口）")
                self._watch_exit()
            else:
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                self._proc = subprocess.Popen(
                    argv, cwd=self.runtime["root"], env=self.runtime["env"],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", bufsize=1,
                    creationflags=flags,
                )
                threading.Thread(target=self._reader, args=(self._proc,), daemon=True).start()
        except Exception as exc:  # noqa: BLE001
            self._log(f"[启动失败] {type(exc).__name__}: {exc}")
            self._set_busy(False, "")

    def _reader(self, proc: subprocess.Popen):
        try:
            for line in proc.stdout:
                self._queue.put(line)
        except Exception as exc:  # noqa: BLE001
            self._queue.put(f"[读取输出失败] {type(exc).__name__}: {exc}\n")
        proc.wait()
        # 带进程身份（类型在 index 0）：_poll 只认当前 self._proc 的结束消息，陈旧任务丢弃
        self._queue.put(("__DONE__", proc, proc.returncode))

    def _watch_exit(self):
        # CREATE_NEW_CONSOLE 任务无 PIPE，轮询退出码
        if self._proc is None:
            return
        if self._proc.poll() is not None:
            self._on_done(self._proc.returncode)
            self._proc = None
            return
        self.after(300, self._watch_exit)

    def _poll(self):
        # 主线程唯一改控件处：drain 队列 → 日志 / 状态
        try:
            while True:
                item = self._queue.get_nowait()
                if item is None:
                    continue
                if isinstance(item, tuple):
                    kind = item[0]
                    if kind == "__DONE__" and len(item) == 3:
                        proc, code = item[1], item[2]
                        if proc is not self._proc:
                            continue  # 陈旧任务（被停止/已结束）的结束消息，丢弃
                        self._on_done(code)
                        self._proc = None
                    elif kind == "__DATE__" and len(item) == 2:
                        if item[1]:
                            self.date_var.set(item[1])
                    elif kind == "__SHORTCUT__" and len(item) == 3:
                        ok, msg = item[1], item[2]
                        self._log(msg)
                        if ok:
                            messagebox.showinfo("完成", msg)
                        else:
                            messagebox.showerror("失败", msg)
                    else:
                        self._append(str(item))
                else:
                    self._append(str(item))
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _on_done(self, code: int):
        self._set_busy(False, "")
        self._status.configure(text="空闲")
        if self._stopping:
            # 主动停止：Windows terminate() 退出码恒为 1，不算失败
            self._log("—— 任务已停止。")
        elif code == 0:
            self._log("—— 任务完成（退出码 0）。")
        else:
            self._log(f"—— 任务出错，退出码 {code}（详情见上方日志）。")

    def _set_busy(self, busy: bool, label: str):
        self._running_label = label
        state = "disabled" if busy else "normal"
        for b in (self._btn_web, self._btn_review, self._btn_dash, self._btn_qa):
            b.configure(state=state)
        self._btn_stop.configure(state="normal" if busy else "disabled")

    # ------------------------------------------------------------ 停止 / 退出 / 快捷方式

    def _kill_proc(self, p: subprocess.Popen):
        try:
            p.terminate()  # Windows 上 terminate==kill（TerminateProcess）
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                subprocess.run(
                    ["taskkill", "/PID", str(p.pid), "/T", "/F"],
                    capture_output=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
        except Exception as exc:  # noqa: BLE001
            self._log(f"[停止失败] {type(exc).__name__}: {exc}")

    def _on_stop(self):
        p = self._proc
        if p is None or p.poll() is not None:
            return
        if not messagebox.askyesno("停止", f"确定停止「{self._running_label}」吗？"):
            return
        self._stopping = True  # 主动停止 → _on_done 显示「任务已停止」而非「出错」
        self._log("正在停止当前任务…")
        self._kill_proc(p)

    def _on_close(self):
        if self._proc is not None and self._proc.poll() is None:
            self._kill_proc(self._proc)
        self.destroy()

    def _on_shortcut(self):
        # PowerShell COM 创建 .lnk 放后台线程，结果经队列回主线程（不冻结窗口）
        self._log("正在创建桌面快捷方式…")
        threading.Thread(target=self._shortcut_worker, daemon=True).start()

    def _shortcut_worker(self):
        try:
            name = launcher.create_shortcut(self.runtime)
            msg = f"已在桌面创建快捷方式「{name}」。\n双击即可打开每日复盘启动器。"
            self._queue.put(("__SHORTCUT__", True, msg))
        except Exception as exc:  # noqa: BLE001
            msg = (f"创建桌面快捷方式失败：{type(exc).__name__}: {exc}\n"
                   "请手动双击项目根目录的 启动.bat。")
            self._queue.put(("__SHORTCUT__", False, msg))

    # ------------------------------------------------------------ 后台

    def _probe_background(self):
        # 只把结果放进队列（工作线程绝不碰控件）；失败留空 → CLI 缺省自动探测
        d = launcher.probe_recent_date()
        self._queue.put(("__DATE__", d))


def main_gui() -> int:
    LauncherApp().mainloop()
    return 0
