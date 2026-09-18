"""Worker handlers（TASK-008-012）：真实 Provider 编排，Job 终态与工件落库。

每个 handler：读输入 → 调 Provider/Agent → 写工件 → 推进项目状态 → 记 ProviderCall。
已知适配器/领域错误转为 Outcome(failed, JobError)，不让 Worker 进程崩。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from functools import wraps
from typing import Any

from server.adapters.ffmpeg_svc import FFmpegError, FFmpegService
from server.adapters.llm.base import LlmError
from server.adapters.runninghub import RunningHubError
from server.app.agents import Agents
from server.app.context import AppContext
from server.app.media import (
    abs_media_path,
    film_rel,
    segment_tail_rel,
)
from server.domain.entities import (
    Asset,
    AssetImage,
    Clip,
    Film,
    Job,
    JobError,
    JobType,
    ProviderCall,
    ScriptVersion,
    StoryboardVersion,
    utcnow,
)
from server.domain.enums import (
    AssetImageStatus,
    AssetKind,
    FilmStatus,
    JobStatus,
    ProjectStatus,
    SeriesStatus,
    VideoMode,
)
from server.domain.errors import DomainError, ValidationFailedError
from server.domain.job import mark_succeeded
from server.domain.outline_quality import validate_series_outline
from server.domain.project import apply_action
from server.domain.textnorm import negative_for_kind
from server.worker.engine import Handler, Outcome


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def error_outcome(exc: Exception) -> Outcome:
    if isinstance(exc, (RunningHubError, LlmError)):
        return Outcome(
            status=JobStatus.FAILED,
            error=JobError(code="PROVIDER_ERROR", message=exc.message, provider_code=exc.code),
        )
    if isinstance(exc, FFmpegError):
        return Outcome(
            status=JobStatus.FAILED,
            error=JobError(code="FFMPEG_ERROR", message=str(exc)),
        )
    if isinstance(exc, ValidationFailedError):
        # 校验明细并入 message（JobError 无 details 字段）：页面直接看到
        # 具体哪条没过，不用翻 worker 日志（2026-09-18 分镜失败排查的教训）
        errors = (exc.details or {}).get("errors")
        detail_text = ""
        if isinstance(errors, list) and errors:
            detail_text = "\n- " + "\n- ".join(str(e)[:300] for e in errors[:10])
        return Outcome(
            status=JobStatus.FAILED,
            error=JobError(code="VALIDATION_SEGMENT", message=exc.message + detail_text),
        )
    if isinstance(exc, DomainError):
        return Outcome(
            status=JobStatus.FAILED,
            error=JobError(code=exc.code, message=exc.message),
        )
    return Outcome(status=JobStatus.FAILED, error=JobError(code="UNKNOWN", message=str(exc)))


def handler(fn: Callable[[Any, Job], Outcome]) -> Handler:
    @wraps(fn)
    def wrapper(self: Handlers, job: Job) -> Outcome:
        try:
            return fn(self, job)
        except Exception as exc:  # noqa: BLE001 - 统一转 failed outcome
            return error_outcome(exc)

    return wrapper


def record_call(
    ctx: AppContext, job: Job, provider: str, model: str, kind: str, usage: dict, ok: bool = True
) -> None:
    ctx.calls.add(
        ProviderCall(
            call_id=new_id("call"),
            job_id=job.job_id,
            provider=provider,
            model=model,
            kind=kind,
            usage=usage,
            ok=ok,
        )
    )


class Handlers:
    def __init__(
        self,
        ctx: AppContext,
        agents: Agents,
        *,
        image=None,
        video=None,
        ffmpeg: FFmpegService | None = None,
    ) -> None:
        self.ctx = ctx
        self.agents = agents
        self.image = image
        self.video = video
        self.ffmpeg = ffmpeg or FFmpegService(ctx.settings.ffmpeg_path)

    # -- script_gen ---------------------------------------------------------

    def handle_script_gen(self, job: Job) -> Outcome:
        project = self.ctx.projects.get(job.project_id)
        if project is None:
            raise DomainError("NOT_FOUND", f"项目不存在：{job.project_id}")
        series_id = str(job.payload.get("series_id") or "")
        if series_id:
            # 系列分集（TASK-047）：全剧大纲 + 本集梗概驱动，连载法则注入
            content = self._run_series_episode_script(project, job, series_id)
        else:
            content = self.agents.run_script(project.idea, project.params.model_dump())
        version_no = len(self.ctx.scripts.list_by_project(project.project_id)) + 1
        version = ScriptVersion(
            script_version_id=new_id("sv"),
            project_id=project.project_id,
            version_no=version_no,
            content=content,
            source=f"llm:{job.job_id}",
        )
        self.ctx.scripts.add(version)
        self.ctx.projects.save(apply_action(project, "script_generated"))
        record_call(self.ctx, job, "llm", self.ctx.settings.llm_model, "llm", {"tokens": 0})
        return mark_succeeded(job)

    def _run_series_episode_script(self, project, job: Job, series_id: str):
        series_row = self.ctx.series.get(series_id)
        if series_row is None or series_row.outline is None:
            raise DomainError("NOT_FOUND", f"系列不存在或缺少大纲：{series_id}")
        number = int(job.payload.get("episode_no") or project.episode_no or 0)
        episode = next(
            (e for e in series_row.outline.episodes if e.episode_no == number), None
        )
        if episode is None:
            raise DomainError("NOT_FOUND", f"系列大纲没有第 {number} 集")
        prev = next(
            (e for e in series_row.outline.episodes if e.episode_no == number - 1), None
        )
        prev_ending = prev.ending_hook if prev is not None else ""
        return self.agents.run_episode_script(
            series_row.outline,
            episode,
            prev_ending,
            series_row.params.model_dump(),
        )

    # -- series_outline_gen（TASK-047） ---------------------------------------

    def handle_series_outline_gen(self, job: Job) -> Outcome:
        """系列大纲生成（LLM，零币）：产出后立即跑确定性质量门。

        硬门未过不阻断落库——大纲停在 OUTLINE_READY 带 warnings，由人在
        页面修订或修订后确认（approve 时才硬拦）。
        """
        series_row = self.ctx.series.get(job.project_id)
        if series_row is None:
            raise DomainError("NOT_FOUND", f"系列不存在：{job.project_id}")
        outline = self.agents.run_series_outline(
            series_row.idea, series_row.params.model_dump()
        )
        issues = validate_series_outline(
            outline, expected_episodes=series_row.params.episode_count
        )
        outline = outline.model_copy(update={"warnings": issues})
        moved = series_row.model_copy(
            update={
                "outline": outline,
                "status": SeriesStatus.OUTLINE_READY,
                "updated_at": utcnow(),
            }
        )
        self.ctx.series.save(moved)
        record_call(self.ctx, job, "llm", self.ctx.settings.llm_model, "llm", {})
        return mark_succeeded(job)

    # -- asset_extract ------------------------------------------------------

    def handle_asset_extract(self, job: Job) -> Outcome:
        pid = job.project_id
        project = self.ctx.projects.get(pid)
        if project is None:
            raise DomainError("NOT_FOUND", f"项目不存在：{pid}")
        # 资产提示词（含资产卡口径）由本进程的 AssetAgent 现场撰写，随后每张图都要
        # 扣币。进程代码过期 = 按旧口径写提示词并照常付费（实测：改完全身卡规则后，
        # 旧 Worker 仍产出 4 张半身卡 + 2 张多视图空镜卡，白花约 138 币，页面无异常）。
        # 所以这里在**花钱之前**硬拦：先重启，再抽取。
        from server.infra.buildinfo import freshness

        fresh = freshness()
        if fresh.stale:
            raise DomainError("STALE_CODE", fresh.describe())
        script = self.ctx.scripts.active(pid)
        if script is None:
            raise DomainError("NOT_FOUND", "缺少 active 剧本")
        # 重新抽取 = 整体替换：清掉旧资产与其图片行（磁盘文件保留），
        # 避免新旧资产卡叠加；下游分镜如引用旧资产会 REF_MISSING，需重新生成分镜。
        self.ctx.assets.delete_assets_for_project(pid)
        plan = self.agents.run_assets(script.content, project.params.style, project.params.ratio)
        from server.app.media import size_for_asset_card

        width, height = size_for_asset_card(project.params.ratio)
        enqueued: list[Job] = []
        for draft in plan.assets:
            asset = Asset(
                asset_id=new_id("asset"),
                project_id=pid,
                kind=draft.kind,
                name=draft.name,
                description=draft.description,
                visual_anchor=draft.visual_anchor,
                image_plan=draft.image_plan,
            )
            self.ctx.assets.add_asset(asset)
            for item in asset.image_plan:
                image_prompt = item.image_prompt
                # 提示词消毒兜底：场景剥人形词；道具剥手/人/场景词并确保 isolated 纯白底
                if asset.kind is AssetKind.SCENE:
                    from server.domain.textnorm import sanitize_scene_prompt

                    image_prompt = sanitize_scene_prompt(image_prompt)[0]
                elif asset.kind is AssetKind.PROP:
                    from server.domain.textnorm import sanitize_prop_prompt

                    image_prompt = sanitize_prop_prompt(image_prompt)[0]
                image_row = AssetImage(
                    asset_image_id=new_id("img"),
                    asset_id=asset.asset_id,
                    version_no=1,
                    view_label=item.view_label,
                    prompt=image_prompt,
                    status=AssetImageStatus.GENERATING,
                )
                self.ctx.assets.add_image(image_row)
                enqueued.extend(
                    self._enqueue_image_job(
                        project.project_id,
                        image_row,
                        width,
                        height,
                        negative_for_kind(asset.kind),
                    )
                )
        record_call(self.ctx, job, "llm", self.ctx.settings.llm_model, "llm", {})
        self.ctx.projects.save(apply_action(project, "assets_generated"))
        return mark_succeeded(job)

    def _enqueue_image_job(
        self, pid: str, image_row: AssetImage, width: int, height: int, negative: str | None = None
    ) -> list[Job]:
        from server.app.media import asset_image_rel

        rel = asset_image_rel(pid, image_row.asset_id, image_row.asset_image_id)
        payload = {
            "asset_image_id": image_row.asset_image_id,
            "prompt": image_row.prompt,
            "width": width,
            "height": height,
            "dest_rel": rel,
        }
        if negative:
            payload["negative"] = negative
        job = Job(job_id=new_id("job"), project_id=pid, type=JobType.IMAGE_GEN, payload=payload)
        self.ctx.jobs.insert(job)
        return [job]

    # -- image_gen ----------------------------------------------------------

    def handle_image_gen(self, job: Job) -> Outcome:
        if self.image is None:
            raise RunningHubError("AUTH", "ImageProvider 未配置")
        payload = job.payload
        row = self.ctx.assets.get_image(payload["asset_image_id"])
        if row is None:
            raise DomainError("NOT_FOUND", f"资产图片不存在：{payload['asset_image_id']}")
        dest = abs_media_path(self.ctx.settings, payload["dest_rel"])
        result = self.image.generate(
            prompt=payload["prompt"],
            width=int(payload["width"]),
            height=int(payload["height"]),
            dest=dest,
            seed=payload.get("seed"),
            negative=payload.get("negative"),
        )
        row.file_path = payload["dest_rel"]
        row.provider_ref = result.provider_task_id
        row.status = AssetImageStatus.READY
        self.ctx.assets.save_image(row)
        record_call(
            self.ctx, job, "runninghub-image", "qwen-image", "image",
            {"coins": result.usage.coins, "seconds": result.usage.task_seconds,
             "seed": result.seed},
        )
        return mark_succeeded(job)

    # -- frame_gen（TASK-031） ------------------------------------------------

    def handle_frame_gen(self, job: Job) -> Outcome:
        if self.image is None:
            raise RunningHubError("AUTH", "ImageProvider 未配置")
        payload = job.payload
        row = self.ctx.frames.get(payload["frame_image_id"])
        if row is None:
            raise DomainError("NOT_FOUND", f"关键帧不存在：{payload['frame_image_id']}")
        dest = abs_media_path(self.ctx.settings, payload["dest_rel"])
        # Edit 参考版（TASK-031）：payload 带在场资产卡相对路径时，
        # 解析为绝对路径传给 ImageProvider（适配器据此切图生图工作流并上传参考图）
        reference_files = [
            abs_media_path(self.ctx.settings, rel)
            for rel in payload.get("reference_rel_paths") or []
        ]
        result = self.image.generate(
            prompt=payload["prompt"],
            width=int(payload["width"]),
            height=int(payload["height"]),
            dest=dest,
            seed=payload.get("seed"),
            negative=payload.get("negative"),
            reference_files=reference_files or None,
        )
        row.file_path = payload["dest_rel"]
        row.provider_ref = result.provider_task_id
        # 以任务实际送入的参考图为准回填（TASK-035）：行是入队时登记的，
        # 完成时再盖一次，保证页面看到的"这张图参考了谁"与真实请求一致
        row.reference_paths = list(payload.get("reference_rel_paths") or [])
        row.status = AssetImageStatus.READY
        self.ctx.frames.save(row)
        record_call(
            self.ctx, job, "runninghub-image", "qwen-image", "image",
            {"coins": result.usage.coins, "seconds": result.usage.task_seconds,
             "seed": result.seed},
        )
        self._maybe_frames_ready(job.project_id)
        return mark_succeeded(job)

    def _maybe_frames_ready(self, pid: str) -> None:
        """全部段各有一张非失败关键帧图 → 推进 FRAME_DRAFTING→FRAME_READY。"""
        project = self.ctx.projects.get(pid)
        if project is None or project.status is not ProjectStatus.FRAME_DRAFTING:
            return
        sb = self.ctx.storyboards.active(pid)
        if sb is None:
            return
        images: dict[str, list] = {}
        for image in self.ctx.frames.list_by_project(pid):
            images.setdefault(image.segment_key, []).append(image)
        usable = {
            key
            for key, rows in images.items()
            if any(r.status in {AssetImageStatus.READY, AssetImageStatus.UPLOADED} for r in rows)
        }
        if {s.segment_key for s in sb.content.segments} <= usable:
            self.ctx.projects.save(apply_action(project, "keyframes_generated"))

    # -- storyboard_gen -----------------------------------------------------

    def handle_storyboard_gen(self, job: Job) -> Outcome:
        pid = job.project_id
        project = self.ctx.projects.get(pid)
        if project is None:
            raise DomainError("NOT_FOUND", f"项目不存在：{pid}")
        script = self.ctx.scripts.active(pid)
        if script is None:
            raise DomainError("NOT_FOUND", "缺少 active 剧本")
        assets = self.ctx.assets.list_assets(pid)
        content, _warnings = self.agents.run_storyboard(
            script.content, assets, project.params.model_dump()
        )
        version_no = len(self.ctx.storyboards.list_by_project(pid)) + 1
        version = StoryboardVersion(
            storyboard_version_id=new_id("sbv"),
            project_id=pid,
            version_no=version_no,
            content=content,
        )
        self.ctx.storyboards.add(version)
        self.ctx.projects.save(apply_action(project, "storyboard_generated"))
        record_call(self.ctx, job, "llm", self.ctx.settings.llm_model, "llm", {})
        return mark_succeeded(job)

    # -- video_gen ----------------------------------------------------------

    def handle_video_gen(self, job: Job) -> Outcome:
        if self.video is None:
            raise RunningHubError("AUTH", "VideoProvider 未配置")
        if not self.ffmpeg.available():
            raise FFmpegError("ffmpeg/ffprobe 不可用")
        pid = job.project_id
        snap = job.input_snapshot
        reference_paths = [
            abs_media_path(self.ctx.settings, rel) for rel in snap["reference_paths"]
        ]
        # 连续性：运行时从前段 Clip 取尾帧，作为参考图第一位（<Picture 1>，0.00s 锚定）
        prev_key = snap.get("continuity_prev_segment_key")
        reference_video_abs = None
        if prev_key:
            tail_rel = self._prev_tail_rel(pid, str(prev_key), snap["storyboard_version_id"])
            if tail_rel is None:
                raise DomainError(
                    "DEPENDENCY_BLOCKED",
                    f"前段 {prev_key} 尾帧不可用，无法保证连续性",
                )
            reference_paths.insert(0, abs_media_path(self.ctx.settings, tail_rel))
            # 视频续接（实验性，settings.h3_reference_video 开启时生效）：
            # 前段成片整段作为 referenceVideo。实测该槽位当前语义偏"内容参考"
            # 而非帧 0 续接，默认关闭，仅保留代码路径
            if self.ctx.settings.h3_reference_video:
                prev_video_rel = self._prev_video_rel(
                    pid, str(prev_key), snap["storyboard_version_id"]
                )
                if prev_video_rel:
                    reference_video_abs = abs_media_path(
                        self.ctx.settings, prev_video_rel
                    )
        dest_rel = snap["dest_rel"]
        result = self.video.generate(
            prompt=snap["prompt"],
            duration_sec=int(snap["duration_sec"]),
            reference_files=reference_paths,
            dest=abs_media_path(self.ctx.settings, dest_rel),
            reference_video=reference_video_abs,
            ratio=str(snap.get("ratio") or ""),
        )
        tail_rel = segment_tail_rel(pid, snap["segment_key"], int(snap["version_no"]))
        self.ffmpeg.extract_tail_frame(
            result.dest, abs_media_path(self.ctx.settings, tail_rel)
        )
        clip = Clip(
            clip_id=new_id("clip"),
            project_id=pid,
            segment_key=snap["segment_key"],
            video_job_id=job.job_id,
            storyboard_version_id=snap["storyboard_version_id"],
            file_path=dest_rel,
            tail_frame_path=tail_rel,
            duration_sec=result.duration_sec,
            mode=VideoMode(snap.get("mode", "r2va")),
        )
        self.ctx.clips.add(clip)
        record_call(
            self.ctx, job, "runninghub-video", "MiniMax-H3", "video",
            {"coins": result.usage.coins, "seconds": result.usage.task_seconds,
             "clip_duration": result.duration_sec},
        )
        self._maybe_video_ready(pid, snap["storyboard_version_id"])
        return mark_succeeded(job)

    def _prev_video_rel(self, pid: str, prev_key: str, storyboard_version_id: str) -> str | None:
        clips = [
            c
            for c in self.ctx.clips.list_by_project(pid)
            if c.segment_key == prev_key and c.storyboard_version_id == storyboard_version_id
        ]
        return clips[-1].file_path if clips and clips[-1].file_path else None

    def _prev_tail_rel(self, pid: str, prev_key: str, storyboard_version_id: str) -> str | None:
        clips = [
            c
            for c in self.ctx.clips.list_by_project(pid)
            if c.segment_key == prev_key and c.storyboard_version_id == storyboard_version_id
        ]
        return clips[-1].tail_frame_path if clips and clips[-1].tail_frame_path else None

    def _maybe_video_ready(self, pid: str, storyboard_version_id: str) -> None:
        project = self.ctx.projects.get(pid)
        sb = self.ctx.storyboards.get_by_id(storyboard_version_id)
        if project is None or sb is None:
            return
        if project.status is not ProjectStatus.VIDEO_PRODUCING:
            return
        have = {
            c.segment_key
            for c in self.ctx.clips.list_by_project(pid)
            if c.storyboard_version_id == storyboard_version_id
        }
        if {s.segment_key for s in sb.content.segments} <= have:
            self.ctx.projects.save(apply_action(project, "video_ready"))

    # -- compose ------------------------------------------------------------

    def handle_compose(self, job: Job) -> Outcome:
        pid = job.project_id
        project = self.ctx.projects.get(pid)
        if project is None:
            raise DomainError("NOT_FOUND", f"项目不存在：{pid}")
        if project.status is ProjectStatus.VIDEO_READY:
            # 上次合成失败回退后的重试：把状态拨回 composing，否则成功收尾
            # 的 apply_action("composed") 会因状态不在 COMPOSING 而崩溃
            project = apply_action(project, "compose")
            self.ctx.projects.save(project)
        try:
            film = self._compose(pid)
        except Exception:
            if project.status is ProjectStatus.COMPOSING:
                self.ctx.projects.save(apply_action(project, "compose_failed"))
            raise
        self.ctx.projects.save(apply_action(project, "composed"))
        record_call(self.ctx, job, "ffmpeg", "-", "compose", {"film": film.file_path})
        return mark_succeeded(job)

    def _compose(self, pid: str) -> Film:
        sb = self.ctx.storyboards.active(pid)
        if sb is None:
            raise DomainError("NOT_FOUND", "缺少 active 分镜")
        ordered_keys = [s.segment_key for s in sb.content.segments]
        clips = {
            c.segment_key: c
            for c in self.ctx.clips.list_by_project(pid)
            if c.storyboard_version_id == sb.storyboard_version_id
        }
        missing = [k for k in ordered_keys if k not in clips]
        if missing:
            raise DomainError("NOT_FOUND", f"段视频缺失：{', '.join(missing)}")
        if not self.ffmpeg.available():
            raise FFmpegError("ffmpeg/ffprobe 不可用")
        version_no = len(self.ctx.films.list_by_project(pid)) + 1
        rel = film_rel(pid, version_no)
        paths = [abs_media_path(self.ctx.settings, clips[k].file_path) for k in ordered_keys]
        dest = abs_media_path(self.ctx.settings, rel)
        _, starts = self.ffmpeg.compose_film(paths, dest)
        probed = self.ffmpeg.probe(dest)
        subtitle_rel = self._burn_subtitles(
            pid, sb, clips, ordered_keys, version_no, rel, starts=starts
        )
        film = Film(
            film_id=new_id("film"),
            project_id=pid,
            version_no=version_no,
            file_path=rel,
            subtitle_path=subtitle_rel,
            segment_keys=ordered_keys,
            duration_sec=probed["duration_sec"],
            status=FilmStatus.READY,
        )
        self.ctx.films.add(film)
        return film

    def _burn_subtitles(
        self, pid: str, sb, clips: dict, ordered_keys: list[str],
        version_no: int, film_rel_path: str,
        starts: list[float] | None = None,
    ) -> str:
        """合成后烧录字幕版；无台词或烧录失败都返回空串（不阻断成片）。"""
        from server.app.media import film_srt_rel, film_sub_rel
        from server.app.subtitles import build_film_srt

        try:
            segments = [s for s in sb.content.segments if s.segment_key in ordered_keys]
            srt_text = build_film_srt(segments, clips, starts=starts)
            if not srt_text:
                return ""
            srt_rel = film_srt_rel(pid, version_no)
            sub_rel = film_sub_rel(pid, version_no)
            srt_abs = abs_media_path(self.ctx.settings, srt_rel)
            srt_abs.parent.mkdir(parents=True, exist_ok=True)
            srt_abs.write_text(srt_text, encoding="utf-8")
            self.ffmpeg.burn_subtitles(
                abs_media_path(self.ctx.settings, film_rel_path),
                srt_abs,
                abs_media_path(self.ctx.settings, sub_rel),
            )
            return sub_rel
        except FFmpegError:
            return ""


def build_handlers(
    ctx: AppContext,
    agents: Agents,
    *,
    image=None,
    video=None,
    ffmpeg: FFmpegService | None = None,
) -> dict[JobType, Handler]:
    handlers = Handlers(ctx, agents, image=image, video=video, ffmpeg=ffmpeg)
    return {
        JobType.SCRIPT_GEN: handlers.handle_script_gen,
        JobType.ASSET_EXTRACT: handlers.handle_asset_extract,
        JobType.IMAGE_GEN: handlers.handle_image_gen,
        JobType.STORYBOARD_GEN: handlers.handle_storyboard_gen,
        JobType.FRAME_GEN: handlers.handle_frame_gen,
        JobType.VIDEO_GEN: handlers.handle_video_gen,
        JobType.COMPOSE: handlers.handle_compose,
        JobType.SERIES_OUTLINE_GEN: handlers.handle_series_outline_gen,
    }
