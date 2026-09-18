"""系列（分集剧）路由（TASK-047）：建系列、大纲评审流、批量生成分集剧本。

系列是剧本层概念：series 存全剧大纲，每集是挂 series_id/episode_no 的普通
project——集内制作（资产/分镜/关键帧/视频/成片）全部走既有项目工作台路由。
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from server.api.serializers import job_dto, project_dto, series_dto
from server.domain.enums import SeriesStatus

router = APIRouter()


def _svc(request: Request):
    return request.app.state.service


@router.post("/series", status_code=201)
def create_series(request: Request, body: dict):
    series_row = _svc(request).create_series(
        title=str(body.get("title", "")),
        idea=str(body.get("idea", "")),
        params=body.get("params"),
    )
    return {"series": series_dto(series_row)}


@router.get("/series")
def list_series(request: Request):
    svc = _svc(request)
    out = []
    for series_row in svc.list_series():
        projects = svc.ctx.projects.list_by_series(series_row.series_id)
        out.append(
            {
                **series_dto(series_row),
                "episode_projects": len(projects),
                "statuses": sorted({p.status.value.upper() for p in projects}),
            }
        )
    return {"series": out}


@router.get("/series/{sid}")
def get_series(request: Request, sid: str):
    svc = _svc(request)
    series_row = svc.get_series(sid)
    return {"series": series_dto(series_row, episodes=svc.series_episodes(sid))}


@router.post("/series/{sid}/commands")
def dispatch_series_command(request: Request, sid: str, body: dict):
    result = _svc(request).dispatch_series(
        sid, str(body.get("type", "")), body.get("payload") or {}
    )
    if "jobs" in result and result.get("ok") is None and result.get("series") is None:
        return {"jobs": [job_dto(j) for j in result["jobs"]]}
    response: dict = {"ok": True}
    if "series" in result:
        response["series"] = series_dto(result["series"])
    if "jobs" in result:
        response["jobs"] = [job_dto(j) for j in result["jobs"]]
    if "project" in result:
        response["project"] = project_dto(result["project"])
    for key in ("copied_assets", "copied_images"):
        if key in result:
            response[key] = result[key]
    return response


@router.get("/series/{sid}/outline-status")
def outline_gate_status(request: Request, sid: str):
    """质量门结果快照（页面横幅用）：warnings 已随编辑/生成实时刷新。"""
    svc = _svc(request)
    series_row = svc.get_series(sid)
    outline = series_row.outline
    warnings = list(outline.warnings) if outline else []
    return {
        "status": series_row.status.value,
        "outline_ready": series_row.status.value
        in {SeriesStatus.OUTLINE_READY.value, SeriesStatus.OUTLINE_APPROVED.value},
        "warnings": warnings,
    }
