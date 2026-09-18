"""系统与设置路由（TASK-002/016）：health、设置读写、连通性测试。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import text

from server.adapters.ffmpeg_svc import detect_ffmpeg
from server.infra.settings_store import (
    apply_overrides,
    load_overrides,
    mask_settings,
    save_overrides,
)

router = APIRouter()


class SettingsBody(BaseModel):
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
    runninghub_api_key: str | None = None
    image_model: str | None = None
    video_model: str | None = None
    video_concurrency: int | None = None
    # 图片生成并发上限（TASK-048，默认 2）：Worker 每轮领取实时读取，改完即生效
    image_concurrency: int | None = None
    ffmpeg_path: str | None = None


@router.get("/system/health")
def health(request: Request):
    settings = request.app.state.ctx.settings
    ffmpeg = detect_ffmpeg(settings.ffmpeg_path)
    db = "ok"
    try:
        with request.app.state.ctx.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 — 健康检查必须吞掉异常并如实降级
        db = "error"
    # 代码新鲜度（TASK-044）：进程启动后改的源码不会生效，付费动作会被 STALE_CODE
    # 拦下——把判定暴露出来，页面/排查一眼能看出"该重启了"，不必靠猜。
    from server.infra.buildinfo import freshness

    fresh = freshness()
    return {
        "status": "ok" if (ffmpeg["found"] and db == "ok") else "degraded",
        "db": db,
        "ffmpeg": {
            "found": ffmpeg["found"],
            "path": ffmpeg["path"],
            "version": ffmpeg["version"],
        },
        "code": {
            "version": fresh.version,
            "stale": fresh.stale,
            "process_started_ts": fresh.process_start_ts,
            "newest_source_ts": fresh.newest_source_ts,
            "newest_source_path": fresh.newest_source_path,
        },
        "providers_configured": {
            "minimax": bool(settings.minimax_api_key),
            "llm": bool(settings.llm_api_key),
            "runninghub": bool(settings.runninghub_api_key),
        },
    }


@router.get("/settings")
def get_settings(request: Request):
    settings = apply_overrides(
        request.app.state.ctx.settings,
        load_overrides(request.app.state.ctx.engine),
    )
    data = {
        "llm_base_url": settings.llm_base_url,
        "llm_model": settings.llm_model,
        "llm_api_key": settings.llm_api_key,
        "runninghub_api_key": settings.runninghub_api_key,
        "image_model": "qwen-image",
        "video_model": "MiniMax-H3",
        "video_concurrency": 1,
        # 图片生成并发上限（TASK-048）：Worker 每轮领取实时读取，改完即生效
        "image_concurrency": max(1, int(settings.image_concurrency)),
        "ffmpeg_path": settings.ffmpeg_path,
    }
    return mask_settings(data)


@router.put("/settings")
def put_settings(request: Request, body: SettingsBody):
    ctx = request.app.state.ctx
    overrides = load_overrides(ctx.engine)
    for field, value in body.model_dump().items():
        if value is not None:
            overrides[field] = value
    save_overrides(ctx.engine, overrides)
    return get_settings(request)


@router.post("/settings/test")
def test_settings(request: Request):
    ctx = request.app.state.ctx
    settings = apply_overrides(ctx.settings, load_overrides(ctx.engine))
    result: dict = {"llm": {"ok": False}, "image": {"ok": False}, "video": {"ok": False}}
    if settings.llm_api_key:
        try:
            from server.adapters.llm import build_llm_from_settings

            llm = build_llm_from_settings(settings)
            reply = llm.chat(system="ping", user="回复 pong", temperature=0.0)
            result["llm"] = {"ok": bool(reply.strip())}
        except Exception as exc:  # noqa: BLE001
            result["llm"] = {"ok": False, "error": str(exc)[:200]}
    else:
        result["llm"] = {"ok": False, "error": "LLM_API_KEY 未配置"}
    result["image"] = {"ok": bool(settings.runninghub_api_key)}
    result["video"] = {"ok": bool(settings.runninghub_api_key)}
    return result
