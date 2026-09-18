"""TASK-017 续跑：从 storyboard_ready 恢复，确保 ≥8 段后生产视频并合成。"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.adapters.image import build_image_provider_from_settings  # noqa: E402
from server.adapters.llm import build_llm_from_settings  # noqa: E402
from server.adapters.video import build_video_provider_from_settings  # noqa: E402
from server.app.agents import Agents  # noqa: E402
from server.app.context import AppContext  # noqa: E402
from server.app.handlers import build_handlers  # noqa: E402
from server.app.usecases import WorkbenchService  # noqa: E402
from server.domain.enums import JobStatus, ProjectStatus  # noqa: E402
from server.infra.config import get_settings  # noqa: E402
from server.infra.db import get_engine  # noqa: E402
from server.infra.settings_store import apply_overrides, load_overrides  # noqa: E402
from server.worker.engine import EngineConfig, WorkerEngine  # noqa: E402

PID = "p-035182ed8ae1"
MIN_SEGMENTS = 8
STORYBOARD_TRIES = 3


def wait_until_idle(worker: WorkerEngine, ctx: AppContext, pid: str, label: str) -> None:
    deadline = time.time() + 3600
    while time.time() < deadline:
        worker.run_once()
        jobs = [j for j in ctx.jobs.list_all() if j.project_id == pid]
        failed = [j for j in jobs if j.status is JobStatus.FAILED]
        busy = [j for j in jobs if j.status in {JobStatus.PENDING, JobStatus.RUNNING}]
        if failed:
            for j in failed:
                print(f"  !! 任务失败 {j.type.value}: {j.error.code} {j.error.message}", flush=True)
            raise SystemExit(f"{label} 阶段出现失败任务，中止")
        if not busy:
            print(f"  [{label}] 队列空闲", flush=True)
            return
        first = busy[0]
        print(
            f"  [{label}] busy={len(busy)} "
            f"{first.type.value}/{first.status.value}",
            flush=True,
        )
        time.sleep(15)


def main() -> int:
    settings = apply_overrides(get_settings(), load_overrides(get_engine()))
    ctx = AppContext.build(settings, get_engine())
    agents = Agents(build_llm_from_settings(settings))
    handlers = build_handlers(
        ctx,
        agents,
        image=build_image_provider_from_settings(settings),
        video=build_video_provider_from_settings(settings),
    )
    worker = WorkerEngine(
        ctx.jobs,
        handlers,
        EngineConfig(poll_interval_sec=5, retry_backoff_base_sec=5, max_batch=20),
    )
    svc = WorkbenchService(ctx)
    project = ctx.projects.get(PID)
    print(f"恢复项目 {PID}（{project.status.value}）", flush=True)

    if project.status.value == "storyboard_ready":
        for attempt in range(1, STORYBOARD_TRIES + 1):
            draft = ctx.storyboards.latest_draft(PID)
            count = len(draft.content.segments) if draft else 0
            if count >= MIN_SEGMENTS:
                break
            print(f"分镜 {count} 段 < {MIN_SEGMENTS}，重新生成（第 {attempt} 次）", flush=True)
            svc.dispatch(PID, "generate_storyboard")
            wait_until_idle(worker, ctx, PID, f"storyboard#{attempt}")

    draft = ctx.storyboards.latest_draft(PID)
    print(f"分镜定稿：{len(draft.content.segments)} 段", flush=True)
    svc.dispatch(PID, "approve_storyboard")

    print("== 视频生产 ==", flush=True)
    t0 = time.time()
    svc.dispatch(PID, "produce_video", {"scope": "all"})
    wait_until_idle(worker, ctx, PID, "video")
    print(f"视频完成，用时 {int((time.time() - t0) / 60)} 分钟", flush=True)

    print("== 合成 ==", flush=True)
    svc.dispatch(PID, "compose")
    wait_until_idle(worker, ctx, PID, "compose")

    final = ctx.projects.get(PID)
    films = ctx.films.list_by_project(PID)
    print(f"最终状态：{final.status.value}", flush=True)
    if films:
        print(
            f"成片 v{films[0].version_no}: {films[0].file_path} | "
            f"{films[0].duration_sec:.1f}s | {len(films[0].segment_keys)} 段",
            flush=True,
        )
    return 0 if final.status is ProjectStatus.COMPOSED else 1


if __name__ == "__main__":
    sys.exit(main())
