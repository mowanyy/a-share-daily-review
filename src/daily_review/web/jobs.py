"""后台复盘任务（Web 端）：threading 单飞 + 跨进程文件锁（v0.23）。

支持三种任务类型：
  - review：盘后复盘（collect → compute → generate_report → 七章报告）
  - plan：隔夜预案（collect prev → news → generate_overnight_plan）
  - open：开盘策略（collect prev → auction → generate_open_strategy）

start() 立即返回 JobState；已有任务在跑则抛 JobBusy（路由 → HTTP 409）
线程内：采集 → 指标 → LLM → 渲染
redirect_stdout 捕获 print 进 job.logs（内存 UTF-8，浏览器端安全）
内存最多保留 max_done 个已完成任务

v0.23（A1）：`_running` 只是进程内变量，多 worker（gunicorn workers>1）部署会
穿透——补跨进程文件锁 FileLock（data/jobs.lock），全局单飞跨进程同样生效。
"""

from __future__ import annotations

import io
import threading
import time
import uuid
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path

from daily_review.llm.client import LLMError
from daily_review.web.joblock import FileLock, LockHeld

PLAN_SECTION = "七、次日预案"


class JobBusy(Exception):
    """已有复盘任务在运行。"""


@dataclass
class JobState:
    id: str
    task_type: str = "review"          # review | plan | open
    trade_date: str = ""
    no_llm: bool = False
    strategy_id: str = ""
    strategy_name: str = ""
    status: str = "running"            # running | done | error
    step: str = "排队中"
    progress: int = 0                  # 0-100
    error: str = ""
    logs: list[str] = field(default_factory=list)
    report_html: str = ""              # 全文 HTML
    plan_html: str = ""                 # 次日预案章节 HTML（仅 review 任务）
    days: int = 0                       # 更新天数（仅 data_update 任务）
    force: bool = False                 # 强制刷新（仅 data_update 任务）
    created_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "task_type": self.task_type,
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
    """单飞任务管理器。每个 Flask app 一份（测试隔离）。

    v0.23：进程内 _running + 跨进程文件锁双通道；lock_path 可注入（测试传 tmp 隔离），
    缺省 data/jobs.lock。
    """

    def __init__(self, max_done: int = 20, lock_path: str | Path | None = None):
        self._lock = threading.Lock()
        self._jobs: dict[str, JobState] = {}
        self._running: str | None = None
        self._max_done = max_done
        self._file_lock: FileLock | None = None
        if lock_path is None:
            from daily_review.config import get_settings

            lock_path = get_settings().data_dir / "jobs.lock"
        self._lock_path = Path(lock_path)

    # ---------- 对外 ----------

    def start_review(self, *, trade_date: str, strategy_id: str = "", no_llm: bool = False) -> JobState:
        return self._start(task_type="review", trade_date=trade_date, strategy_id=strategy_id, no_llm=no_llm)

    # 向后兼容：旧代码 jm.start(...) 仍可用
    def start(self, *, trade_date: str, strategy_id: str = "", no_llm: bool = False) -> JobState:
        return self.start_review(trade_date=trade_date, strategy_id=strategy_id, no_llm=no_llm)

    def start_plan(self, *, trade_date: str) -> JobState:
        return self._start(task_type="plan", trade_date=trade_date)

    def start_open(self, *, trade_date: str) -> JobState:
        return self._start(task_type="open", trade_date=trade_date)

    def start_data_update(self, *, trade_date: str, days: int = 6, force: bool = False) -> JobState:
        """数据更新：刷新缓存 + 重采 N 天。"""
        return self._start(task_type="data_update", trade_date=trade_date, days=days, force=force)

    def _start(self, *, task_type: str, trade_date: str, strategy_id: str = "", no_llm: bool = False,
               days: int = 0, force: bool = False) -> JobState:
        with self._lock:
            if self._running is not None:
                running = self._jobs.get(self._running)
                hint = f"{running.task_type}({running.trade_date})" if running else ""
                raise JobBusy(f"已有任务在运行（{hint}），请等待完成")
            job = JobState(
                id=uuid.uuid4().hex[:12],
                task_type=task_type,
                trade_date=trade_date,
                no_llm=no_llm,
                strategy_id=strategy_id,
                days=days,
                force=force,
                created_at=time.time(),
            )
            self._jobs[job.id] = job
            self._running = job.id

        # 跨进程文件锁：进程内检查通过后仍需抢文件锁（多 worker 场景另一个进程可能也在跑）
        try:
            lock = FileLock(self._lock_path)
            lock.acquire()
        except LockHeld as exc:
            with self._lock:
                self._jobs.pop(job.id, None)
                self._running = None
            raise JobBusy(f"已有任务在运行（跨进程锁）：{exc}") from exc
        self._file_lock = lock

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
            self._release_file_lock()

    def _release_file_lock(self) -> None:
        """任务结束（含异常）释放跨进程文件锁；幂等。"""
        lock, self._file_lock = self._file_lock, None
        if lock is not None:
            lock.release()

    def _run_inner(self, job: JobState) -> None:
        from daily_review.web.md import md_to_html, section_html

        try:
            if job.task_type == "review":
                self._run_review(job, md_to_html, section_html)
            elif job.task_type == "plan":
                self._run_plan(job, md_to_html)
            elif job.task_type == "open":
                self._run_open(job, md_to_html)
            elif job.task_type == "data_update":
                self._run_data_update(job)
            else:
                job.error = f"未知任务类型: {job.task_type}"
                self._set(job, status="error")
        except LLMError as exc:
            self._set(job, status="error", error=f"LLM 调用失败：{exc}")
            job.logs.append(f"LLM 调用失败：{exc}")
        except Exception as exc:  # noqa: BLE001 —— 后台任务兜底
            self._set(job, status="error", error=f"{type(exc).__name__}: {exc}")
            job.logs.append(f"任务异常：{type(exc).__name__}: {exc}")

    def _run_review(self, job: JobState, md_to_html, section_html) -> None:
        """盘后复盘：collect → compute → generate_report → 渲染。"""
        from daily_review.llm.reporter import generate_report
        from daily_review.pipeline import collect, compute
        from daily_review.web.strategy import get_strategy

        strategy = None
        if job.strategy_id:
            strategy = get_strategy(job.strategy_id)
            if strategy is None:
                job.strategy_name = "（未找到，按通用预案）"
                job.logs.append(f"战法 {job.strategy_id} 未找到，按通用预案处理")
            else:
                job.strategy_name = strategy.name

        self._set(job, step="采集数据（东财行情）", progress=10)
        collected = collect(job.trade_date)

        self._set(job, step="计算指标（情绪温度/梯队/题材/炸板/龙虎榜）", progress=40)
        indicators = compute(collected)
        # v0.30：完整复盘落盘权威快照（预案/开盘策略引用昨日值不再重算漂移）
        from daily_review.analysis.review_snapshot import save_review_snapshot

        save_review_snapshot(indicators, job.trade_date)

        if job.no_llm:
            job.logs.append("已跳过 LLM 报告（--no-llm）；数据与指标已就绪")
            self._set(job, step="完成（--no-llm）", progress=100, status="done")
            return

        self._set(job, step="LLM 生成复盘报告（DeepSeek）", progress=70)
        md = generate_report(indicators, job.trade_date, strategy=strategy)

        self._set(job, step="渲染报告", progress=95)
        job.report_html = md_to_html(md)
        job.plan_html, found = section_html(md, PLAN_SECTION)
        if not found:
            job.logs.append("（报告中未找到「七、次日预案」章节）")
        job.logs.append("复盘报告已生成")
        self._set(job, step="完成", progress=100, status="done")

    def _run_plan(self, job: JobState, md_to_html) -> None:
        """隔夜预案：collect prev → news → generate_overnight_plan → 渲染。"""
        from daily_review.data.eastmoney_news import fetch_overnight_news
        from daily_review.llm.premarket import generate_overnight_plan
        from daily_review.pipeline import collect, compute

        # 1. 确定前一交易日
        from daily_review.data import eastmoney_pool
        dates = eastmoney_pool.resolve_recent_trade_dates(job.trade_date, n_days=2)
        prev_date = dates[1] if len(dates) > 1 and dates[0] == job.trade_date else dates[0] if dates else job.trade_date
        job.logs.append(f"隔夜预案基准日: {prev_date}，今日: {job.trade_date}")

        self._set(job, step="采集昨日复盘数据（缓存）", progress=10)
        collected = collect(prev_date)
        indicators = compute(collected)

        self._set(job, step="采集隔夜消息（东财7x24快讯）", progress=40)
        news = fetch_overnight_news(job.trade_date)
        job.logs.append(f"隔夜消息 {len(news)} 条")

        self._set(job, step="LLM 生成隔夜预案", progress=70)
        md = generate_overnight_plan(indicators, news, job.trade_date)

        self._set(job, step="渲染", progress=95)
        job.report_html = md_to_html(md)
        job.logs.append("隔夜预案已生成")
        self._set(job, step="完成", progress=100, status="done")

    def _run_open(self, job: JobState, md_to_html) -> None:
        """开盘策略：collect prev → auction → generate_open_strategy → 渲染。"""
        import pandas as pd

        from daily_review.analysis.auction import compute_auction, fetch_auction_data
        from daily_review.config import get_settings
        from daily_review.llm.premarket import generate_open_strategy
        from daily_review.pipeline import collect, compute

        # 1. 确定前一交易日
        from daily_review.data import eastmoney_pool
        dates = eastmoney_pool.resolve_recent_trade_dates(job.trade_date, n_days=2)
        prev_date = dates[1] if len(dates) > 1 and dates[0] == job.trade_date else dates[0] if dates else job.trade_date
        job.logs.append(f"开盘策略基准日: {prev_date}，今日: {job.trade_date}")

        self._set(job, step="采集昨日复盘数据（缓存）", progress=10)
        collected = collect(prev_date)
        indicators = compute(collected)

        # 2. 加载隔夜预案
        plan_text = ""
        plan_path = Path(get_settings().output_dir) / f"{job.trade_date}_隔夜预案.md"
        if plan_path.exists():
            plan_text = plan_path.read_text(encoding="utf-8")
            job.logs.append("已加载隔夜预案")
        else:
            job.logs.append("未找到隔夜预案文件，开盘策略缺少消息面上下文")

        # 3. 竞价数据
        self._set(job, step="采集竞价数据", progress=40)
        zt = collected.get("zt")
        auction_data = []
        if zt is not None and not zt.empty:
            codes = [str(c) for c in zt["code"]]
            job.logs.append(f"竞价采集 {len(codes)} 只涨停股")
            quotes = fetch_auction_data(codes)
            prev_seal_map = {}
            if "fund" in zt.columns:
                for _, r in zt.iterrows():
                    code = str(r["code"])
                    fund = r.get("fund")
                    if fund is not None and pd.notna(fund):
                        prev_seal_map[code] = float(fund)
            auction_data = compute_auction(quotes, prev_seal_map=prev_seal_map)
            ready = [r for r in auction_data if r.get("auction_pct") is not None]
            job.logs.append(f"竞价数据 {len(auction_data)} 条（有竞价价 {len(ready)} 只）")
        else:
            job.logs.append("昨日无涨停股，竞价数据为空")

        self._set(job, step="LLM 生成开盘策略", progress=70)
        md = generate_open_strategy(indicators, auction_data, plan_text, job.trade_date)

        self._set(job, step="渲染", progress=95)
        job.report_html = md_to_html(md)
        job.logs.append("开盘策略已生成")
        self._set(job, step="完成", progress=100, status="done")

    def _run_data_update(self, job: JobState) -> None:
        """数据更新：刷新静态缓存 + 重采 N 天数据。"""
        from daily_review.data import eastmoney_pool as em
        from daily_review.data.local_cache import refresh_all
        from daily_review.pipeline import collect

        days = max(1, job.days or 6)
        job.logs.append(f"数据更新: {job.trade_date}，近 {days} 天")

        # 1. 刷新静态缓存
        self._set(job, step="刷新静态缓存（行业映射/交易日历）", progress=10)

        def _fetch_industry():
            return em.fetch_stock_industry_map()

        def _fetch_trade_dates():
            ds = em.resolve_recent_trade_dates(job.trade_date, n_days=days)
            return set(ds) if ds else set()

        result = refresh_all(_fetch_industry, _fetch_trade_dates, force=job.force)
        if result["industry_map"]:
            job.logs.append("行业映射已刷新")
        if result["trade_dates"]:
            job.logs.append("交易日历已刷新")

        # 2. 重采 N 天数据
        dates = em.resolve_recent_trade_dates(job.trade_date, n_days=days)
        total = len(dates)
        job.logs.append(f"重采 {total} 天数据...")

        for i, d in enumerate(dates):
            pct = 10 + int((i + 1) / total * 80)
            self._set(job, step=f"采集 {d}（{i+1}/{total}）", progress=pct)
            try:
                collected = collect(d)
                zt_count = len(collected.get("zt", []))
                zb_count = len(collected.get("zb", []))
                job.logs.append(f"  {d}: 涨停 {zt_count} / 炸板 {zb_count}")
            except Exception as exc:
                job.logs.append(f"  {d}: 失败 - {exc}")

        job.logs.append("数据更新完成")
        self._set(job, step="完成", progress=100, status="done")

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