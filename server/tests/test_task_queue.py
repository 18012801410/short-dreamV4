"""jobs 队列与 Worker 引擎全链单测（TASK-004）：FakeProvider 演示全链。

覆盖：入队→执行→成功 / 失败 / 退避重试 / 依赖排序 / video 两阶段轮询 / 中断恢复。
用独立临时 SQLite + 可注入时钟，不碰真实 data/app.db。
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine

from server.adapters.fake import FakeProvider, make_fake_handlers
from server.domain.entities import Clip, Job, ProviderCall
from server.domain.enums import JobStatus, JobType
from server.infra.repositories import ClipRepo, JobRepo, ProjectRepo, ProviderCallRepo
from server.infra.tables import metadata
from server.tests.factories import make_project
from server.worker.engine import EngineConfig, WorkerEngine


@pytest.fixture()
def db_engine(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path/'q.db'}", connect_args={"check_same_thread": False}
    )
    metadata.create_all(engine)
    return engine


class FakeClock:
    """可手动推进的时钟，用来跨过退避等待。"""

    def __init__(self) -> None:
        self.now = datetime(2026, 9, 12, 12, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture()
def clock():
    return FakeClock()


def make_engine_under_test(db_engine, clock, provider: FakeProvider) -> WorkerEngine:
    repo = JobRepo(db_engine)
    return WorkerEngine(
        repo,
        make_fake_handlers(provider),
        EngineConfig(poll_interval_sec=2, retry_backoff_base_sec=2),
        clock=clock,
    )


def make_job(job_id: str = "j-1", **overrides) -> Job:
    defaults: dict = {
        "job_id": job_id,
        "project_id": "p-1",
        "type": JobType.SCRIPT_GEN,
    }
    defaults.update(overrides)
    return Job(**defaults)


def seed_project(db_engine) -> None:
    ProjectRepo(db_engine).create(make_project())


def test_enqueue_run_succeed(db_engine, clock) -> None:
    seed_project(db_engine)
    repo = JobRepo(db_engine)
    provider = FakeProvider()
    worker = make_engine_under_test(db_engine, clock, provider)

    job = make_job()
    repo.insert(job)
    assert worker.run_once() is True
    done = repo.get(job.job_id)
    assert done is not None and done.status is JobStatus.SUCCEEDED
    assert done.attempts == 1 and done.finished_at is not None
    # 队列空了，再跑一轮无事可做
    assert worker.run_once() is False


def test_failure_terminal_when_no_retry_budget(db_engine, clock) -> None:
    seed_project(db_engine)
    repo = JobRepo(db_engine)
    provider = FakeProvider(fail_attempts={JobType.SCRIPT_GEN: 1})
    worker = make_engine_under_test(db_engine, clock, provider)

    repo.insert(make_job(max_attempts=1))
    worker.run_once()
    failed = repo.get("j-1")
    assert failed is not None and failed.status is JobStatus.FAILED
    assert failed.error is not None and failed.error.code == "FAKE_FAIL"


def test_backoff_retry_same_job_then_success(db_engine, clock) -> None:
    seed_project(db_engine)
    repo = JobRepo(db_engine)
    provider = FakeProvider(fail_attempts={JobType.SCRIPT_GEN: 2})
    worker = make_engine_under_test(db_engine, clock, provider)

    repo.insert(make_job(max_attempts=3))
    worker.run_once()  # attempt 1 失败 → pending，退避 2s
    first = repo.get("j-1")
    assert first is not None and first.status is JobStatus.PENDING
    assert first.attempts == 1
    scheduled = repo.next_attempt_at("j-1")
    assert scheduled is not None and scheduled > clock.now

    worker.run_once()  # 退避未到，不领取
    assert repo.get("j-1").status is JobStatus.PENDING

    clock.advance(3)
    worker.run_once()  # attempt 2 失败 → 退避 4s
    assert repo.get("j-1").attempts == 2
    clock.advance(5)
    worker.run_once()  # attempt 3 成功
    done = repo.get("j-1")
    assert done.status is JobStatus.SUCCEEDED and done.attempts == 3


def test_dependencies_order_execution(db_engine, clock) -> None:
    seed_project(db_engine)
    repo = JobRepo(db_engine)
    provider = FakeProvider()
    worker = make_engine_under_test(db_engine, clock, provider)

    repo.insert(make_job("j-story", type=JobType.STORYBOARD_GEN, depends_on=["j-script"]))
    repo.insert(make_job("j-script", type=JobType.SCRIPT_GEN))

    # 单轮批处理内依赖排序生效：j-script 先执行，j-story 紧随其后
    worker.run_once()
    statuses = {j.job_id: j.status for j in repo.list_all()}
    assert statuses == {
        "j-script": JobStatus.SUCCEEDED,
        "j-story": JobStatus.SUCCEEDED,
    }
    assert [jid for jid, _ in provider.calls] == ["j-script", "j-story"]


def test_priority_breaks_ties(db_engine, clock) -> None:
    seed_project(db_engine)
    repo = JobRepo(db_engine)
    provider = FakeProvider()
    worker = make_engine_under_test(db_engine, clock, provider)

    repo.insert(make_job("j-low", priority=0))
    repo.insert(make_job("j-high", priority=5))
    worker.run_once()
    order = [jid for jid, _ in provider.calls]
    assert order == ["j-high", "j-low"]


def test_video_two_phase_submit_then_poll(db_engine, clock) -> None:
    seed_project(db_engine)
    repo = JobRepo(db_engine)
    provider = FakeProvider()
    worker = make_engine_under_test(db_engine, clock, provider)

    repo.insert(
        make_job(
            "j-video",
            type=JobType.VIDEO_GEN,
            input_snapshot={"segment_key": "S01G01", "prompt": "x"},
        )
    )
    worker.run_once()  # 提交：running + provider_task_id
    submitted = repo.get("j-video")
    assert submitted.status is JobStatus.RUNNING
    assert submitted.provider_task_id == "fake-task-j-video"
    assert submitted.phase == "submitted"

    worker.run_once()  # 轮询：成功
    done = repo.get("j-video")
    assert done.status is JobStatus.SUCCEEDED


def test_recovery_resumes_video_and_interrupts_others(db_engine, clock) -> None:
    seed_project(db_engine)
    repo = JobRepo(db_engine)
    snapshot = {"segment_key": "S01G01", "prompt": "x"}

    # 模拟上次进程崩溃留下的两个 running Job
    repo.insert(make_job("j-v", type=JobType.VIDEO_GEN, input_snapshot=snapshot))
    repo.save(repo.get("j-v").model_copy(update={"status": JobStatus.RUNNING, "attempts": 1,
                                                "provider_task_id": "fake-task-j-v"}))
    repo.insert(make_job("j-s", type=JobType.SCRIPT_GEN, max_attempts=2))
    repo.save(repo.get("j-s").model_copy(update={"status": JobStatus.RUNNING, "attempts": 1}))

    provider = FakeProvider()
    fresh_worker = make_engine_under_test(db_engine, clock, provider)  # "重启"
    stats = fresh_worker.recover_stale_running()
    assert stats == {"resumed": 1, "interrupted": 1}

    resumed = repo.get("j-v")
    assert resumed.status is JobStatus.RUNNING  # video 继续轮询
    interrupted = repo.get("j-s")
    assert interrupted.status is JobStatus.PENDING  # 标 failed 后自动排程重试
    assert any(e.event == "interrupted" for e in interrupted.log)
    # 退避时间在崩溃后重排
    assert repo.next_attempt_at("j-s") > clock.now

    clock.advance(3)
    fresh_worker.run_once()  # 轮询 j-v 至成功；j-s 退避已过也被执行
    assert repo.get("j-v").status is JobStatus.SUCCEEDED
    assert repo.get("j-s").status is JobStatus.SUCCEEDED


def test_no_handler_marks_failed(db_engine, clock) -> None:
    seed_project(db_engine)
    repo = JobRepo(db_engine)
    provider = FakeProvider()
    handlers = make_fake_handlers(provider)
    del handlers[JobType.COMPOSE]
    worker = WorkerEngine(repo, handlers, EngineConfig(), clock=clock)

    repo.insert(make_job("j-c", type=JobType.COMPOSE))
    worker.run_once()
    failed = repo.get("j-c")
    assert failed.status is JobStatus.FAILED and failed.error.code == "NO_HANDLER"


def test_clip_and_provider_call_repos_roundtrip(db_engine) -> None:
    clip_repo = ClipRepo(db_engine)
    call_repo = ProviderCallRepo(db_engine)

    clip_repo.add(
        Clip(
            clip_id="c-1",
            project_id="p-1",
            segment_key="S01G01",
            video_job_id="j-1",
            storyboard_version_id="sbv-1",
            file_path="p-1/segments/S01G01_v1.mp4",
            duration_sec=5.0,
            mode="r2va",
        )
    )
    assert clip_repo.list_by_project("p-1")[0].segment_key == "S01G01"

    call_repo.add(
        ProviderCall(
            call_id="pc-1",
            job_id="j-1",
            provider="minimax-video",
            model="MiniMax-H3",
            kind="video",
            usage={"total_seconds": 5},
        )
    )
    assert call_repo.list_by_job("j-1")[0].usage == {"total_seconds": 5}


# --------------------------------------------------------------------------
# 队列满（TASK_QUEUE_MAXED）长退避（2026-09-16 实测修复）
# --------------------------------------------------------------------------


def test_queue_full_failure_gets_long_fixed_backoff(db_engine, clock) -> None:
    """TASK_QUEUE_MAXED：固定 120s 长退避，不用指数短退避烧光重试次数。"""
    seed_project(db_engine)
    repo = JobRepo(db_engine)

    def crashing_handler(job: Job):
        from server.domain.entities import JobError
        from server.worker.engine import Outcome

        return Outcome(
            status=JobStatus.FAILED,
            error=JobError(
                code="HANDLER_CRASHED",
                message="[REJECTED] 任务创建被拒：TASK_QUEUE_MAXED",
            ),
        )

    worker = WorkerEngine(
        repo,
        {JobType.SCRIPT_GEN: crashing_handler},
        EngineConfig(retry_backoff_base_sec=2),
        clock=clock,
    )
    repo.insert(make_job("j-q"))
    worker.run_once()

    failed = repo.get("j-q")
    assert failed.status is JobStatus.PENDING and failed.attempts == 1
    scheduled = repo.next_attempt_at("j-q")
    assert scheduled is not None
    delay = (scheduled - clock.now).total_seconds()
    assert delay == 120.0


def test_normal_failure_keeps_exponential_backoff(db_engine, clock) -> None:
    """非队列满失败：退避 = base * 2^(attempts-1)，行为不变。"""
    seed_project(db_engine)
    repo = JobRepo(db_engine)

    def failing_handler(job: Job):
        from server.domain.entities import JobError
        from server.worker.engine import Outcome

        return Outcome(
            status=JobStatus.FAILED,
            error=JobError(code="PROVIDER_ERROR", message="some other failure"),
        )

    worker = WorkerEngine(
        repo,
        {JobType.SCRIPT_GEN: failing_handler},
        EngineConfig(retry_backoff_base_sec=2),
        clock=clock,
    )
    repo.insert(make_job("j-n"))
    worker.run_once()

    delay = (repo.next_attempt_at("j-n") - clock.now).total_seconds()
    assert delay == 2.0


def test_queue_full_retry_does_not_consume_budget(db_engine, clock) -> None:
    """TASK_QUEUE_MAXED 不消耗重试预算（TASK-034）：多 Worker 并行批量提交时
    额度占满是常态，若照常扣预算会把整批任务打成"重试次数用尽"而全灭。"""
    seed_project(db_engine)
    repo = JobRepo(db_engine)

    def always_queue_full(job: Job):
        from server.domain.entities import JobError
        from server.worker.engine import Outcome

        return Outcome(
            status=JobStatus.FAILED,
            error=JobError(
                code="HANDLER_CRASHED",
                message="[REJECTED] 任务创建被拒：TASK_QUEUE_MAXED",
            ),
        )

    worker = WorkerEngine(
        repo,
        {JobType.SCRIPT_GEN: always_queue_full},
        EngineConfig(retry_backoff_base_sec=0.01, queue_full_retry_delay_sec=0.01),
        clock=clock,
    )
    job = make_job("j-qf")
    job = job.model_copy(update={"max_attempts": 2})
    repo.insert(job)
    # 连续跑 4 轮（超过 max_attempts=2）：仍应保持 pending 可重试，不被打成用尽
    for _ in range(4):
        clock.advance(1)
        worker.run_once()
        current = repo.get("j-qf")
        assert current.status is JobStatus.PENDING
    final = repo.get("j-qf")
    assert final.attempts == 4
    assert final.max_attempts > final.attempts  # 预算被同步放宽


def test_manual_override_survives_production_recompile(tmp_path) -> None:
    """TASK-040 人工覆盖：页面手改过的正文在产视频时直接使用，不再被实时重编译
    覆盖；执行"恢复自动编译"后交回编译器。"""
    import sqlalchemy as sa

    from server.app.context import AppContext
    from server.app.usecases import WorkbenchService
    from server.domain.entities import (
        Asset,
        AssetImage,
        H3Prompt,
        Segment,
        Shot,
        StoryboardContent,
        StoryboardVersion,
        SubjectPlacement,
    )
    from server.domain.enums import AssetImageStatus, AssetKind, WorkStatus
    from server.infra.config import Settings
    from server.infra.tables import metadata

    settings = Settings(data_dir=tmp_path, _env_file=None)
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    engine = sa.create_engine(
        f"sqlite:///{tmp_path/'override.db'}", connect_args={"check_same_thread": False}
    )
    metadata.create_all(engine)
    ctx = AppContext.build(settings, engine)
    svc = WorkbenchService(ctx)
    pid = svc.create_project("渡口", "想法", {}).project_id
    ctx.assets.add_asset(
        Asset(asset_id="a1", project_id=pid, kind=AssetKind.CHARACTER, name="老船工",
              visual_anchor="灰白胡须、深色油皮外套")
    )
    ctx.assets.add_image(
        AssetImage(asset_image_id="i1", asset_id="a1", version_no=1, view_label="主设定",
                   file_path=f"{pid}/assets/a1.png",
                   status=AssetImageStatus.READY, approved=True)
    )

    def make_seg() -> Segment:
        return Segment(
            segment_key="S01G01", scene_id="S1", index=1, duration_sec=5,
            shots=[Shot(shot_no=1, cutpoint_sec=0.0, camera="static shot",
                        description="ferryman at the pier")],
            asset_refs=[{"asset_id": "a1", "usage_note": "主角"}],
            subject_placements=[SubjectPlacement(name="老船工", placement="at the pier")],
            h3_prompt=H3Prompt(text=""),
        )

    # 直推状态机到分镜阶段（编辑 active 分镜会触发 FR-016 失效回退，需要项目已过该门）
    from server.domain.project import apply_action as _apply

    moved = ctx.projects.get(pid)
    for action in ("generate_script", "script_generated", "approve_script",
                   "generate_assets", "assets_generated", "approve_assets",
                   "generate_storyboard", "storyboard_generated", "approve_storyboard"):
        moved = _apply(moved, action)
    ctx.projects.save(moved)

    base = make_seg()
    from server.app.h3_compiler import compile_h3_prompt

    compiled = compile_h3_prompt(base, {"a1": ctx.assets.get_asset("a1")})
    seg = base.model_copy(update={"h3_prompt": H3Prompt(text=compiled)})
    ctx.storyboards.add(
        StoryboardVersion(
            storyboard_version_id="sbv-ov", project_id=pid, version_no=1,
            content=StoryboardContent(segments=[seg]), status=WorkStatus.ACTIVE,
        )
    )

    # 人工改写正文 → 覆盖标记
    edited = compiled.replace(
        "Soft ambient underscore, low volume.", "custom music line, no strings"
    )
    assert edited != compiled
    svc.dispatch(pid, "edit_segment", {
        "segment_key": "S01G01",
        "segment": {
            **seg.model_dump(),
            "h3_prompt": {**seg.h3_prompt.model_dump(), "text": edited},
        },
    })
    # 编辑会新建草稿版本（原 active 转 superseded，需重新确认分镜）
    saved = ctx.storyboards.latest_draft(pid).content.segments[0]
    assert saved.h3_prompt.manual_override is True
    produced = svc._compile_production_prompt(svc.get_project(pid), saved, opening_frame=False)
    assert produced == edited.strip()

    # 恢复自动编译 → 标记清除，正文回到编译器版本
    svc.dispatch(pid, "edit_segment", {
        "segment_key": "S01G01", "reset_prompt": True,
        "segment": saved.model_dump(),
    })
    restored = ctx.storyboards.latest_draft(pid).content.segments[0]
    assert restored.h3_prompt.manual_override is False
    assert "custom music line" not in restored.h3_prompt.text
    assert restored.h3_prompt.text.startswith("subject_definitions:")


# --------------------------------------------------------------------------
# 图片并发闸门（TASK-048）：image_concurrency 上限内的图片任务才可领取
# --------------------------------------------------------------------------


def test_claim_type_gate_skips_blocked_types(db_engine, clock) -> None:
    """type_gate 返回 False 的任务本轮不领取（留在 pending，不耗重试预算）。"""
    seed_project(db_engine)
    repo = JobRepo(db_engine)
    repo.insert(make_job("j-img", type=JobType.IMAGE_GEN))
    repo.insert(make_job("j-script", type=JobType.SCRIPT_GEN))

    claimed = repo.claim_next_runnable(clock(), type_gate=lambda _job: False)
    assert claimed is None

    claimed = repo.claim_next_runnable(
        clock(), type_gate=lambda job: job.type is JobType.SCRIPT_GEN
    )
    assert claimed is not None
    job, _deps = claimed
    assert job.type is JobType.SCRIPT_GEN
    # 被闸门挡住的图片任务原封不动留在 pending
    assert repo.get("j-img").status is JobStatus.PENDING


def test_engine_image_concurrency_gate(db_engine, clock) -> None:
    """并发上限=1 且已有一张图在跑时，第二张图不领取；放宽后恢复领取。"""
    seed_project(db_engine)
    repo = JobRepo(db_engine)
    provider = FakeProvider()
    limit = {"value": 1}
    worker = WorkerEngine(
        repo,
        make_fake_handlers(provider),
        EngineConfig(poll_interval_sec=2, retry_backoff_base_sec=2),
        clock=clock,
        image_concurrency_provider=lambda: limit["value"],
    )

    # 占住唯一并发名额：一张图处于 running
    repo.insert(make_job("j-img-running", type=JobType.IMAGE_GEN, status=JobStatus.RUNNING))
    repo.insert(make_job("j-img-queued", type=JobType.IMAGE_GEN))
    worker.run_once()
    assert repo.get("j-img-queued").status is JobStatus.PENDING

    # 上限放宽到 2 → 队列里的图被领取并（FakeProvider 即时）执行成功
    limit["value"] = 2
    worker.run_once()
    assert repo.get("j-img-queued").status is JobStatus.SUCCEEDED
