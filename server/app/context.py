"""应用层上下文（TASK-008）：仓储集合 + 设置，用例与 handler 共用。"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.engine import Engine

from server.infra.config import Settings
from server.infra.repositories import (
    AssetRepo,
    ClipRepo,
    FilmRepo,
    JobRepo,
    ProjectRepo,
    ProviderCallRepo,
    ScriptVersionRepo,
    SegmentFrameRepo,
    SeriesRepo,
    StoryboardVersionRepo,
)


@dataclass
class AppContext:
    settings: Settings
    engine: Engine
    projects: ProjectRepo
    jobs: JobRepo
    scripts: ScriptVersionRepo
    assets: AssetRepo
    storyboards: StoryboardVersionRepo
    frames: SegmentFrameRepo
    clips: ClipRepo
    films: FilmRepo
    calls: ProviderCallRepo
    series: SeriesRepo

    @classmethod
    def build(cls, settings: Settings, engine: Engine) -> AppContext:
        return cls(
            settings=settings,
            engine=engine,
            projects=ProjectRepo(engine),
            jobs=JobRepo(engine),
            scripts=ScriptVersionRepo(engine),
            assets=AssetRepo(engine),
            storyboards=StoryboardVersionRepo(engine),
            frames=SegmentFrameRepo(engine),
            clips=ClipRepo(engine),
            films=FilmRepo(engine),
            calls=ProviderCallRepo(engine),
            series=SeriesRepo(engine),
        )
