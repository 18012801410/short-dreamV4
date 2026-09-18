"""项目工作台路由（TASK-008-012）：项目/命令/剧本/资产/分镜/段/任务/成片。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, Request, UploadFile

from server.api.serializers import (
    asset_dto,
    clip_dto,
    film_dto,
    frame_image_dto,
    job_dto,
    project_dto,
    script_version_dto,
    storyboard_dto,
)
from server.domain.enums import JobStatus

router = APIRouter()


def _svc(request: Request):
    return request.app.state.service


@router.post("/projects", status_code=201)
def create_project(request: Request, body: dict):
    project = _svc(request).create_project(
        title=str(body.get("title", "")).strip() or "未命名项目",
        idea=str(body.get("idea", "")),
        params=body.get("params"),
    )
    return {"project": project_dto(project)}


@router.get("/projects")
def list_projects(request: Request, include_archived: bool = False):
    return {"projects": [project_dto(p) for p in _svc(request).list_projects(include_archived)]}


@router.get("/projects/{pid}")
def get_project(request: Request, pid: str):
    svc = _svc(request)
    project = svc.get_project(pid)
    data = project_dto(project)
    script = svc.ctx.scripts.active(pid)
    sb = svc.ctx.storyboards.active(pid)
    data["active_script"] = script_version_dto(script) if script else None
    data["stage_stats"] = _stage_stats(svc, pid, sb)
    return data


@router.delete("/projects/{pid}")
def delete_project(request: Request, pid: str):
    """硬删除项目：级联清 DB 全部关联行，媒体文件移入 data/trash（可手动恢复）。

    有 pending/running 任务时返回 409（先取消或等跑完）。
    """
    return _svc(request).delete_project(pid)


def _stage_stats(svc, pid: str, sb) -> dict:
    clips = {c.segment_key: c for c in svc.ctx.clips.list_by_project(pid)}
    segments = list(sb.content.segments) if sb else []
    done = sum(1 for s in segments if s.segment_key in clips)
    return {
        "segments_total": len(segments),
        "segments_done": done,
        "assets_total": len(svc.ctx.assets.list_assets(pid)),
    }


@router.post("/projects/{pid}/translate_prompt")
def translate_prompt(request: Request, pid: str, body: dict):
    """中文修改要求 → 英文图片提示词（TASK-048，LLM 免费调用，不落库）。

    mode=t2i（默认，Qwen-Image 文生图公式）/ mode=edit（Qwen-Image-Edit 编辑指令）。
    带 current_prompt 时走「智能修改」：只落实用户的改动，其余原样保留。
    """
    prompt = _svc(request).translate_prompt(
        pid,
        str(body.get("text", "")),
        mode=str(body.get("mode", "t2i")),
        current_prompt=str(body.get("current_prompt", "")),
    )
    return {"prompt": prompt}


@router.post("/projects/{pid}/commands")
def dispatch_command(request: Request, pid: str, body: dict):
    result = _svc(request).dispatch(pid, str(body.get("type", "")), body.get("payload") or {})
    if "jobs" in result and result.get("ok") is None and result.get("project") is None:
        jobs = result["jobs"]
        return {"jobs": [job_dto(j) for j in jobs]}
    response: dict = {"ok": True}
    keys = ("project", "jobs", "asset_image", "frame_image", "asset",
            "storyboard_version", "stopped")
    for key in keys:
        if key in result:
            value = result[key]
            if key == "project":
                value = project_dto(value)
            elif key == "jobs":
                value = [job_dto(j) for j in value]
            elif key == "asset_image":
                value = asset_dto_image(value)
            elif key == "frame_image":
                value = frame_image_dto(value)
            elif key == "asset":
                value = asset_dto(value, [])
            elif key == "storyboard_version":
                value = storyboard_dto(value)
            response[key] = value
    return response


def asset_dto_image(image_row):
    from server.api.serializers import asset_image_dto

    return asset_image_dto(image_row)


@router.get("/projects/{pid}/script")
def get_script(request: Request, pid: str):
    svc = _svc(request)
    svc.get_project(pid)
    versions = svc.ctx.scripts.list_by_project(pid)
    active = next((v for v in versions if v.status.value == "active"), None)
    draft = next((v for v in versions if v.status.value == "draft"), None)
    return {
        "active": script_version_dto(active) if active else None,
        "draft": script_version_dto(draft) if draft else None,
        "versions": [script_version_dto(v) for v in versions],
    }


@router.get("/projects/{pid}/assets")
def get_assets(request: Request, pid: str):
    svc = _svc(request)
    svc.get_project(pid)
    assets = svc.ctx.assets.list_assets(pid)
    images = svc.ctx.assets.list_images(project_id=pid)
    return {"assets": [asset_dto(a, images) for a in assets]}


@router.get("/projects/{pid}/frames")
def get_frames(request: Request, pid: str):
    """段关键帧图列表（TASK-031）：逐段携带开场锚点描述，按分镜段序排列。

    TASK-035 增补（关键帧页要"看得见、改得动"）：
    - `segment_context`：本帧所属分镜段的内容（场景/时长/镜头动作/逐人开场位置与
      是否在画面内）与自动选中的参考图；
    - `reference_options`：可作参考图的候选（已批准且已落盘的资产图，含缩略图 URL），
      供页面手工指定参考图重抽。
    """
    svc = _svc(request)
    svc.get_project(pid)
    sb = svc.ctx.storyboards.active(pid)
    segments = list(sb.content.segments) if sb else []
    order = [s.segment_key for s in segments]
    descriptions = {s.segment_key: s.keyframe_description for s in segments}
    rows = svc.ctx.frames.list_by_project(pid)
    frames = [
        frame_image_dto(row)
        for row in sorted(rows, key=lambda r: (order.index(r.segment_key), r.version_no))
        if row.segment_key in order
    ]
    beats_zh = svc.shot_beats_zh(pid, segments)
    segment_context = {
        s.segment_key: {
            "scene_id": s.scene_id,
            "duration_sec": s.duration_sec,
            "shot_summaries": [
                (shot.action or shot.description or "").strip() for shot in s.shots
            ],
            "shot_beats_zh": beats_zh.get(s.segment_key, []),
            "placements": [
                {"name": p.name, "placement": p.placement, "in_frame": p.in_frame}
                for p in s.subject_placements
            ],
            "auto_reference_paths": svc.auto_reference_cards(pid, s),
        }
        for s in segments
    }
    reference_options = [
        {
            "image_id": image.asset_image_id,
            "asset_id": asset.asset_id,
            "kind": asset.kind.value,
            "name": asset.name,
            "view_label": image.view_label,
            "path": image.file_path,
            "url": f"/media/{image.file_path}",
        }
        for asset in svc.ctx.assets.list_assets(pid)
        for image in svc.ctx.assets.list_images(asset_id=asset.asset_id)
        if image.approved and image.file_path
    ]
    return {
        "frames": frames,
        "keyframe_descriptions": descriptions,
        "segment_context": segment_context,
        "reference_options": reference_options,
    }


@router.get("/projects/{pid}/storyboard")
def get_storyboard(request: Request, pid: str):
    svc = _svc(request)
    project = svc.get_project(pid)
    versions = svc.ctx.storyboards.list_by_project(pid)
    active = next((v for v in versions if v.status.value == "active"), None)
    draft = next((v for v in versions if v.status.value == "draft"), None)
    known = {a.asset_id for a in svc.ctx.assets.list_assets(pid)}

    def with_resolution(version):
        if version is None:
            return None
        resolution = {}
        for seg in version.content.segments:
            missing = [r.asset_id for r in seg.asset_refs if r.asset_id not in known]
            resolution[seg.segment_key] = (
                {"status": "ref_missing", "assets": missing}
                if missing
                else {"status": "ok"}
            )
        dto = storyboard_dto(version, resolution)
        previews = svc.segment_reference_previews(project, version)
        beats_zh = svc.shot_beats_zh(pid, list(version.content.segments))
        for seg_dto in dto["segments"]:
            preview = previews.get(seg_dto["segment_key"])
            seg_dto["references"] = preview
            # 页面显示的正文 = 生产实际会送出的那一份（TASK-040）：
            # 未人工覆盖 → 实时编译结果；人工覆盖 → 存储文本
            seg_dto["shot_beats_zh"] = beats_zh.get(seg_dto["segment_key"], [])
            if preview and preview.get("prompt_text"):
                seg_dto["h3_prompt"]["text"] = preview["prompt_text"]
                seg_dto["prompt_view"] = (
                    "manual" if preview.get("prompt_manual") else "compiled"
                )
        return dto

    return {"active": with_resolution(active), "draft": with_resolution(draft)}


@router.get("/projects/{pid}/jobs")
def list_jobs(request: Request, pid: str, status: str | None = None, type: str | None = None):
    svc = _svc(request)
    svc.get_project(pid)
    if status:
        jobs = svc.ctx.jobs.list_by_status(JobStatus(status))
    else:
        jobs = svc.ctx.jobs.list_all()
    jobs = [j for j in jobs if j.project_id == pid and (type is None or j.type.value == type)]
    return {"jobs": [job_dto(j) for j in jobs]}


@router.post("/jobs/{job_id}/retry")
def retry_job(request: Request, job_id: str):
    from server.domain.job import retry

    svc = _svc(request)
    job = svc.ctx.jobs.get(job_id)
    if job is None:
        return _not_found(job_id)
    retried = retry(job)
    svc.ctx.jobs.save(retried)
    return {"jobs": [job_dto(svc.ctx.jobs.get(job_id))]}


@router.post("/jobs/{job_id}/cancel")
def cancel_job(request: Request, job_id: str):
    from server.domain.job import cancel as do_cancel

    svc = _svc(request)
    job = svc.ctx.jobs.get(job_id)
    if job is None:
        return _not_found(job_id)
    svc.ctx.jobs.save(do_cancel(job))
    return {"jobs": [job_dto(svc.ctx.jobs.get(job_id))]}


@router.get("/projects/{pid}/segments")
def list_segments(request: Request, pid: str):
    svc = _svc(request)
    svc.get_project(pid)
    sb = svc.ctx.storyboards.active(pid)
    clips = {c.segment_key: c for c in svc.ctx.clips.list_by_project(pid)}
    jobs_by_key: dict[str, dict] = {}
    for job in svc.ctx.jobs.list_all():
        if job.project_id != pid or job.type.value != "video_gen":
            continue
        key = job.input_snapshot.get("segment_key")
        prev = jobs_by_key.get(key)
        if key and (prev is None or job.created_at >= prev["created_at"]):
            jobs_by_key[key] = job_dto(job) | {"created_at": job.created_at}
    segments = []
    for seg in (sb.content.segments if sb else []):
        clip = clips.get(seg.segment_key)
        segments.append({
            "segment_key": seg.segment_key,
            "duration_sec": seg.duration_sec,
            "status": "done" if clip else "pending",
            "clip": clip_dto(clip) if clip else None,
            "job": jobs_by_key.get(seg.segment_key),
        })
    return {"segments": segments}


@router.get("/projects/{pid}/film")
def list_films(request: Request, pid: str):
    svc = _svc(request)
    svc.get_project(pid)
    return {"films": [film_dto(f) for f in svc.ctx.films.list_by_project(pid)]}


@router.post("/projects/{pid}/assets/{asset_id}/upload_image")
def upload_image(
    request: Request,
    pid: str,
    asset_id: str,
    file: Annotated[UploadFile, File()],
):
    content = file.read()
    ext = "." + (file.filename or "upload.png").rsplit(".", 1)[-1].lower()
    result = _svc(request).dispatch(
        pid,
        "upload_asset_image",
        {"asset_id": asset_id, "content": content, "ext": ext},
    )
    return {"ok": True, "asset_image": asset_dto_image(result["asset_image"])}


@router.post("/projects/{pid}/segments/{segment_key}/upload_frame")
def upload_frame(
    request: Request,
    pid: str,
    segment_key: str,
    file: Annotated[UploadFile, File()],
    cell_no: Annotated[int | None, Form()] = None,
):
    """上传段关键帧（绕过生成，直接可批准；TASK-031）。

    多宫格方案A：cell_no 为可选格号（1 起），上传的是某格的替换图时带上。
    """
    content = file.read()
    ext = "." + (file.filename or "upload.png").rsplit(".", 1)[-1].lower()
    result = _svc(request).dispatch(
        pid,
        "upload_frame_image",
        {
            "segment_key": segment_key,
            "content": content,
            "ext": ext,
            "cell_no": cell_no,
        },
    )
    return {"ok": True, "frame_image": frame_image_dto(result["frame_image"])}


def _not_found(job_id: str):
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=404,
        content={"error": {"code": "NOT_FOUND", "message": f"任务不存在：{job_id}", "details": {}}},
    )
