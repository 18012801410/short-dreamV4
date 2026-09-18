"""FakeProvider（TASK-004）：Worker 全链演示与单测的供应商替身。

可配置两种行为：
- fail_attempts：按 JobType 配置前 N 次 handler 调用返回失败（测退避重试）
- video 两阶段：首次提交返回 running+provider_task_id，再次问询后成功（测恢复/轮询）
真实 Provider handler（TASK-005-007）落地后，本模块仅保留测试用途。
"""

from __future__ import annotations

from collections import defaultdict

from server.domain.entities import Job, JobError
from server.domain.enums import JobStatus, JobType
from server.worker.engine import Handler, Outcome


class FakeProvider:
    def __init__(
        self,
        *,
        fail_attempts: dict[JobType, int] | None = None,
        video_polls_to_complete: int = 1,
    ) -> None:
        self.calls: list[tuple[str, str]] = []  # (job_id, phase)
        self.fail_attempts: dict[JobType, int] = fail_attempts or {}
        self.video_polls_to_complete = video_polls_to_complete
        self._polls: dict[str, int] = defaultdict(int)

    # -- handler 实现（make_fake_handlers 组装） ----------------------------

    def instant(self, job: Job) -> Outcome:
        self.calls.append((job.job_id, "run"))
        if job.attempts <= self.fail_attempts.get(job.type, 0):
            return Outcome(
                status=JobStatus.FAILED, error=JobError(code="FAKE_FAIL", message="按配置失败")
            )
        return Outcome(status=JobStatus.SUCCEEDED)

    def video(self, job: Job) -> Outcome:
        if not job.provider_task_id:
            self.calls.append((job.job_id, "submit"))
            return Outcome(
                status=JobStatus.RUNNING,
                provider_task_id=f"fake-task-{job.job_id}",
                progress=10,
                phase="submitted",
            )
        self.calls.append((job.job_id, "poll"))
        self._polls[job.job_id] += 1
        if self._polls[job.job_id] < self.video_polls_to_complete:
            return Outcome(
                status=JobStatus.RUNNING,
                provider_task_id=job.provider_task_id,
                progress=50,
                phase="generating",
            )
        return Outcome(status=JobStatus.SUCCEEDED, provider_task_id=job.provider_task_id)


def make_fake_handlers(provider: FakeProvider) -> dict[JobType, Handler]:
    return {
        JobType.SCRIPT_GEN: provider.instant,
        JobType.ASSET_EXTRACT: provider.instant,
        JobType.IMAGE_GEN: provider.instant,
        JobType.STORYBOARD_GEN: provider.instant,
        JobType.VIDEO_GEN: provider.video,
        JobType.TAIL_EXTRACT: provider.instant,
        JobType.COMPOSE: provider.instant,
    }
