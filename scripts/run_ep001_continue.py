"""EP001 续跑：换上带口播预算的精简剧本 → 分镜 → 视频 → 合成。

前置：run_ep001.py 在分镜阶段失败（初版节拍剧本 822 字台词远超 45s 口播预算，
逐字对账与口播预算死锁）。本脚本用修复后的 ScriptAgent（含台词预算规则）
重新生成精简剧本，目标时长放宽到 60s，直接换 active 版本后重跑分镜。
"""

from __future__ import annotations

import sqlite3
import sys
import time
import uuid
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
from server.domain.entities import (
    ScriptVersion,
    utcnow,
)  # noqa: E402
from server.domain.enums import (
    AssetKind,
    BeatType,
    JobStatus,
    ProjectStatus,
    WorkStatus,
)  # noqa: E402
from server.domain.errors import DomainError  # noqa: E402
from server.infra.config import get_settings  # noqa: E402
from server.infra.db import get_engine  # noqa: E402
from server.infra.settings_store import apply_overrides, load_overrides  # noqa: E402
from server.worker.engine import EngineConfig, WorkerEngine  # noqa: E402

TARGET_DURATION = 60
POLL_TIMEOUT = 4500
PID_FILE = Path(__file__).parent / "_ep001_pid.txt"


def wait_until_idle(
    worker: WorkerEngine, ctx: AppContext, pid: str, label: str,
    known_failed: set[str],
) -> None:
    deadline = time.time() + POLL_TIMEOUT
    last = ""
    while time.time() < deadline:
        worker.run_once()
        jobs = [j for j in ctx.jobs.list_all() if j.project_id == pid]
        failed = [
            j for j in jobs
            if j.status is JobStatus.FAILED and j.job_id not in known_failed
        ]
        busy = [j for j in jobs if j.status in {JobStatus.PENDING, JobStatus.RUNNING}]
        if failed:
            for j in failed:
                print(f"  !! 任务失败 {j.type.value}: {j.error.code} {j.error.message}", flush=True)
            raise SystemExit(f"{label} 阶段出现失败任务，中止")
        if not busy:
            print(f"  [{label}] 队列空闲", flush=True)
            return
        running = busy[0]
        status = (
            f"[{label}] {running.type.value} {running.status.value}"
            f" {running.progress}% (busy={len(busy)})"
        )
        if status != last:
            print(status, flush=True)
            last = status
        time.sleep(8)
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

    def known_failed_ids() -> set[str]:
        return {
            j.job_id
            for j in ctx.jobs.list_all()
            if j.project_id == pid and j.status is JobStatus.FAILED
        }

    pid = PID_FILE.read_text(encoding="utf-8").strip()
    project = ctx.projects.get(pid)
    print(f"续跑项目：{pid}（当前状态 {project.status.value}）", flush=True)

    conn = sqlite3.connect(Path(settings.data_dir) / "app.db")
    idea = conn.execute(
        "select idea from projects where project_id=?", (pid,)
    ).fetchone()[0]
    conn.close()

    # 1. 用带台词预算规则的 ScriptAgent 重新生成精简剧本（目标 60s）；
    #    已有精简版（llm:condensed）则复用，避免重复生成
    params = project.params.model_copy(update={"target_duration_sec": TARGET_DURATION})
    active = ctx.scripts.active(pid)
    if active is not None and active.source == "llm:condensed":
        content = active.content
        print(f"  复用精简剧本 v{active.version_no}", flush=True)
    else:
        print("== 重新生成精简剧本（预算 ≤180 字台词）==", flush=True)
        content = agents.run_script(idea, params.model_dump())
    spoken = sum(len(d.line.strip()) for sc in content.scenes for d in sc.dialogues)
    print(f"  精简后台词 {spoken} 字（预算 {TARGET_DURATION * 3}）", flush=True)
    for sc in content.scenes:
        counts: dict[str, int] = {}
        for b in sc.beats:
            counts[b.type.value] = counts.get(b.type.value, 0) + 1
        print(f"  {sc.id} {sc.title}｜beats={len(sc.beats)} {counts}", flush=True)

    # 资产名与新剧本说话人对齐检查
    char_names = [
        a.name for a in ctx.assets.list_assets(pid) if a.kind is AssetKind.CHARACTER
    ]
    missing = sorted(
        {
            d.speaker
            for sc in content.scenes
            for d in sc.dialogues
            if d.speaker not in char_names and not any(n in d.speaker for n in char_names)
        }
    )
    if missing:
        print(f"  ⚠ 台词说话人无角色卡（将以原名引出）：{missing}", flush=True)

    # 2. 换 active 剧本版本（仓库层直换，避免回退重抽资产）；
    #    复用分支（v2 已是 active）只调状态不换版本
    fresh = active is None or active.source != "llm:condensed"
    version = active
    if fresh:
        old_active = ctx.scripts.active(pid)
        if old_active is not None:
            old_active.status = WorkStatus.SUPERSEDED
            ctx.scripts.save(old_active)
        version = ScriptVersion(
            script_version_id=f"sv-{uuid.uuid4()}",
            project_id=pid,
            version_no=len(ctx.scripts.list_by_project(pid)) + 1,
            content=content,
            source="llm:condensed",
            status=WorkStatus.ACTIVE,
        )
        ctx.scripts.add(version)
    project = ctx.projects.get(pid)
    # 状态回位：已有分镜草稿 → storyboard_ready；否则 ASSET_APPROVED
    has_draft = ctx.storyboards.latest_draft(pid) is not None
    target_status = (
        ProjectStatus.STORYBOARD_READY if has_draft else ProjectStatus.ASSET_APPROVED
    )
    project = project.model_copy(
        update={
            "params": params,
            "status": target_status,
            "updated_at": utcnow(),
        }
    )
    ctx.projects.save(project)
    print(
        f"  剧本 v{version.version_no} active（fresh={fresh}），状态回 "
        f"{target_status.value}",
        flush=True,
    )

    # 3. 分镜（已有草稿则复用，不重复生成）
    print("== 分镜 ==", flush=True)
    if ctx.storyboards.latest_draft(pid) is None:
        known_failed = known_failed_ids()
        svc.dispatch(pid, "generate_storyboard")
        wait_until_idle(worker, ctx, pid, "storyboard", known_failed)
    sb = ctx.storyboards.latest_draft(pid)
    if sb is None:
        raise SystemExit("没有分镜草稿")
    segs = sorted(sb.content.segments, key=lambda s: (s.scene_id, s.index))
    total = sum(s.duration_sec for s in segs)
    print(f"  分镜 {len(segs)} 段，Σ={total}s", flush=True)
    scenes = {sc.id: sc for sc in content.scenes}
    claimed: dict[str, set[int]] = {}
    for seg in segs:
        for shot in seg.shots:
            claimed.setdefault(seg.scene_id, set()).update(shot.beat_refs)
        words = [len(shot.action.split()) for shot in seg.shots]
        print(
            f"  {seg.segment_key} {seg.duration_sec}s"
            f" action词数={words} soundscape={'Y' if seg.soundscape else '-'}",
            flush=True,
        )
    for scene_id, sc in scenes.items():
        must = {
            i + 1
            for i, b in enumerate(sc.beats)
            if b.type in (BeatType.ACTION, BeatType.ON_SCREEN_TEXT)
        }
        got = claimed.get(scene_id, set())
        if must:
            print(f"  场景 {scene_id} 节拍认领 {len(must & got)}/{len(must)}", flush=True)
    print("  —— S01G01 编译提示词 ——", flush=True)
    print(segs[0].h3_prompt.text[:900], flush=True)
    svc.dispatch(pid, "approve_storyboard")

    # 3.5 关键帧阶段（TASK-031 新增：分镜与视频之间的冻结关键帧）
    if hasattr(ProjectStatus, "FRAME_APPROVED"):
        print("== 关键帧 ==", flush=True)
        known_failed = known_failed_ids()
        svc.dispatch(pid, "generate_keyframes")
        wait_until_idle(worker, ctx, pid, "keyframes", known_failed)
        for frame in ctx.frames.list_by_project(pid):
            if not frame.approved and frame.status.value in {"ready", "uploaded"}:
                svc.dispatch(pid, "approve_frame_image", {
                    "frame_image_id": frame.frame_image_id,
                    "approved": True,
                })
        svc.dispatch(pid, "approve_keyframes", {"allow_tail_fallback": True})
        print(
            f"  关键帧 {len(ctx.frames.list_by_project(pid))} 张，已确认",
            flush=True,
        )

    # 4. 视频
    print("== 视频生产 ==", flush=True)
    t0 = time.time()
    known_failed = known_failed_ids()
    svc.dispatch(pid, "produce_video", {"scope": "all"})
    wait_until_idle(worker, ctx, pid, "video", known_failed)
    print(f"  视频完成，用时 {int(time.time() - t0)}s", flush=True)
    ffmpeg = FFmpegService(settings.ffmpeg_path)
    clips = ctx.clips.list_by_project(pid)
    if clips:
        dims = ffmpeg.probe(settings.media_dir / clips[0].file_path)
        print(
            f"  首段实测 {dims['width']}×{dims['height']}（期望 480×864）"
            f" 时长 {dims['duration_sec']:.2f}s",
            flush=True,
        )

    # 5. 合成
    print("== 合成 ==", flush=True)
    known_failed = known_failed_ids()
    svc.dispatch(pid, "compose")
    wait_until_idle(worker, ctx, pid, "compose", known_failed)

    final = ctx.projects.get(pid)
    films = ctx.films.list_by_project(pid)
    print(f"\n最终状态：{final.status.value}", flush=True)
    if films:
        film = films[0]
        path = ctx.settings.media_dir / film.file_path
        probed = ffmpeg.probe(path)
        print(
            f"成片 v{film.version_no}: {path} | {film.duration_sec:.1f}s |"
            f" {probed['width']}×{probed['height']} | 字幕版：{film.subtitle_path}",
            flush=True,
        )
    coins = 0
    for job in ctx.jobs.list_all():
        if job.project_id == pid:
            for call in ctx.calls.list_by_job(job.job_id):
                coins += int(call.usage.get("coins") or 0)
    print(f"总消耗：约 {coins} RH 币", flush=True)
    return 0 if final.status is ProjectStatus.COMPOSED else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (LlmError, RunningHubError, DomainError) as exc:
        print(f"E2E 失败：{exc}")
        sys.exit(1)
