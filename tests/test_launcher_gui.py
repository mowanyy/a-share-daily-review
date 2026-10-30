"""启动器 GUI 壳测试：不创建真实 Tk 窗口，用 LauncherApp.__new__ + 假控件直测核心逻辑。

覆盖对抗性审查确认的缺陷修复：
  - 陈旧 __DONE__（被停止/结束的旧任务）在新任务运行中到达时被丢弃，不误清 busy
  - 当前任务的 __DONE__ → _on_done 复位 busy + self._proc 置 None（消除死引用）
  - 主动停止 → 显示「任务已停止」而非「任务出错」（Windows terminate 退出码恒为 1）
  - __DATE__ 探测结果经队列回主线程回填日期框（工作线程不碰控件）
  - _start 重置 _stopping 标志
"""

from __future__ import annotations

import queue

from daily_review.launcher_gui import LauncherApp


class _Var:
    """简化 tkinter StringVar：仅记录 set/get。"""

    def __init__(self):
        self.value = None

    def set(self, v):
        self.value = v

    def get(self):
        return self.value


class _Widget:
    """简化控件：configure(state=/text=) 只记录状态。"""

    def __init__(self):
        self.state = "normal"
        self.text = ""

    def configure(self, **kw):
        self.state = kw.get("state", self.state)
        self.text = kw.get("text", self.text)


class _Text(_Widget):
    """简化 tk.Text：append 行、index 返回行数（触发/不触发裁剪都安全）。"""

    def __init__(self):
        super().__init__()
        self.lines = []

    def insert(self, idx, s):
        self.lines.extend(s.rstrip("\n").split("\n"))

    def see(self, idx):
        pass

    def delete(self, a, b):
        self.deleted = (a, b)

    def index(self, idx):
        return f"{len(self.lines) + 1}.0"


class _FakeProc:
    def __init__(self, rc=0):
        self.returncode = rc

    def poll(self):
        return None  # 视为仍在运行

    def wait(self):
        pass


def _make_app():
    app = LauncherApp.__new__(LauncherApp)
    app.runtime = {"root": ".", "env": {}}
    app._queue = queue.Queue()
    app._proc = None
    app._stopping = False
    app._running_label = ""
    app.date_var = _Var()
    app._text = _Text()
    app._status = _Widget()
    app._btn_web = _Widget()
    app._btn_review = _Widget()
    app._btn_dash = _Widget()
    app._btn_qa = _Widget()
    app._btn_stop = _Widget()
    app.after = lambda *a, **k: None  # 不调度 _poll 递归
    return app


def test_poll_drops_stale_done_of_old_task():
    # 场景：旧任务 A 被停止/结束，随后启动任务 B（当前 _proc）；A 的 __DONE__ 才到达。
    # 带进程身份的 __DONE__ 必须被丢弃——否则会误清 B 的 busy、误报「完成/出错」。
    app = _make_app()
    app._set_busy(True, "跑复盘")
    cur = _FakeProc()   # 当前任务 B
    old = _FakeProc()   # 旧任务 A
    app._proc = cur
    app._queue.put(("__DONE__", old, 1))
    app._poll()
    assert app._proc is cur                 # 不被陈旧消息清掉
    assert app._btn_web.state == "disabled"  # busy 保持
    assert not any("任务完成" in ln or "任务出错" in ln for ln in app._text.lines)


def test_poll_handles_done_of_current_proc():
    # 当前任务正常结束 → _on_done 复位 busy、self._proc 置 None（消除死引用）。
    app = _make_app()
    app._set_busy(True, "跑复盘")
    cur = _FakeProc(rc=0)
    app._proc = cur
    app._queue.put(("__DONE__", cur, 0))
    app._poll()
    assert app._proc is None
    assert app._btn_web.state == "normal"
    assert app._status.text == "空闲"
    assert any("任务完成" in ln for ln in app._text.lines)


def test_on_done_stopped_shows_stopped_not_error():
    # 主动停止：Windows terminate() 退出码恒为 1，不得显示「任务出错」。
    app = _make_app()
    app._set_busy(True, "Web 工作台")
    app._stopping = True
    app._on_done(1)
    assert any("任务已停止" in ln for ln in app._text.lines)
    assert not any("任务出错" in ln for ln in app._text.lines)
    assert app._btn_web.state == "normal"


def test_poll_date_fills_date_var():
    # __DATE__ 探测结果经队列回主线程回填日期框（工作线程不碰控件）。
    app = _make_app()
    app._queue.put(("__DATE__", "20260810"))
    app._poll()
    assert app.date_var.get() == "20260810"
    # 空结果（探测失败）不改动日期框
    app._queue.put(("__DATE__", ""))
    app._poll()
    assert app.date_var.get() == "20260810"


def test_start_resets_stopping_flag(monkeypatch):
    # 新任务启动必须重置 _stopping，否则上一个任务的「停止」状态污染本次结束消息。
    app = _make_app()
    app._stopping = True

    class _Popen:
        def __init__(self, *a, **k):
            self.returncode = None
            self.stdout = []  # 空输出 → reader 立即 EOF

        def poll(self):
            return None

        def wait(self):
            self.returncode = 0
            return 0

    monkeypatch.setattr("daily_review.launcher_gui.subprocess.Popen", _Popen)
    app._start(["py", "-m", "daily_review", "review"], "跑复盘")
    assert app._stopping is False
