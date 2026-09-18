"""TASK-029 端到端质量测试驱动：分阶段跑流水线，每阶段暂停供人工验收。

用法：python scripts/test_pipeline.py <command> [args]
  create                        建项目（雨夜关东煮）+ 生成剧本 + 等待完成 + 打印剧本
  script-dump                   打印 active 剧本 JSON
  approve-script                批准剧本
  assets-gen                    生成资产+资产图，等待完成，打印图片清单
  image-regen <img_id> [--seed N]  对某张资产图同资产重出一张（新版本）
  image-del <img_id>            删除资产图（磁盘留底）
  approve-images                批准全部 ready 的资产图
  approve-assets                提交资产门
  sb-gen                        生成分镜 + 等待 + 打印段落详情与参考图绑定
  sb-dump                       打印 active 分镜详情与参考图绑定
  approve-storyboard            批准分镜
  video <first|all|<seg_key>>   生产视频（scope 透传 produce_video）
  seg-regen <seg_key> [--prompt-file F]  重出某段（可带提示词覆盖文件）
  compose                       合成成片（含烧字幕）+ 打印成片路径
  status                        项目状态/工件计数/币数汇总
  wait                          只跑 Worker 直到队列空闲（中断恢复用）

真实调用 DeepSeek + RunningHub。所有产物落 data/。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.adapters.ffmpeg_svc import FFmpegService  # noqa: E402
from server.adapters.image import build_image_provider_from_settings  # noqa: E402
from server.adapters.llm import build_llm_from_settings  # noqa: E402
from server.adapters.video import build_video_provider_from_settings  # noqa: E402
from server.app.context import AppContext  # noqa: E402
from server.app.handlers import build_handlers  # noqa: E402
from server.app.usecases import WorkbenchService  # noqa: E402
from server.domain.enums import AssetImageStatus, JobStatus  # noqa: E402
from server.infra.config import get_settings  # noqa: E402
from server.infra.db import get_engine  # noqa: E402
from server.infra.settings_store import apply_overrides, load_overrides  # noqa: E402
from server.worker.engine import EngineConfig, WorkerEngine  # noqa: E402

POLL_TIMEOUT = 5400

TITLE = "雨夜关东煮（E2E质量测试）"
IDEA = (
    "深夜的街角便利店，值末班的年轻女店员小满正准备关店，一位浑身被雨淋透的老人"
    "走进来，想买一碗早就停售的关东煮。小满为自己煮了最后一碗，两人聊起「有些味道"
    "是留给等它的人的」。老人临走留下一枚刻着月牙的黄铜怀表抵账，小满追出门，"
    "雨夜长街已空无一人，只有怀表在掌心微微发烫。"
)
PARAMS = {
    "genre": "温情治愈+微悬疑",
    "style": "电影写实，雨夜冷蓝调与便利店暖光对比，35mm 胶片感",
    "target_duration_sec": 40,
    "scene_count": 3,
    "ratio": "16:9",
    "resolution": "768P",
    "prompt_lang": "en",
}


def build() -> tuple[WorkerEngine, AppContext, WorkbenchService, object]:
    settings = apply_overrides(get_settings(), load_overrides(get_engine()))
    ctx = AppContext.build(settings, get_engine())
    from server.app.agents import Agents

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
    return worker, ctx, WorkbenchService(ctx), settings


def wait_until_idle(worker: WorkerEngine, ctx: AppContext, pid: str, label: str) -> None:
    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        try:
            worker.run_once()
        except Exception as exc:  # 引擎在重试耗尽时会抛供应商异常，记录后继续走状态机
            print(f"  [warn] worker 异常：{exc}", flush=True)
        jobs = [j for j in ctx.jobs.list_all() if j.project_id == pid]
        failed = [j for j in jobs if j.status is JobStatus.FAILED]
        busy = [j for j in jobs if j.status in {JobStatus.PENDING, JobStatus.RUNNING}]
        # 供应商队列满（TASK_QUEUE_MAXED）是瞬时限制：重置任务 60s 后再试
        queued = [
            j for j in failed
            if j.error and "TASK_QUEUE_MAXED" in (j.error.message or "")
        ]
        for j in queued:
            print(f"  [queue] {j.type.value} 供应商队列满，60s 后自动重试", flush=True)
            ctx.jobs.save(
                j.model_copy(update={"status": JobStatus.PENDING, "attempts": 0}),
                next_attempt_at=_utc_now_plus(60),
            )
        failed = [j for j in failed if j not in queued]
        if queued:
            time.sleep(10)
            continue
        if failed:
            # 引擎可能正在退避重试：等 15s 复查，仍在才算真失败
            time.sleep(15)
            still_ids = {
                j.job_id
                for j in ctx.jobs.list_all()
                if j.project_id == pid and j.status is JobStatus.FAILED
            }
            really = [j for j in failed if j.job_id in still_ids]
            if not really:
                continue
            for j in really:
                print(f"  !! 任务失败 {j.type.value}: {j.error.code} {j.error.message}")
            raise SystemExit(2)
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


def _utc_now_plus(sec: int):
    from datetime import datetime, timedelta

    return datetime.utcnow() + timedelta(seconds=sec)


def jdump(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def dump_script(ctx: AppContext, pid: str) -> None:
    sv = ctx.scripts.active(pid) or ctx.scripts.latest_draft(pid)
    if sv is None:
        print("无 active/草稿剧本")
        return
    jdump(
        {
            "script_version_id": sv.script_version_id,
            "version_no": sv.version_no,
            "status": sv.status.value,
            "total_est_seconds": sv.content.total_est_seconds,
            "content": sv.content.model_dump(),
        }
    )


def dump_images(ctx: AppContext, pid: str) -> None:
    assets = {a.asset_id: a for a in ctx.assets.list_assets(pid)}
    rows = []
    for img in ctx.assets.list_images(project_id=pid):
        asset = assets.get(img.asset_id)
        rows.append(
            {
                "asset_image_id": img.asset_image_id,
                "asset": f"{asset.kind.value}:{asset.name}" if asset else "?",
                "view_label": img.view_label,
                "status": img.status.value,
                "approved": img.approved,
                "file": str(ctx.settings.media_dir / img.file_path) if img.file_path else "",
                "prompt": img.prompt,
            }
        )
    jdump(rows)


def dump_storyboard(ctx: AppContext, svc: WorkbenchService, pid: str) -> None:
    sb = ctx.storyboards.active(pid) or ctx.storyboards.latest_draft(pid)
    if sb is None:
        print("无 active/草稿分镜")
        return
    project = ctx.projects.get(pid)
    previews = svc.segment_reference_previews(project, sb)
    segs = []
    for seg in sb.content.segments:
        pv = previews.get(seg.segment_key, {})
        segs.append(
            {
                "segment_key": seg.segment_key,
                "scene_id": seg.scene_id,
                "index": seg.index,
                "duration_sec": seg.duration_sec,
                "shots": [s.model_dump() for s in seg.shots],
                "asset_refs": [r.model_dump() for r in seg.asset_refs],
                "continuity": seg.continuity.model_dump(),
                "numbering_ok": pv.get("numbering_ok"),
                "pictures": pv.get("pictures"),
                "h3_prompt": seg.h3_prompt.text,
            }
        )
    jdump(
        {
            "storyboard_version_id": sb.storyboard_version_id,
            "version_no": sb.version_no,
            "status": sb.status.value,
            "segment_count": len(sb.content.segments),
            "total_sec": sum(s.duration_sec for s in sb.content.segments),
            "segments": segs,
        }
    )


def status(ctx: AppContext, pid: str) -> None:
    project = ctx.projects.get(pid)
    jobs = [j for j in ctx.jobs.list_all() if j.project_id == pid]
    coins = 0
    for job in jobs:
        for call in ctx.calls.list_by_job(job.job_id):
            coins += int(call.usage.get("coins") or 0)
    clips = ctx.clips.list_by_project(pid)
    films = ctx.films.list_by_project(pid)
    jdump(
        {
            "project_id": pid,
            "status": project.status.value,
            "params": (
                project.params.model_dump()
                if hasattr(project.params, "model_dump")
                else project.params
            ),
            "assets": len(ctx.assets.list_assets(pid)),
            "asset_images": len(ctx.assets.list_images(project_id=pid)),
            "jobs_total": len(jobs),
            "jobs_failed": sum(1 for j in jobs if j.status is JobStatus.FAILED),
            "clips": [
                {
                    "segment_key": c.segment_key,
                    "duration": c.duration_sec,
                    "file": c.file_path,
                    "tail": c.tail_frame_path,
                }
                for c in clips
            ],
            "films": [
                {
                    "version_no": f.version_no,
                    "file": str(ctx.settings.media_dir / f.file_path),
                    "subtitle": (
                        str(ctx.settings.media_dir / f.subtitle_path)
                        if f.subtitle_path
                        else ""
                    ),
                    "duration": f.duration_sec,
                    "segments": len(f.segment_keys),
                }
                for f in films
            ],
            "coins_spent": coins,
        }
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command")
    ap.add_argument("arg", nargs="?", default="")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--prompt-file", default="")
    ap.add_argument("--project-id", default="")
    args = ap.parse_args()

    worker, ctx, svc, settings = build()
    pid = args.project_id

    if args.command == "create":
        project = svc.create_project(title=TITLE, idea=IDEA, params=dict(PARAMS))
        pid = project.project_id
        print(f"项目创建：{pid}")
        svc.dispatch(pid, "generate_script")
        wait_until_idle(worker, ctx, pid, "script")
        dump_script(ctx, pid)
        return 0

    if not pid:
        pid = _latest_project(ctx) if not args.project_id else args.project_id
    print(f"project={pid} command={args.command}")

    cmd = args.command
    if cmd == "script-dump":
        dump_script(ctx, pid)
    elif cmd == "approve-script":
        svc.dispatch(pid, "approve_script")
        print("剧本已批准")
    elif cmd == "assets-gen":
        svc.dispatch(pid, "generate_assets")
        wait_until_idle(worker, ctx, pid, "assets")
        dump_images(ctx, pid)
    elif cmd == "image-regen":
        img = next(
            (i for i in ctx.assets.list_images(project_id=pid) if i.asset_image_id == args.arg),
            None,
        )
        if img is None:
            raise SystemExit(f"图片不存在：{args.arg}")
        payload = {"asset_id": img.asset_id}
        if args.seed is not None:
            payload["seed"] = args.seed
        svc.dispatch(pid, "generate_asset_image", payload)
        wait_until_idle(worker, ctx, pid, "image-regen")
        dump_images(ctx, pid)
    elif cmd == "image-del":
        svc.dispatch(pid, "delete_asset_image", {"asset_image_id": args.arg})
        print(f"已删除 {args.arg}")
    elif cmd == "approve-images":
        n = 0
        for img in ctx.assets.list_images(project_id=pid):
            if not img.approved and img.status is AssetImageStatus.READY:
                svc.dispatch(pid, "approve_asset_image", {"asset_image_id": img.asset_image_id})
                n += 1
        print(f"已批准 {n} 张")
    elif cmd == "approve-assets":
        svc.dispatch(pid, "approve_assets")
        print("资产门已提交")
    elif cmd == "sb-gen":
        svc.dispatch(pid, "generate_storyboard")
        wait_until_idle(worker, ctx, pid, "storyboard")
        dump_storyboard(ctx, svc, pid)
    elif cmd == "sb-dump":
        dump_storyboard(ctx, svc, pid)
    elif cmd == "approve-storyboard":
        svc.dispatch(pid, "approve_storyboard")
        print("分镜已批准")
    elif cmd == "video":
        scope = {"first": "first_only", "all": "all"}.get(args.arg, args.arg)
        svc.dispatch(pid, "produce_video", {"scope": scope})
        wait_until_idle(worker, ctx, pid, f"video:{scope}")
        status(ctx, pid)
    elif cmd == "seg-regen":
        payload = {"segment_key": args.arg}
        if args.prompt_file:
            payload["prompt_overrides"] = Path(args.prompt_file).read_text(encoding="utf-8")
        svc.dispatch(pid, "regenerate_segment", payload)
        wait_until_idle(worker, ctx, pid, f"seg-regen:{args.arg}")
        status(ctx, pid)
    elif cmd == "compose":
        svc.dispatch(pid, "compose")
        wait_until_idle(worker, ctx, pid, "compose")
        status(ctx, pid)
    elif cmd == "status":
        status(ctx, pid)
    elif cmd == "wait":
        wait_until_idle(worker, ctx, pid, "resume")
        status(ctx, pid)
    else:
        raise SystemExit(f"未知命令：{cmd}")
    return 0


def _latest_project(ctx: AppContext) -> str:
    projects = ctx.projects.list_all()
    if not projects:
        raise SystemExit("无项目，请先 create")
    return projects[0].project_id


if __name__ == "__main__":
    sys.exit(main())
