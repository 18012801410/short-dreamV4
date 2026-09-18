"""EP001 全链重跑（节拍流 + 画幅优化验证）：9:16 竖屏，真实调用 LLM + RunningHub。

用法：python scripts/run_ep001.py
复用 p-b27600b52ce7 的 EP001 完整剧本作为想法；验证点：
  1. 剧本带节拍流（action/sfx/on_screen_text 等，dialogues 派生 + 语气归位）
  2. 资产卡跟 9:16 竖版 + 陆峥半身/全身 A/B（同 seed）
  3. 分镜节拍 100% 认领 + 编译提示词密度
  4. 视频成片 480×864 竖版（画幅换算）
"""

from __future__ import annotations

import re
import sqlite3
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
from server.domain.enums import AssetKind, BeatType, JobStatus, ProjectStatus  # noqa: E402
from server.domain.errors import DomainError  # noqa: E402
from server.infra.config import get_settings  # noqa: E402
from server.infra.db import get_engine  # noqa: E402
from server.infra.settings_store import apply_overrides, load_overrides  # noqa: E402
from server.worker.engine import EngineConfig, WorkerEngine  # noqa: E402

SOURCE_PROJECT = "p-b27600b52ce7"
POLL_TIMEOUT = 4500
SEED = 20260915
FULLBODY_REPL = (
    "full-body standing portrait, the entire body from head to toe visible, "
    "standing naturally, centered"
)


def make_full_body(prompt: str) -> str:
    replaced = re.sub(r"bust portrait[^,]*", FULLBODY_REPL, prompt, count=1)
    if replaced == prompt:
        replaced = "full-body character reference card, head-to-toe visible, " + prompt
    return replaced


def wait_until_idle(worker: WorkerEngine, ctx: AppContext, pid: str, label: str) -> None:
    deadline = time.time() + POLL_TIMEOUT
    last = ""
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

    conn = sqlite3.connect(Path(settings.data_dir) / "app.db")
    idea = conn.execute(
        "select idea from projects where project_id=?", (SOURCE_PROJECT,)
    ).fetchone()[0]
    conn.close()

    project = svc.create_project(
        title="代驾背锅夜（EP001 节拍流验证）",
        idea=idea,
        params={
            "genre": "都市悬疑短剧",
            "style": (
                "cinematic realistic style, film grain, "
                "dramatic natural lighting, 35mm photography"
            ),
            "target_duration_sec": 45,
            "scene_count": 4,
            "ratio": "9:16",
            "resolution": "768P",
            "prompt_lang": "en",
        },
    )
    pid = project.project_id
    print(f"项目创建：{pid}（9:16 竖屏）", flush=True)

    # -- 阶段 1：剧本（节拍流） -------------------------------------------------
    print("== 阶段 1：剧本 ==", flush=True)
    svc.dispatch(pid, "generate_script")
    wait_until_idle(worker, ctx, pid, "script")
    content = ctx.scripts.latest_draft(pid).content
    for sc in content.scenes:
        counts: dict[str, int] = {}
        for b in sc.beats:
            counts[b.type.value] = counts.get(b.type.value, 0) + 1
        print(
            f"  {sc.id} {sc.title}｜beats={len(sc.beats)} {counts}｜"
            f"dialogues={len(sc.dialogues)}（语气 {sum(1 for d in sc.dialogues if d.tone)} 句）",
            flush=True,
        )
        actions = [b.text for b in sc.beats if b.type is BeatType.ACTION]
        if actions:
            print(f"    action 示例：{actions[0][:60]}", flush=True)
    if content.warnings:
        print(f"  warnings：{content.warnings}", flush=True)
    svc.dispatch(pid, "approve_script")

    # -- 阶段 2：资产（9:16 卡 + A/B） -----------------------------------------
    print("== 阶段 2：资产 ==", flush=True)
    svc.dispatch(pid, "generate_assets")
    wait_until_idle(worker, ctx, pid, "assets")
    assets = ctx.assets.list_assets(pid)
    print(f"  资产 {len(assets)} 个", flush=True)

    char = next(
        (a for a in assets if a.kind is AssetKind.CHARACTER and "陆峥" in a.name),
        next(a for a in assets if a.kind is AssetKind.CHARACTER),
    )
    card_prompt = char.image_plan[0].image_prompt
    svc.dispatch(pid, "generate_asset_image", {
        "asset_id": char.asset_id, "view_label": "主设定", "seed": SEED,
    })
    svc.dispatch(pid, "generate_asset_image", {
        "asset_id": char.asset_id, "view_label": "主设定",
        "extra_prompt": make_full_body(card_prompt), "seed": SEED,
    })
    wait_until_idle(worker, ctx, pid, "ab-test")
    imgs = [i for i in ctx.assets.list_images(asset_id=char.asset_id) if i.version_no > 1]
    for i in sorted(imgs, key=lambda x: x.version_no):
        print(f"  A/B v{i.version_no}: {ctx.settings.media_dir / i.file_path}", flush=True)

    for image_row in ctx.assets.list_images(project_id=pid):
        if (
            not image_row.approved
            and image_row.version_no == 1
            and image_row.status.value in {"ready", "uploaded"}
        ):
            image_row.approved = True
            ctx.assets.save_image(image_row)
    svc.dispatch(pid, "approve_assets")

    # -- 阶段 3：分镜（节拍认领 + 编译密度） ------------------------------------
    print("== 阶段 3：分镜 ==", flush=True)
    svc.dispatch(pid, "generate_storyboard")
    wait_until_idle(worker, ctx, pid, "storyboard")
    sb = ctx.storyboards.active(pid)
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
            f"  {seg.segment_key} {seg.duration_sec}s "
            f"action词数={words} soundscape={'Y' if seg.soundscape else '-'}",
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

    # -- 阶段 3.5：关键帧（TASK-031） ------------------------------------------
    print("== 阶段 3.5：关键帧 ==", flush=True)
    svc.dispatch(pid, "generate_keyframes")
    wait_until_idle(worker, ctx, pid, "frame")
    for frame_row in ctx.frames.list_by_project(pid):
        if not frame_row.approved and frame_row.status.value in {"ready", "uploaded"}:
            frame_row.approved = True
            ctx.frames.save(frame_row)
    svc.dispatch(pid, "approve_keyframes", {})
    print(f"  关键帧 {len(ctx.frames.list_by_project(pid))} 张（已全部批准）", flush=True)

    # -- 阶段 4：视频全量（验证竖版） -------------------------------------------
    print("== 阶段 4：视频生产 ==", flush=True)
    t0 = time.time()
    svc.dispatch(pid, "produce_video", {"scope": "all"})
    wait_until_idle(worker, ctx, pid, "video")
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

    # -- 阶段 5：合成 -----------------------------------------------------------
    print("== 阶段 5：合成 ==", flush=True)
    svc.dispatch(pid, "compose")
    wait_until_idle(worker, ctx, pid, "compose")

    final = ctx.projects.get(pid)
    films = ctx.films.list_by_project(pid)
    print(f"\n最终状态：{final.status.value}", flush=True)
    if films:
        film = films[0]
        path = ctx.settings.media_dir / film.file_path
        probed = ffmpeg.probe(path)
        print(
            f"成片 v{film.version_no}: {path} | {film.duration_sec:.1f}s | "
            f"{probed['width']}×{probed['height']} | 字幕版：{film.subtitle_path}",
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
