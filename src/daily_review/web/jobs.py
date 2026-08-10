"""后台复盘任务（Web 端）：threading 单飞，同时只跑一个。

- start() 立即返回 JobState；已有任务在跑则抛 JobBusy（路由 → HTTP 409）
- 线程内：collect → compute → generate_report(strategy) → Markdown 渲染
- redirect_stdout 捕获 print 进 job.logs（内存 UTF-8，浏览器端安全，不落 GBK 控制台）
- 内存最多保留 max_done 个已完成任务
"""

from __future__ import annotations

import io
import threading
import time
import uuid
from contextlib import redirect_stdout
from dataclasses import dataclass, field

from daily_review.llm.client import LLMError

PLAN_SECTION = "七、次日预案"


class JobBusy(Exception):
    """已有复盘任务在运行。"""


@dataclass
class JobState:
    id: str
    trade_date: str
    no_llm: bool = False
    strategy_id: str = ""
    strategy_name: str = ""
    status: str = "running"          # running | done | error
    step: str = "排队中"
    progress: int = 0                # 0-100
    error: str = ""
    logs: list[str] = field(default_factory=list)
    report_html: str = ""            # 全文 HTML
    plan_html: str = ""              # 次日预案章节 HTML
    created_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "trade_date": self.trade_date,
            "no_llm": self.no_llm,
            "strategy_id": self.strategy_id,
            "strategy_name": self.strategy_name,
            "status": self.status,
            "step": self.step,
            "progress": self.progress,
            "error": self.error,
            "logs": list(self.logs),
            "report_html": self.report_html,
            "plan_html": self.plan_html,
        }


class JobManager:
    """单飞任务管理器。每个 Flask app 一份（测试隔离）。"""

    def __init__(self, max_done: int = 20):
        self._lock = threading.Lock()
        self._jobs: dict[str, JobState] = {}
        self._running: str | None = None
        self._max_done = max_done

    # ---------- 对外 ----------

    def start(self, *, trade_date: str, strategy_id: str = "", no_llm: bool = False) -> JobState:
        with self._lock:
            if self._running is not None:
                running = self._jobs.get(self._running)
                hint = running.trade_date if running else ""
                raise JobBusy(f"已有复盘任务在运行（{hint}），请等待完成")
            job = JobState(
                id=uuid.uuid4().hex[:12],
                trade_date=trade_date,
                no_llm=no_llm,
                strategy_id=strategy_id,
                created_at=time.time(),
            )
            self._jobs[job.id] = job
            self._running = job.id
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    def status(self, job_id: str) -> JobState | None:
        return self._jobs.get(job_id)

    def list(self) -> list[JobState]:
        return [j for j in self._jobs.values()]

    # ---------- 内部 ----------

    def _run(self, job: JobState) -> None:
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                self._run_inner(job)
        finally:
            tail = buf.getvalue().strip()
            if tail:
                job.logs.append(tail)
            with self._lock:
                self._running = None
                self._prune()

    def _run_inner(self, job: JobState) -> None:
        from daily_review.web.md import md_to_html, section_html
        from daily_review.web.strategy import get_strategy

        try:
            strategy = None
            if job.strategy_id:
                strategy = get_strategy(job.strategy_id)
                if strategy is None:
                    job.strategy_name = "（未找到，按通用预案）"
                    job.logs.append(f"战法 {job.strategy_id} 未找到，按通用预案处理")
                else:
                    job.strategy_name = strategy.name

            self._set(job, step="采集数据（东财行情）", progress=10)
            from daily_review.pipeline import collect, compute

            collected = collect(job.trade_date)

            self._set(job, step="计算指标（情绪温度/梯队/题材/炸板/龙虎榜）", progress=40)
            indicators = compute(collected)

            if job.no_llm:
                job.logs.append("已跳过 LLM 报告（--no-llm）；数据与指标已就绪")
                self._set(job, step="完成（--no-llm）", progress=100, status="done")
                return

            self._set(job, step="LLM 生成复盘报告（DeepSeek）", progress=70)
            from daily_review.llm.reporter import generate_report

            md = generate_report(indicators, job.trade_date, strategy=strategy)

            self._set(job, step="渲染报告", progress=95)
            job.report_html = md_to_html(md)
            job.plan_html, found = section_html(md, PLAN_SECTION)
            if not found:
                job.logs.append("（报告中未找到「七、次日预案」章节）")
            job.logs.append("复盘报告已生成")
            self._set(job, step="完成", progress=100, status="done")
        except LLMError as exc:
            self._set(job, status="error", error=f"LLM 调用失败：{exc}")
            job.logs.append(f"LLM 调用失败：{exc}")
        except Exception as exc:  # noqa: BLE001 —— 后台任务兜底，错误进 job.error 供前端展示
            self._set(job, status="error", error=f"{type(exc).__name__}: {exc}")
            job.logs.append(f"任务异常：{type(exc).__name__}: {exc}")

    def _set(self, job: JobState, *, status: str | None = None, step: str | None = None,
             progress: int | None = None, error: str | None = None) -> None:
        if status is not None:
            job.status = status
        if step is not None:
            job.step = step
        if progress is not None:
            job.progress = progress
        if error is not None:
            job.error = error

    def _prune(self) -> None:
        done = [j for j in self._jobs.values() if j.status in ("done", "error")]
        if len(done) > self._max_done:
            done.sort(key=lambda j: j.created_at)
            for j in done[: len(done) - self._max_done]:
                self._jobs.pop(j.id, None)
