"""Job 状态机单测（TASK-003）。"""

import pytest
from pydantic import ValidationError

from server.domain.entities import Job, JobError
from server.domain.enums import JobStatus, JobType
from server.domain.errors import (
    DependencyBlockedError,
    JobNotRetryableError,
    StateIllegalError,
)
from server.domain.job import (
    cancel,
    ensure_runnable,
    mark_failed,
    mark_running,
    mark_succeeded,
    recover_interrupted,
    retry,
)


def make_job(**overrides) -> Job:
    defaults: dict = {
        "job_id": "j-1",
        "project_id": "p-1",
        "type": JobType.SCRIPT_GEN,
    }
    defaults.update(overrides)
    return Job(**defaults)


def test_dependency_gate_blocks_running() -> None:
    job = make_job(depends_on=["j-0"])
    with pytest.raises(DependencyBlockedError):
        mark_running(job, {"j-0": JobStatus.PENDING})
    with pytest.raises(DependencyBlockedError):
        ensure_runnable(job, {})
    # 全部依赖 succeeded 后放行
    running = mark_running(job, {"j-0": JobStatus.SUCCEEDED})
    assert running.status is JobStatus.RUNNING


def test_running_counts_attempts_and_success_is_terminal() -> None:
    job = mark_running(make_job())
    assert job.attempts == 1
    done = mark_succeeded(job)
    assert done.status is JobStatus.SUCCEEDED and done.finished_at is not None
    with pytest.raises(StateIllegalError):
        mark_running(done)
    with pytest.raises(StateIllegalError):
        mark_succeeded(done)
    with pytest.raises(JobNotRetryableError):
        retry(done)


def test_failed_then_retry_resets_same_job() -> None:
    job = mark_running(make_job(max_attempts=3))
    failed = mark_failed(job, JobError(code="LLM_SCHEMA", message="bad json"))
    assert failed.status is JobStatus.FAILED and failed.error is not None
    retried = retry(failed)
    assert retried.status is JobStatus.PENDING and retried.error is None
    assert retried.attempts == 1  # attempts 保留，入 running 时再 +1
    again = mark_failed(mark_running(retried), JobError(code="X", message="x"))
    assert again.attempts == 2
    third = mark_failed(mark_running(retry(again)), JobError(code="X", message="x"))
    with pytest.raises(JobNotRetryableError):
        retry(third)  # attempts=3 >= max_attempts=3


def test_cancel_from_pending_or_running() -> None:
    cancelled = cancel(make_job(), "user asked")
    assert cancelled.status is JobStatus.CANCELLED
    with pytest.raises(StateIllegalError):
        cancel(cancelled)
    with pytest.raises(StateIllegalError):
        cancel(mark_succeeded(mark_running(make_job())))


def test_recover_video_job_with_provider_task_resumes_polling() -> None:
    job = make_job(
        type=JobType.VIDEO_GEN,
        input_snapshot={"prompt": "x", "segment_key": "S01G01"},
        provider_task_id="mm-123",
    )
    running = mark_running(job)
    recovered, resumed = recover_interrupted(running)
    assert resumed and recovered.status is JobStatus.RUNNING
    assert recovered.log[-1].event == "resumed"


def test_recover_other_jobs_fail_with_interrupted_and_retryable() -> None:
    running = mark_running(make_job())
    recovered, resumed = recover_interrupted(running)
    assert not resumed and recovered.status is JobStatus.FAILED
    assert recovered.error is not None and recovered.error.code == "INTERRUPTED"
    assert retry(recovered).status is JobStatus.PENDING
    # 非 running 的 Job 原样返回
    pending = make_job()
    assert recover_interrupted(pending) == (pending, False)


def test_video_gen_requires_input_snapshot() -> None:
    with pytest.raises(ValidationError):
        make_job(type=JobType.VIDEO_GEN)
    snapshot_job = make_job(
        type=JobType.VIDEO_GEN,
        input_snapshot={
            "storyboard_version_id": "sbv-1",
            "segment_key": "S01G01",
            "prompt": "...",
            "references": [],
            "mode": "r2va",
            "resolution": "768P",
            "duration": 5,
        },
    )
    assert snapshot_job.input_snapshot["mode"] == "r2va"
