"""Worker 轮询引擎（TASK-004）：领取、执行、终态、重试排程与中断恢复。

职责边界：引擎只管 Job 生命周期机械（DOMAIN_MODEL「Job」）；干什么活由
handlers 决定——真实 Provider handler 在 TASK-005-007 接入，测试与演示用
server/adapters/fake.py。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from server.domain.entities import Job, JobError
from server.domain.enums import JobStatus, JobType
from server.domain.errors import JobNotRetryableError
from server.domain.job import mark_failed, mark_succeeded, recover_interrupted, retry
from server.infra.repositories import JobRepo

log = logging.getLogger("worker.engine")


@dataclass
class Outcome:
    """handler 的一次执行结果：succeeded / running(+provider_task_id) / failed(+error)。"""

    status: JobStatus
    provider_task_id: str = ""
    error: JobError | None = None
    progress: int | None = None
    phase: str = ""


Handler = Callable[[Job], Outcome]


@dataclass
class EngineConfig:
    poll_interval_sec: float = 2.0
    retry_backoff_base_sec: float = 2.0
    max_batch: int = 10  # 单轮最多处理任务数，防饿死轮询
    # RunningHub 账号并发队列占满（TASK_QUEUE_MAXED）时的固定重试退避：
    # 占队列的是数分钟级的长任务，指数短退避（5-20s）会在额度内烧光重试次数
    queue_full_retry_delay_sec: float = 120.0
    # 图片并发闸门的作用范围（TASK-048）：这些类型的任务同时 running 数超过
    # image_concurrency_provider() 给出的上限时暂不领取（留在 pending 不耗预算）
    image_job_types: frozenset[JobType] = frozenset(
        {JobType.IMAGE_GEN, JobType.FRAME_GEN}
    )


# 任务创建被拒（RunningHub 并发队列占满）的错误特征：配 queue_full_retry_delay_sec
QUEUE_FULL_MARKER = "TASK_QUEUE_MAXED"


class WorkerEngine:
    def __init__(
        self,
        job_repo: JobRepo,
        handlers: dict[JobType, Handler],
        config: EngineConfig | None = None,
        clock: Callable[[], datetime] | None = None,
        image_concurrency_provider: Callable[[], int] | None = None,
    ) -> None:
        self._repo = job_repo
        self._handlers = handlers
        self._config = config or EngineConfig()
        self._clock = clock or (lambda: datetime.now(UTC))
        # 图片并发上限提供器：每轮领取时调用（读 settings 表覆盖，设置页改完
        # 即生效无需重启）；None = 不设闸门（保持旧行为）
        self._image_concurrency_provider = image_concurrency_provider

    # -- 启动恢复 ----------------------------------------------------------

    def recover_stale_running(self) -> dict[str, int]:
        """Worker 重启处置：video_gen 有 provider_task_id → 继续轮询；其余 → failed。

        被标 failed(INTERRUPTED) 的任务若还有重试额度，立即按退避排程重试。
        """
        stats = {"resumed": 0, "interrupted": 0}
        for job in self._repo.list_by_status(JobStatus.RUNNING):
            recovered, resumed = recover_interrupted(job)
            self._repo.save(recovered)
            if resumed:
                stats["resumed"] += 1
            else:
                stats["interrupted"] += 1
                self._schedule_retry(recovered)
        return stats

    # -- 单轮循环 ----------------------------------------------------------

    def run_once(self) -> bool:
        """跑一轮：先推进运行中的 video_gen，再领取 pending。返回是否做了事。"""
        did_work = self._advance_running_videos()
        for _ in range(self._config.max_batch):
            claimed = self._repo.claim_next_runnable(
                self._clock(), type_gate=self._claim_gate
            )
            if claimed is None:
                break
            job, _deps = claimed
            did_work = True
            self._execute(job)
        return did_work

    def _claim_gate(self, job: Job) -> bool:
        """领取闸门（TASK-048）：图片类任务达到并发上限时本轮跳过。

        上限每次现场读取（settings 表覆盖 > .env 默认），设置页改完即生效。
        提供器异常时放行（不因配置读取失败卡死队列）。
        """
        if self._image_concurrency_provider is None:
            return True
        if job.type not in self._config.image_job_types:
            return True
        try:
            limit = max(1, int(self._image_concurrency_provider()))
        except Exception:  # noqa: BLE001 - 配置读取失败按无闸门处理
            return True
        running = self._repo.count_running_of_types(
            sorted(t.value for t in self._config.image_job_types)
        )
        return running < limit

    def run_forever(self) -> None:
        while True:
            self.run_once()
            time.sleep(self._config.poll_interval_sec)

    # -- 内部 --------------------------------------------------------------

    def _advance_running_videos(self) -> bool:
        """对有 provider_task_id 的 running video_gen 再问一次 handler（恢复轮询）。"""
        handler = self._handlers.get(JobType.VIDEO_GEN)
        if handler is None:
            return False
        did_work = False
        for job in self._repo.running_video_jobs():
            outcome = handler(job)
            self._apply_outcome(job, outcome)
            did_work = True
        return did_work

    def _execute(self, job: Job) -> None:
        handler = self._handlers.get(job.type)
        if handler is None:
            failed = mark_failed(
                job, JobError(code="NO_HANDLER", message=f"未注册 {job.type.value} 的 handler")
            )
            self._repo.save(failed)
            return
        try:
            outcome = handler(job)
        except Exception as exc:  # handler 内部异常按失败处理，不让 Worker 死掉
            log.exception("handler crashed for job %s", job.job_id)
            outcome = Outcome(
                status=JobStatus.FAILED,
                error=JobError(code="HANDLER_CRASHED", message=str(exc)),
            )
        self._apply_outcome(job, outcome)

    def _apply_outcome(self, job: Job, outcome: Outcome) -> None:
        if outcome.status is JobStatus.SUCCEEDED:
            self._repo.save(
                mark_succeeded(job, progress=outcome.progress or 100, phase=outcome.phase)
            )
            log.info("job %s succeeded", job.job_id)
        elif outcome.status is JobStatus.RUNNING:
            updates: dict[str, object] = {"provider_task_id": outcome.provider_task_id}
            if outcome.progress is not None:
                updates["progress"] = outcome.progress
            if outcome.phase:
                updates["phase"] = outcome.phase
            self._repo.save(job.model_copy(update=updates))
            log.info(
                "job %s running (provider_task_id=%s)", job.job_id, outcome.provider_task_id
            )
        elif outcome.status is JobStatus.FAILED:
            assert outcome.error is not None
            failed = mark_failed(job, outcome.error)
            self._repo.save(failed)
            log.warning("job %s failed: %s", job.job_id, outcome.error.code)
            if failed.attempts < failed.max_attempts:
                self._schedule_retry(failed)

    def _schedule_retry(self, failed: Job) -> None:
        """重试 = 同 Job 重置 pending，退避时间 = base * 2^(attempts-1)。

        队列满（TASK_QUEUE_MAXED）例外：固定长退避，且**不消耗重试预算**——
        额度占满不是任务本身的失败，而是"账号并发额度被在途长任务占着"，
        多 Worker 并行跑图/跑视频时必然出现；若照常扣预算，一批并发提交就会
        把额度耗光导致整批任务全灭（2026-09-16 实测：13 张资产图全灭）。
        """
        queue_full = failed.error is not None and QUEUE_FULL_MARKER in (
            failed.error.message or ""
        )
        if queue_full:
            failed = failed.model_copy(
                update={"max_attempts": failed.max_attempts + 1}
            )
        try:
            retried = retry(failed)
        except JobNotRetryableError:
            return
        if queue_full:
            delay = self._config.queue_full_retry_delay_sec
        else:
            delay = self._config.retry_backoff_base_sec * (2 ** (retried.attempts - 1))
        next_at = self._clock() + timedelta(seconds=delay)
        self._repo.save(retried, next_attempt_at=next_at)
        log.info(
            "job %s scheduled retry %d/%d at %s (delay=%.0fs%s)",
            retried.job_id,
            retried.attempts,
            retried.max_attempts,
            next_at.isoformat(),
            delay,
            "，队列满不消耗预算" if queue_full else "",
        )
