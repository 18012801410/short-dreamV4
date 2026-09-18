"""Job 状态机（TASK-003）：依赖门、重试、不可变性与 Worker 重启恢复。

规则来源：DOMAIN_MODEL.md「Job」。pending → running → succeeded|failed|cancelled；
重试 = 同 Job failed→pending（仅限 attempts < max_attempts）；"重新生成" = 新 Job。
"""

from __future__ import annotations

from server.domain.entities import Job, JobError, JobEvent, utcnow
from server.domain.enums import JobStatus, JobType
from server.domain.errors import (
    DependencyBlockedError,
    JobNotRetryableError,
    StateIllegalError,
)


def _log(job: Job, event: str, detail: str = "") -> list[JobEvent]:
    return [*job.log, JobEvent(event=event, detail=detail)]


def ensure_runnable(job: Job, dep_statuses: dict[str, JobStatus]) -> None:
    """依赖门：depends_on 未全部 succeeded 的 Job 不得进入 running。"""
    unsatisfied = [
        dep for dep in job.depends_on if dep_statuses.get(dep) is not JobStatus.SUCCEEDED
    ]
    if unsatisfied:
        raise DependencyBlockedError(
            f"Job {job.job_id} 的依赖未全部成功：{', '.join(unsatisfied)}",
            details={"unsatisfied": unsatisfied},
        )


def mark_running(job: Job, dep_statuses: dict[str, JobStatus] | None = None) -> Job:
    """pending → running。提供 dep_statuses 时先过依赖门；占用一个 attempts。"""
    if job.status is not JobStatus.PENDING:
        raise StateIllegalError(
            f"只有 pending Job 可进入 running（当前 {job.status.value}）"
        )
    if dep_statuses is not None:
        ensure_runnable(job, dep_statuses)
    return job.model_copy(
        update={
            "status": JobStatus.RUNNING,
            "attempts": job.attempts + 1,
            "started_at": utcnow(),
            "error": None,
            "log": _log(job, "running", f"attempt {job.attempts + 1}/{job.max_attempts}"),
        }
    )


def mark_succeeded(job: Job, *, progress: int = 100, phase: str = "done") -> Job:
    if job.status is not JobStatus.RUNNING:
        raise StateIllegalError(
            f"只有 running Job 可成功（当前 {job.status.value}）"
        )
    return job.model_copy(
        update={
            "status": JobStatus.SUCCEEDED,
            "progress": progress,
            "phase": phase,
            "finished_at": utcnow(),
            "log": _log(job, "succeeded"),
        }
    )


def mark_failed(job: Job, error: JobError) -> Job:
    if job.status is not JobStatus.RUNNING:
        raise StateIllegalError(
            f"只有 running Job 可失败（当前 {job.status.value}）"
        )
    return job.model_copy(
        update={
            "status": JobStatus.FAILED,
            "error": error,
            "finished_at": utcnow(),
            "log": _log(job, "failed", f"{error.code}: {error.message}"),
        }
    )


def cancel(job: Job, reason: str = "") -> Job:
    if job.status in {JobStatus.SUCCEEDED, JobStatus.CANCELLED}:
        raise StateIllegalError(
            f"{job.status.value} Job 不可取消"
        )
    return job.model_copy(
        update={
            "status": JobStatus.CANCELLED,
            "finished_at": utcnow(),
            "log": _log(job, "cancelled", reason),
        }
    )


def retry(job: Job) -> Job:
    """重试 = 同 Job 重置为 pending（重试计费安全：仅限失败态且 attempts 未用尽）。"""
    if job.status is not JobStatus.FAILED:
        raise JobNotRetryableError(
            f"只有 failed Job 可重试（当前 {job.status.value}）"
        )
    if job.attempts >= job.max_attempts:
        raise JobNotRetryableError(
            f"Job {job.job_id} 重试次数已用尽（{job.attempts}/{job.max_attempts}）；"
            "请改用重新生成（新 Job）"
        )
    return job.model_copy(
        update={
            "status": JobStatus.PENDING,
            "error": None,
            "log": _log(job, "retry_scheduled", f"attempt {job.attempts + 1}/{job.max_attempts}"),
        }
    )


def recover_interrupted(job: Job) -> tuple[Job, bool]:
    """Worker 重启时对遗留 running Job 的处置（DOMAIN_MODEL 不变量）。

    返回 (job, resumed)：
    - video_gen 且有 provider_task_id → 保持 running 继续轮询（resumed=True）
    - 其余 → 标 failed（code=INTERRUPTED），可重试
    """
    if job.status is not JobStatus.RUNNING:
        return job, False
    if job.type is JobType.VIDEO_GEN and job.provider_task_id:
        resumed = job.model_copy(
            update={"log": _log(job, "resumed", f"provider_task_id={job.provider_task_id}")}
        )
        return resumed, True
    interrupted = job.model_copy(
        update={
            "status": JobStatus.FAILED,
            "error": JobError(code="INTERRUPTED", message="Worker 重启导致任务中断，可重试"),
            "finished_at": utcnow(),
            "log": _log(job, "interrupted"),
        }
    )
    return interrupted, False
