"""REST DTO 序列化（TASK-008）：API_SPEC 形状。"""

from __future__ import annotations

from typing import Any

from server.domain.entities import (
    Asset,
    AssetImage,
    Clip,
    Film,
    Job,
    Project,
    ScriptVersion,
    Series,
    StoryboardVersion,
)


def project_dto(project: Project) -> dict[str, Any]:
    return {
        "id": project.project_id,
        "title": project.title,
        "idea": project.idea,
        "params": project.params.model_dump(mode="json"),
        "status": project.status.value.upper(),
        "series_id": project.series_id or None,
        "episode_no": project.episode_no or None,
        "created_at": project.created_at.isoformat(),
        "updated_at": project.updated_at.isoformat(),
    }


def series_dto(
    series_row: Series, episodes: list[dict] | None = None
) -> dict[str, Any]:
    """系列 DTO（TASK-047）：outline 原样 JSON；episodes 为路由层组装的分集视图。"""
    data: dict[str, Any] = {
        "id": series_row.series_id,
        "title": series_row.title,
        "idea": series_row.idea,
        "params": series_row.params.model_dump(mode="json"),
        "status": series_row.status.value,
        "outline": series_row.outline.model_dump(mode="json") if series_row.outline else None,
        "created_at": series_row.created_at.isoformat(),
        "updated_at": series_row.updated_at.isoformat(),
    }
    if episodes is not None:
        data["episodes"] = [
            {
                **ep,
                "project": project_dto(ep["project"]) if ep.get("project") else None,
            }
            for ep in episodes
        ]
    return data


def script_version_dto(version: ScriptVersion) -> dict[str, Any]:
    return {
        "id": version.script_version_id,
        "version_no": version.version_no,
        "status": version.status.value,
        "source": version.source,
        "content": version.content.model_dump(),
    }


def asset_image_dto(image: AssetImage) -> dict[str, Any]:
    return {
        "id": image.asset_image_id,
        "asset_id": image.asset_id,
        "version_no": image.version_no,
        "view_label": image.view_label,
        "prompt": image.prompt,
        "file_path": image.file_path,
        "url": f"/media/{image.file_path}" if image.file_path else None,
        "status": image.status.value,
        "approved": image.approved,
    }


def frame_image_dto(image) -> dict[str, Any]:
    """段关键帧图 DTO（TASK-031，形状镜像 asset_image_dto）。

    reference_urls（TASK-035）：本张图实际送入的参考图；页面直接展示缩略图，
    让"这张图参考了谁"可见可追溯。
    """
    return {
        "id": image.frame_image_id,
        "project_id": image.project_id,
        "segment_key": image.segment_key,
        "version_no": image.version_no,
        "view_label": image.view_label,
        "grid_cell": image.grid_cell,
        "prompt": image.prompt,
        "file_path": image.file_path,
        "url": f"/media/{image.file_path}" if image.file_path else None,
        "reference_paths": list(image.reference_paths),
        "reference_urls": [f"/media/{p}" for p in image.reference_paths],
        "status": image.status.value,
        "approved": image.approved,
    }


def asset_dto(asset: Asset, images: list[AssetImage]) -> dict[str, Any]:
    return {
        "id": asset.asset_id,
        "kind": asset.kind.value,
        "name": asset.name,
        "description": asset.description,
        "visual_anchor": asset.visual_anchor,
        "image_plan": asset.image_plan,
        "images": [asset_image_dto(i) for i in images if i.asset_id == asset.asset_id],
    }


def storyboard_dto(version: StoryboardVersion, resolution: dict[str, dict] | None = None) -> dict:
    segments = []
    for seg in version.content.segments:
        item = seg.model_dump()
        item["resolution_status"] = (resolution or {}).get(seg.segment_key, {"status": "ok"})
        segments.append(item)
    return {
        "id": version.storyboard_version_id,
        "version_no": version.version_no,
        "status": version.status.value,
        "segments": segments,
    }


def job_dto(job: Job) -> dict[str, Any]:
    return {
        "id": job.job_id,
        "type": job.type.value,
        "status": job.status.value,
        "progress": job.progress,
        "phase": job.phase,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "depends_on": job.depends_on,
        "error": job.error.model_dump() if job.error else None,
        "log": [e.model_dump(mode="json") for e in job.log[-20:]],
        "created_at": job.created_at.isoformat(),
    }


def clip_dto(clip: Clip) -> dict[str, Any]:
    return {
        "id": clip.clip_id,
        "segment_key": clip.segment_key,
        "video_job_id": clip.video_job_id,
        "storyboard_version_id": clip.storyboard_version_id,
        "url": f"/media/{clip.file_path}",
        "tail_frame_url": f"/media/{clip.tail_frame_path}" if clip.tail_frame_path else None,
        "duration_sec": clip.duration_sec,
        "mode": clip.mode.value,
    }


def film_dto(film: Film) -> dict[str, Any]:
    return {
        "id": film.film_id,
        "version_no": film.version_no,
        "url": f"/media/{film.file_path}" if film.file_path else None,
        "subtitle_url": f"/media/{film.subtitle_path}" if film.subtitle_path else None,
        "segment_keys": film.segment_keys,
        "duration_sec": film.duration_sec,
        "status": film.status.value,
        "error": film.error,
    }
