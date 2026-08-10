"""web/jobs.py 后台复盘任务测试：单飞 / 进度 / 渲染 / 错误 / 清理（全离线）。"""

from __future__ import annotations

import threading
import time

import pytest

from daily_review.web.jobs import JobBusy, JobManager


@pytest.fixture
def fake_pipeline(monkeypatch):
    """monkeypatch collect/compute/generate_report。"""
    import daily_review.llm.reporter as reporter_mod
    import daily_review.pipeline as pipeline_mod

    state = {"report_md": "## 七、次日预案\n计划内容", "llm_calls": 0}

    def fake_collect(trade_date, n_days=10):
        return {"date": trade_date}

    def fake_compute(collected):
        return {"emotion": {"available": False}, "ladder": {}}

    def fake_generate(indicators, trade_date, *, api_key=None, out_path=None, strategy=None):
        state["llm_calls"] += 1
        return state["report_md"]

    monkeypatch.setattr(pipeline_mod, "collect", fake_collect)
    monkeypatch.setattr(pipeline_mod, "compute", fake_compute)
    monkeypatch.setattr(reporter_mod, "generate_report", fake_generate)
    return state


def _wait_done(jm, job_id, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = jm.status(job_id)
        if j is not None and j.status in ("done", "error"):
            return j
        time.sleep(0.02)
    raise AssertionError("job 超时未完成")


def test_job_done_with_report_and_plan(fake_pipeline):
    jm = JobManager()
    job = jm.start(trade_date="20260806")
    done = _wait_done(jm, job.id)
    assert done.status == "done"
    assert done.progress == 100
    assert "<h2>七、次日预案</h2>" in done.report_html
    assert done.plan_html and "计划内容" in done.plan_html
    assert fake_pipeline["llm_calls"] == 1


def test_no_llm_skips_report(fake_pipeline):
    jm = JobManager()
    job = jm.start(trade_date="20260806", no_llm=True)
    done = _wait_done(jm, job.id)
    assert done.status == "done"
    assert fake_pipeline["llm_calls"] == 0
    assert done.report_html == ""


def test_job_busy_single_flight(fake_pipeline, monkeypatch):
    import daily_review.pipeline as pipeline_mod

    gate = threading.Event()

    def slow_collect(trade_date, n_days=10):
        gate.wait(5)
        return {"date": trade_date}

    monkeypatch.setattr(pipeline_mod, "collect", slow_collect)
    jm = JobManager()
    job1 = jm.start(trade_date="20260806")
    with pytest.raises(JobBusy):
        jm.start(trade_date="20260807")
    gate.set()
    assert _wait_done(jm, job1.id).status == "done"
    # 释放后可再跑
    job3 = jm.start(trade_date="20260807", no_llm=True)
    assert _wait_done(jm, job3.id).status == "done"


def test_job_error_sets_error(fake_pipeline, monkeypatch):
    import daily_review.pipeline as pipeline_mod

    def bad_compute(collected):
        raise RuntimeError("boom")

    monkeypatch.setattr(pipeline_mod, "compute", bad_compute)
    jm = JobManager()
    job = jm.start(trade_date="20260806")
    done = _wait_done(jm, job.id)
    assert done.status == "error"
    assert "boom" in done.error
    assert done.logs  # 异常写入日志


def test_prune_keeps_max_done(fake_pipeline):
    jm = JobManager(max_done=3)
    ids = []
    for i in range(6):
        job = jm.start(trade_date=f"2026080{i}", no_llm=True)
        ids.append(job.id)
        _wait_done(jm, job.id)
    assert len(jm.list()) <= 3
    assert jm.status(ids[0]) is None
    assert jm.status(ids[-1]) is not None
