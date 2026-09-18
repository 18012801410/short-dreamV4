"""TASK-017 真实端到端：想法 → 剧本 → 资产 → 分镜 → 视频（≥8 段）→ 成片。

用法：python scripts/run_e2e.py [--project-id EXISTING]
真实调用 DeepSeek + RunningHub（预估 400-700 RH 币，耗时 40-60 分钟）。
所有产物落 data/（DB + media），可随时中断，Worker 语义支持恢复。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.adapters.ffmpeg_svc import FFmpegService  # noqa: E402
from server.adapters.image import build_image_provider_from_settings  # noqa: E402
from server.adapters.llm import build_llm_from_settings  # noqa: E402
from server.adapters.llm.base import LlmError  # noqa: E402
from server.adapters.runninghub import RunningHubError  # noqa: E402
from server.adapters.video import build_video_provider_from_settings  # noqa: E402
from server.app.agents import Agents  # noqa: E402
from server.app.context import AppContext  # noqa: E402
from server.app.handlers import build_handlers  # noqa: E402
from server.app.usecases import WorkbenchService  # noqa: E402
from server.domain.enums import JobStatus, ProjectStatus  # noqa: E402
from server.domain.errors import DomainError  # noqa: E402
from server.infra.config import get_settings  # noqa: E402
from server.infra.db import get_engine  # noqa: E402
from server.infra.settings_store import apply_overrides, load_overrides  # noqa: E402
from server.worker.engine import EngineConfig, WorkerEngine  # noqa: E402

SEGMENTS_TARGET = 8
DURATION_PER_SEGMENT = 5
POLL_TIMEOUT = 3600


def wait_until_idle(worker: WorkerEngine, ctx: AppContext, pid: str, label: str) -> None:
    """跑 Worker 直到队列空闲；有失败任务则中止脚本。"""
    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        worker.run_once()
        jobs = [j for j in ctx.jobs.list_all() if j.project_id == pid]
        failed = [j for j in jobs if j.status is JobStatus.FAILED]
        busy = [j for j in jobs if j.status in {JobStatus.PENDING, JobStatus.RUNNING}]
        if failed:
            for j in failed:
                print(f"  !! 任务失败 {j.type.value}: {j.error.code} {j.error.message}")
            raise SystemExit(f"{label} 阶段出现失败任务，中止")
        if not busy:
            print(f"  [{label}] 队列空闲")
            return
        running = busy[0]
        print(
            f"  [{label}] {running.type.value} {running.status.value} "
            f"{running.progress}% (busy={len(busy)})",
            flush=True,
        )
        time.sleep(10)
    raise SystemExit(f"{label} 等待超时")


def main() -> int:
    settings = apply_overrides(get_settings(), load_overrides(get_engine()))
    ctx = AppContext.build(settings, get_engine())
    agents = Agents(build_llm_from_settings(settings))
    video = build_video_provider_from_settings(settings)
    image = build_image_provider_from_settings(settings)
    handlers = build_handlers(
        ctx, agents, image=image, video=video, ffmpeg=FFmpegService(settings.ffmpeg_path)
    )
    worker = WorkerEngine(
        ctx.jobs,
        handlers,
        EngineConfig(poll_interval_sec=5, retry_backoff_base_sec=5, max_batch=20),
    )
    svc = WorkbenchService(ctx)

    # 1. 建项目
    project = svc.create_project(
        title="渡口夜行（E2E）",
        idea=(
            "深夜的江边渡口，摆渡了四十年的老船工接到最后一班任务：把一位赶末班车的年轻护士"
            "送到对岸。江雾、旧手电、一段关于「河为什么记得」的对话，到达时天光微亮。"
        ),
        params={
            "genre": "温情剧情",
            "style": "电影写实，冷灰绿夜色调，35mm 胶片感",
            "target_duration_sec": SEGMENTS_TARGET * DURATION_PER_SEGMENT,
            "scene_count": 3,
            "ratio": "16:9",
            "resolution": "768P",
            "prompt_lang": "en",
        },
    )
    pid = project.project_id
    print(f"项目创建：{pid}")

    # 2. 剧本
    print("== 阶段 1：剧本 ==")
    svc.dispatch(pid, "generate_script")
    wait_until_idle(worker, ctx, pid, "script")
    svc.dispatch(pid, "approve_script")

    # 3. 资产 + 逐图批准
    print("== 阶段 2：资产 ==")
    svc.dispatch(pid, "generate_assets")
    wait_until_idle(worker, ctx, pid, "assets")
    for image_row in ctx.assets.list_images(project_id=pid):
        if not image_row.approved and image_row.status.value in {"ready", "uploaded"}:
            image_row.approved = True
            ctx.assets.save_image(image_row)
    svc.dispatch(pid, "approve_assets")
    n_imgs = len(ctx.assets.list_images(project_id=pid))
    print(f"  资产 {len(ctx.assets.list_assets(pid))} 个，图片 {n_imgs} 张")

    # 4. 分镜（LLM 需产出通过确定性校验的六段式提示词）
    print("== 阶段 3：分镜 ==")
    svc.dispatch(pid, "generate_storyboard")
    wait_until_idle(worker, ctx, pid, "storyboard")
    sb = ctx.storyboards.active(pid)
    print(f"  分镜 {len(sb.content.segments)} 段（目标 ≥{SEGMENTS_TARGET}）")
    svc.dispatch(pid, "approve_storyboard")

    # 4.5 关键帧（TASK-031）：逐段开场锚点图，批准后作该段 <Picture 1>
    print("== 阶段 3.5：关键帧 ==")
    svc.dispatch(pid, "generate_keyframes")
    wait_until_idle(worker, ctx, pid, "frame")
    for frame_row in ctx.frames.list_by_project(pid):
        if not frame_row.approved and frame_row.status.value in {"ready", "uploaded"}:
            frame_row.approved = True
            ctx.frames.save(frame_row)
    svc.dispatch(pid, "approve_keyframes", {})
    n_frames = len(ctx.frames.list_by_project(pid))
    print(f"  关键帧 {n_frames} 张")

    # 5. 视频全量生产（最耗时）
    print("== 阶段 4：视频生产 ==")
    t0 = time.time()
    svc.dispatch(pid, "produce_video", {"scope": "all"})
    wait_until_idle(worker, ctx, pid, "video")
    print(f"  视频完成，用时 {int(time.time() - t0)}s")

    # 6. 合成
    print("== 阶段 5：合成 ==")
    svc.dispatch(pid, "compose")
    wait_until_idle(worker, ctx, pid, "compose")

    final = ctx.projects.get(pid)
    films = ctx.films.list_by_project(pid)
    print(f"\n最终状态：{final.status.value}")
    if films:
        film = films[0]
        abs_path = ctx.settings.media_dir / film.file_path
        print(
            f"成片 v{film.version_no}: {abs_path} | {film.duration_sec:.1f}s | "
            f"{len(film.segment_keys)} 段"
        )
    calls = ctx.calls.list_by_job("")  # 全量统计
    del calls
    coins = 0
    for job in ctx.jobs.list_all():
        for call in ctx.calls.list_by_job(job.job_id):
            coins += int(call.usage.get("coins") or 0)
    print(f"总消耗：约 {coins} RH 币")
    return 0 if final.status is ProjectStatus.COMPOSED else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (LlmError, RunningHubError, DomainError) as exc:
        print(f"E2E 失败：{exc}")
        sys.exit(1)
