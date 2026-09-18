"""FastAPI 应用入口（TASK-002/008）：DI 工厂 + 错误信封 + 静态媒体。"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from sqlalchemy.engine import Engine

from server.adapters.ffmpeg_svc import FFmpegError
from server.adapters.llm.base import LlmError
from server.adapters.runninghub import RunningHubError
from server.api.routes_series import router as series_router
from server.api.routes_system import router as system_router
from server.api.routes_workbench import router as workbench_router
from server.app.context import AppContext
from server.app.usecases import WorkbenchService
from server.domain.errors import DomainError
from server.infra.config import Settings, get_settings
from server.infra.db import get_engine

_STATUS_BY_CODE = {
    "NOT_FOUND": 404,
    "STATE_ILLEGAL": 409,
    "DEPENDENCY_BLOCKED": 409,
    "REF_MISSING": 422,
    "VALIDATION_ERROR": 422,
    "VALIDATION_FAILED": 422,
    "VALIDATION_SEGMENT": 422,
    "ARTIFACT_IMMUTABLE": 409,
    "JOB_NOT_RETRYABLE": 409,
}


def _envelope(code: str, message: str, details: dict | None = None, status: int = 400):
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "details": details or {}}},
    )


def create_app(
    settings: Settings | None = None, engine: Engine | None = None
) -> FastAPI:
    settings = settings or get_settings()
    engine = engine or get_engine()
    ctx = AppContext.build(settings, engine)

    app = FastAPI(title="short-dreamV4", version="0.2.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.ctx = ctx
    app.state.service = WorkbenchService(ctx)

    @app.exception_handler(DomainError)
    async def domain_error_handler(_request: Request, exc: DomainError):
        return _envelope(
            exc.code, exc.message, exc.details, _STATUS_BY_CODE.get(exc.code, 400)
        )

    @app.exception_handler(ValidationError)
    async def validation_error_handler(_request: Request, exc: ValidationError):
        return _envelope(
            "VALIDATION_ERROR",
            "请求校验失败",
            {"errors": exc.errors()[:8]},
            422,
        )

    @app.exception_handler(LlmError)
    @app.exception_handler(RunningHubError)
    async def provider_error_handler(_request: Request, exc: Exception):
        return _envelope(
            "PROVIDER_ERROR",
            getattr(exc, "message", str(exc)),
            {"provider_code": getattr(exc, "code", "")},
            502,
        )

    @app.exception_handler(FFmpegError)
    async def ffmpeg_error_handler(_request: Request, exc: FFmpegError):
        return _envelope("FFMPEG_MISSING", str(exc), {}, 503)

    app.include_router(system_router, prefix="/api")
    app.include_router(workbench_router, prefix="/api")
    app.include_router(series_router, prefix="/api")
    app.mount("/media", StaticFiles(directory=settings.media_dir), name="media")
    return app


app = create_app()
