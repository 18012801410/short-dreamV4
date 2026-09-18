"""Core 风格仓储（TASK-004）：领域模型 ↔ 行映射，JSON 列原样进出。

时间戳规范：领域层用 aware UTC datetime；行映射在写入时序列化为
JSON（mode="json"）后把 DateTime 列还原成 naive UTC 落库，读回时统一补
tzinfo=UTC。SQLite 时区无关，比较一律在 UTC 语义下进行。
仓储只做持久化映射；状态机与不变量全部在领域层（server/domain）。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa

from server.domain.entities import (
    Asset,
    AssetImage,
    Clip,
    Film,
    Job,
    Project,
    ProviderCall,
    ScriptVersion,
    SegmentFrameImage,
    Series,
    StoryboardVersion,
)
from server.domain.enums import JobStatus, WorkStatus
from server.domain.errors import DependencyBlockedError, StateIllegalError
from server.domain.job import mark_running
from server.infra.tables import (
    asset_images,
    assets,
    clips,
    films,
    jobs,
    projects,
    provider_calls,
    script_versions,
    segment_frame_images,
    series,
    storyboard_versions,
)


def _row_to_dict(row: sa.Row) -> dict[str, Any]:
    return dict(row._mapping)


def _dump_for_db(values: dict[str, Any], dt_fields: tuple[str, ...]) -> dict[str, Any]:
    """model_dump(mode="json") 的 ISO 字符串 → naive UTC（供 DateTime 列绑定）。"""
    for field in dt_fields:
        raw = values.get(field)
        if isinstance(raw, str):
            parsed = datetime.fromisoformat(raw)
            values[field] = (
                parsed.astimezone(UTC).replace(tzinfo=None) if parsed.tzinfo else parsed
            )
    return values


def _load_datetimes(values: dict[str, Any], dt_fields: tuple[str, ...]) -> dict[str, Any]:
    """DB 读回的 naive datetime → aware UTC（与领域层 utcnow() 一致）。"""
    for field in dt_fields:
        raw = values.get(field)
        if isinstance(raw, datetime) and raw.tzinfo is None:
            values[field] = raw.replace(tzinfo=UTC)
    return values


class ProjectRepo:
    DT_FIELDS = ("created_at", "updated_at")

    def __init__(self, engine: sa.Engine) -> None:
        self._engine = engine

    def create(self, project: Project) -> None:
        values = _dump_for_db(project.model_dump(mode="json"), self.DT_FIELDS)
        with self._engine.begin() as conn:
            conn.execute(sa.insert(projects).values(**values))

    def get(self, project_id: str) -> Project | None:
        stmt = sa.select(projects).where(projects.c.project_id == project_id)
        with self._engine.connect() as conn:
            row = conn.execute(stmt).first()
        return self._to_domain(row) if row else None

    def save(self, project: Project) -> None:
        values = _dump_for_db(project.model_dump(mode="json"), self.DT_FIELDS)
        with self._engine.begin() as conn:
            conn.execute(
                sa.update(projects)
                .where(projects.c.project_id == project.project_id)
                .values(**values)
            )

    def list_all(self) -> list[Project]:
        stmt = sa.select(projects).order_by(projects.c.created_at.desc())
        with self._engine.connect() as conn:
            return [self._to_domain(r) for r in conn.execute(stmt)]

    def list_by_series(self, series_id: str) -> list[Project]:
        stmt = (
            sa.select(projects)
            .where(projects.c.series_id == series_id)
            .order_by(projects.c.episode_no.asc())
        )
        with self._engine.connect() as conn:
            return [self._to_domain(r) for r in conn.execute(stmt)]

    def delete_project_cascade(self, project_id: str) -> dict[str, int]:
        """级联删除项目全部数据行（单事务，供「删除项目」用例）。

        删除顺序按依赖：provider_calls（经 jobs 关联）→ jobs → clips → films →
        segment_frame_images → asset_images（经 assets 关联）→ assets →
        script_versions → storyboard_versions → projects。
        媒体文件不在此处理（归档回收由用例层负责）。

        返回各表删除行数（键为表名），便于用例层向用户报账。
        """
        by_pid = lambda t: t.c.project_id == project_id  # noqa: E731
        with self._engine.begin() as conn:
            counts: dict[str, int] = {}
            counts["provider_calls"] = conn.execute(
                sa.delete(provider_calls).where(
                    provider_calls.c.job_id.in_(
                        sa.select(jobs.c.job_id).where(by_pid(jobs))
                    )
                )
            ).rowcount
            counts["jobs"] = conn.execute(sa.delete(jobs).where(by_pid(jobs))).rowcount
            counts["asset_images"] = conn.execute(
                sa.delete(asset_images).where(
                    asset_images.c.asset_id.in_(
                        sa.select(assets.c.asset_id).where(by_pid(assets))
                    )
                )
            ).rowcount
            for name, table in (
                ("clips", clips),
                ("films", films),
                ("segment_frame_images", segment_frame_images),
                ("assets", assets),
                ("script_versions", script_versions),
                ("storyboard_versions", storyboard_versions),
                ("projects", projects),
            ):
                counts[name] = conn.execute(sa.delete(table).where(by_pid(table))).rowcount
            return counts

    def _to_domain(self, row: sa.Row) -> Project:
        values = _load_datetimes(_row_to_dict(row), self.DT_FIELDS)
        return Project.model_validate(values)


class SeriesRepo:
    """series 表仓储（TASK-047）：大纲 JSON 原样进出，schema 由 Pydantic 校验。"""

    DT_FIELDS = ("created_at", "updated_at")

    def __init__(self, engine: sa.Engine) -> None:
        self._engine = engine

    def add(self, series_row: Series) -> None:
        values = _dump_for_db(series_row.model_dump(mode="json"), self.DT_FIELDS)
        with self._engine.begin() as conn:
            conn.execute(sa.insert(series).values(**values))

    def get(self, series_id: str) -> Series | None:
        stmt = sa.select(series).where(series.c.series_id == series_id)
        with self._engine.connect() as conn:
            row = conn.execute(stmt).first()
        return self._to_domain(row) if row else None

    def save(self, series_row: Series) -> None:
        values = _dump_for_db(series_row.model_dump(mode="json"), self.DT_FIELDS)
        with self._engine.begin() as conn:
            conn.execute(
                sa.update(series).where(series.c.series_id == series_row.series_id).values(**values)
            )

    def list_all(self) -> list[Series]:
        stmt = sa.select(series).order_by(series.c.created_at.desc())
        with self._engine.connect() as conn:
            return [self._to_domain(r) for r in conn.execute(stmt)]

    def _to_domain(self, row: sa.Row) -> Series:
        values = _load_datetimes(_row_to_dict(row), self.DT_FIELDS)
        return Series.model_validate(values)


class JobRepo:
    """jobs 表即队列。next_attempt_at 是表级排程列，不进领域模型。"""

    DT_FIELDS = ("created_at", "started_at", "finished_at")

    def __init__(self, engine: sa.Engine) -> None:
        self._engine = engine

    def _to_row(self, job: Job, *, next_attempt_at: datetime | None = None) -> dict[str, Any]:
        values = _dump_for_db(job.model_dump(mode="json"), self.DT_FIELDS)
        values["next_attempt_at"] = (
            next_attempt_at.astimezone(UTC).replace(tzinfo=None)
            if next_attempt_at is not None and next_attempt_at.tzinfo
            else next_attempt_at
        )
        return values

    def _to_domain(self, row: sa.Row) -> Job:
        values = _row_to_dict(row)
        values.pop("next_attempt_at", None)
        return Job.model_validate(_load_datetimes(values, self.DT_FIELDS))

    @staticmethod
    def _normalize_scheduled(raw: datetime | None) -> datetime | None:
        if raw is not None and raw.tzinfo is None:
            return raw.replace(tzinfo=UTC)
        return raw

    def insert(self, job: Job, *, next_attempt_at: datetime | None = None) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                sa.insert(jobs).values(**self._to_row(job, next_attempt_at=next_attempt_at))
            )

    def get(self, job_id: str) -> Job | None:
        stmt = sa.select(jobs).where(jobs.c.job_id == job_id)
        with self._engine.connect() as conn:
            row = conn.execute(stmt).first()
        return self._to_domain(row) if row else None

    def next_attempt_at(self, job_id: str) -> datetime | None:
        stmt = sa.select(jobs.c.next_attempt_at).where(jobs.c.job_id == job_id)
        with self._engine.connect() as conn:
            row = conn.execute(stmt).first()
        return self._normalize_scheduled(row.next_attempt_at) if row else None

    def save(self, job: Job, *, next_attempt_at: datetime | None = None) -> None:
        """整行覆盖保存（以领域模型为准；排程列显式传入，未传则清除）。"""
        stmt = (
            sa.update(jobs)
            .where(jobs.c.job_id == job.job_id)
            .values(**self._to_row(job, next_attempt_at=next_attempt_at))
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def statuses_for(self, job_ids: list[str]) -> dict[str, JobStatus]:
        if not job_ids:
            return {}
        stmt = sa.select(jobs.c.job_id, jobs.c.status).where(jobs.c.job_id.in_(job_ids))
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return {r.job_id: JobStatus(r.status) for r in rows}

    def list_by_status(self, status: JobStatus) -> list[Job]:
        stmt = sa.select(jobs).where(jobs.c.status == status.value).order_by(
            jobs.c.priority.desc(), jobs.c.created_at.asc()
        )
        with self._engine.connect() as conn:
            return [self._to_domain(r) for r in conn.execute(stmt)]

    def list_all(self) -> list[Job]:
        stmt = sa.select(jobs).order_by(jobs.c.created_at.asc())
        with self._engine.connect() as conn:
            return [self._to_domain(r) for r in conn.execute(stmt)]

    def claim_next_runnable(
        self,
        now: datetime,
        *,
        type_gate: Callable[[Job], bool] | None = None,
    ) -> tuple[Job, dict[str, JobStatus]] | None:
        """领取一个可运行的 pending Job：依赖全部 succeeded 且退避时间已到。

        按 priority 降序、created_at 升序；依赖未满足的 Job 留在队列中。
        type_gate（并发闸门，TASK-048）：返回 False 的 Job 本轮跳过（留在
        pending，不消耗重试预算），用于图片类任务的并发上限。
        认领是原子的（条件 UPDATE 抢占）：多 Worker 并发时只有一个进程能把
        pending 置为 running，其余进程 rowcount=0 跳过，避免同一任务双跑。
        返回 (已置为 running 的 Job, 其依赖状态快照)；无可领取任务返回 None。
        """
        for job in self.list_by_status(JobStatus.PENDING):
            if type_gate is not None and not type_gate(job):
                continue
            scheduled = self.next_attempt_at(job.job_id)
            if scheduled is not None and scheduled > now:
                continue
            dep_statuses = self.statuses_for(job.depends_on)
            try:
                running = mark_running(job, dep_statuses)
            except (DependencyBlockedError, StateIllegalError):
                continue  # 依赖未满足或状态已被并发改动，跳过看下一个
            with self._engine.begin() as conn:
                claimed = conn.execute(
                    sa.update(jobs)
                    .where(jobs.c.job_id == job.job_id)
                    .where(jobs.c.status == JobStatus.PENDING.value)
                    .values(status=JobStatus.RUNNING.value, next_attempt_at=None)
                )
            if claimed.rowcount != 1:
                continue  # 已被其他 Worker 抢走
            self.save(running)  # 落其余 running 字段（已持有认领权）
            return running, dep_statuses
        return None

    def running_video_jobs(self) -> list[Job]:
        """已提交到平台、等待轮询的 video_gen（两阶段提交模型的可推进任务）。

        必须过滤 provider_task_id 非空（TASK-034）：并发 Worker 池下，若把
        "正在别的进程里阻塞执行"的任务也返回，另一个进程会拿它再跑一遍
        handler——那就是重复生成一整段视频（真金白银双倍消耗）。
        """
        stmt = (
            sa.select(jobs)
            .where(jobs.c.status == JobStatus.RUNNING.value)
            .where(jobs.c.type == "video_gen")
            .where(jobs.c.provider_task_id != "")
        )
        with self._engine.connect() as conn:
            return [self._to_domain(r) for r in conn.execute(stmt)]

    def count_running_of_types(self, type_values: list[str]) -> int:
        """指定类型的 running 任务数（图片并发闸门的现场计数，TASK-048）。"""
        if not type_values:
            return 0
        stmt = (
            sa.select(sa.func.count())
            .select_from(jobs)
            .where(jobs.c.status == JobStatus.RUNNING.value, jobs.c.type.in_(type_values))
        )
        with self._engine.connect() as conn:
            return int(conn.execute(stmt).scalar() or 0)


class ClipRepo:
    DT_FIELDS = ("created_at",)

    def __init__(self, engine: sa.Engine) -> None:
        self._engine = engine

    def add(self, clip: Clip) -> None:
        values = _dump_for_db(clip.model_dump(mode="json"), self.DT_FIELDS)
        with self._engine.begin() as conn:
            conn.execute(sa.insert(clips).values(**values))

    def list_by_project(self, project_id: str) -> list[Clip]:
        stmt = (
            sa.select(clips)
            .where(clips.c.project_id == project_id)
            .order_by(clips.c.created_at.asc())
        )
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return [
            Clip.model_validate(_load_datetimes(_row_to_dict(r), self.DT_FIELDS)) for r in rows
        ]


class ProviderCallRepo:
    DT_FIELDS = ("created_at",)

    def __init__(self, engine: sa.Engine) -> None:
        self._engine = engine

    def add(self, call: ProviderCall) -> None:
        values = _dump_for_db(call.model_dump(mode="json"), self.DT_FIELDS)
        with self._engine.begin() as conn:
            conn.execute(sa.insert(provider_calls).values(**values))

    def list_by_job(self, job_id: str) -> list[ProviderCall]:
        stmt = sa.select(provider_calls).where(provider_calls.c.job_id == job_id)
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return [
            ProviderCall.model_validate(_load_datetimes(_row_to_dict(r), self.DT_FIELDS))
            for r in rows
        ]


class ScriptVersionRepo:
    DT_FIELDS = ("created_at",)

    def __init__(self, engine: sa.Engine) -> None:
        self._engine = engine

    def add(self, version: ScriptVersion) -> None:
        values = _dump_for_db(version.model_dump(mode="json"), self.DT_FIELDS)
        with self._engine.begin() as conn:
            conn.execute(sa.insert(script_versions).values(**values))

    def list_by_project(self, project_id: str) -> list[ScriptVersion]:
        stmt = (
            sa.select(script_versions)
            .where(script_versions.c.project_id == project_id)
            .order_by(script_versions.c.version_no.desc())
        )
        with self._engine.connect() as conn:
            return [self._to_domain(r) for r in conn.execute(stmt)]

    def active(self, project_id: str) -> ScriptVersion | None:
        for v in self.list_by_project(project_id):
            if v.status is WorkStatus.ACTIVE:
                return v
        return None

    def latest_draft(self, project_id: str) -> ScriptVersion | None:
        for v in self.list_by_project(project_id):
            if v.status is WorkStatus.DRAFT:
                return v
        return None

    def save(self, version: ScriptVersion) -> None:
        values = _dump_for_db(version.model_dump(mode="json"), self.DT_FIELDS)
        with self._engine.begin() as conn:
            conn.execute(
                sa.update(script_versions)
                .where(script_versions.c.script_version_id == version.script_version_id)
                .values(**values)
            )

    def _to_domain(self, row: sa.Row) -> ScriptVersion:
        values = _load_datetimes(_row_to_dict(row), self.DT_FIELDS)
        return ScriptVersion.model_validate(values)


class AssetRepo:
    """Asset + AssetImage 一体仓储（同表族）。"""

    def __init__(self, engine: sa.Engine) -> None:
        self._engine = engine

    def add_asset(self, asset: Asset) -> None:
        values = _dump_for_db(asset.model_dump(mode="json"), ("updated_at",))
        with self._engine.begin() as conn:
            conn.execute(sa.insert(assets).values(**values))

    def get_asset(self, asset_id: str) -> Asset | None:
        stmt = sa.select(assets).where(assets.c.asset_id == asset_id)
        with self._engine.connect() as conn:
            row = conn.execute(stmt).first()
        return self._to_asset(row) if row else None

    def list_assets(self, project_id: str) -> list[Asset]:
        stmt = sa.select(assets).where(assets.c.project_id == project_id).order_by(
            assets.c.kind.asc(), assets.c.name.asc()
        )
        with self._engine.connect() as conn:
            return [self._to_asset(r) for r in conn.execute(stmt)]

    def _to_asset(self, row: sa.Row) -> Asset:
        values = _load_datetimes(_row_to_dict(row), ("updated_at",))
        return Asset.model_validate(values)

    def save_asset(self, asset: Asset) -> None:
        values = _dump_for_db(asset.model_dump(mode="json"), ("updated_at",))
        with self._engine.begin() as conn:
            conn.execute(
                sa.update(assets).where(assets.c.asset_id == asset.asset_id).values(**values)
            )

    def delete_image(self, asset_image_id: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                sa.delete(asset_images).where(asset_images.c.asset_image_id == asset_image_id)
            )

    def delete_assets_for_project(self, project_id: str) -> None:
        """整体替换语义（重新抽取资产前调用）：先删图再删资产。磁盘文件保留。"""
        with self._engine.begin() as conn:
            conn.execute(
                asset_images.delete().where(
                    asset_images.c.asset_id.in_(
                        sa.select(assets.c.asset_id).where(assets.c.project_id == project_id)
                    )
                )
            )
            conn.execute(assets.delete().where(assets.c.project_id == project_id))

    def add_image(self, image: AssetImage) -> None:
        values = _dump_for_db(image.model_dump(mode="json"), ("created_at",))
        with self._engine.begin() as conn:
            conn.execute(sa.insert(asset_images).values(**values))

    def get_image(self, asset_image_id: str) -> AssetImage | None:
        stmt = sa.select(asset_images).where(
            asset_images.c.asset_image_id == asset_image_id
        )
        with self._engine.connect() as conn:
            row = conn.execute(stmt).first()
        return self._to_image(row) if row else None

    def list_images(
        self, project_id: str | None = None, asset_id: str | None = None
    ) -> list[AssetImage]:
        stmt = sa.select(asset_images)
        if asset_id is not None:
            stmt = stmt.where(asset_images.c.asset_id == asset_id)
        if project_id is not None:
            stmt = stmt.where(
                asset_images.c.asset_id.in_(
                    sa.select(assets.c.asset_id).where(assets.c.project_id == project_id)
                )
            )
        stmt = stmt.order_by(asset_images.c.created_at.asc())
        with self._engine.connect() as conn:
            return [self._to_image(r) for r in conn.execute(stmt)]

    def save_image(self, image: AssetImage) -> None:
        values = _dump_for_db(image.model_dump(mode="json"), ("created_at",))
        with self._engine.begin() as conn:
            conn.execute(
                sa.update(asset_images)
                .where(asset_images.c.asset_image_id == image.asset_image_id)
                .values(**values)
            )

    def _to_image(self, row: sa.Row) -> AssetImage:
        values = _load_datetimes(_row_to_dict(row), ("created_at",))
        return AssetImage.model_validate(values)


class SegmentFrameRepo:
    """段关键帧图仓储（TASK-031，镜像 AssetRepo 的图片方法族）。"""

    def __init__(self, engine: sa.Engine) -> None:
        self._engine = engine

    def add(self, image: SegmentFrameImage) -> None:
        values = _dump_for_db(image.model_dump(mode="json"), ("created_at",))
        with self._engine.begin() as conn:
            conn.execute(sa.insert(segment_frame_images).values(**values))

    def get(self, frame_image_id: str) -> SegmentFrameImage | None:
        stmt = sa.select(segment_frame_images).where(
            segment_frame_images.c.frame_image_id == frame_image_id
        )
        with self._engine.connect() as conn:
            row = conn.execute(stmt).first()
        return self._to_domain(row) if row else None

    def list_by_project(self, project_id: str) -> list[SegmentFrameImage]:
        stmt = (
            sa.select(segment_frame_images)
            .where(segment_frame_images.c.project_id == project_id)
            .order_by(segment_frame_images.c.created_at.asc())
        )
        with self._engine.connect() as conn:
            return [self._to_domain(r) for r in conn.execute(stmt)]

    def list_by_segment(self, project_id: str, segment_key: str) -> list[SegmentFrameImage]:
        stmt = (
            sa.select(segment_frame_images)
            .where(
                segment_frame_images.c.project_id == project_id,
                segment_frame_images.c.segment_key == segment_key,
            )
            .order_by(segment_frame_images.c.version_no.asc())
        )
        with self._engine.connect() as conn:
            return [self._to_domain(r) for r in conn.execute(stmt)]

    def save(self, image: SegmentFrameImage) -> None:
        values = _dump_for_db(image.model_dump(mode="json"), ("created_at",))
        with self._engine.begin() as conn:
            conn.execute(
                sa.update(segment_frame_images)
                .where(segment_frame_images.c.frame_image_id == image.frame_image_id)
                .values(**values)
            )

    def delete(self, frame_image_id: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                sa.delete(segment_frame_images).where(
                    segment_frame_images.c.frame_image_id == frame_image_id
                )
            )

    def _to_domain(self, row: sa.Row) -> SegmentFrameImage:
        values = _load_datetimes(_row_to_dict(row), ("created_at",))
        return SegmentFrameImage.model_validate(values)


class StoryboardVersionRepo:
    DT_FIELDS = ("created_at",)

    def __init__(self, engine: sa.Engine) -> None:
        self._engine = engine

    def add(self, version: StoryboardVersion) -> None:
        values = _dump_for_db(version.model_dump(mode="json"), self.DT_FIELDS)
        with self._engine.begin() as conn:
            conn.execute(sa.insert(storyboard_versions).values(**values))

    def list_by_project(self, project_id: str) -> list[StoryboardVersion]:
        stmt = (
            sa.select(storyboard_versions)
            .where(storyboard_versions.c.project_id == project_id)
            .order_by(storyboard_versions.c.version_no.desc())
        )
        with self._engine.connect() as conn:
            return [self._to_domain(r) for r in conn.execute(stmt)]

    def get_by_id(self, version_id: str) -> StoryboardVersion | None:
        stmt = sa.select(storyboard_versions).where(
            storyboard_versions.c.storyboard_version_id == version_id
        )
        with self._engine.connect() as conn:
            row = conn.execute(stmt).first()
        return self._to_domain(row) if row else None

    def active(self, project_id: str) -> StoryboardVersion | None:
        for v in self.list_by_project(project_id):
            if v.status is WorkStatus.ACTIVE:
                return v
        return None

    def latest_draft(self, project_id: str) -> StoryboardVersion | None:
        for v in self.list_by_project(project_id):
            if v.status is WorkStatus.DRAFT:
                return v
        return None

    def save(self, version: StoryboardVersion) -> None:
        values = _dump_for_db(version.model_dump(mode="json"), self.DT_FIELDS)
        with self._engine.begin() as conn:
            conn.execute(
                sa.update(storyboard_versions)
                .where(
                    storyboard_versions.c.storyboard_version_id == version.storyboard_version_id
                )
                .values(**values)
            )

    def _to_domain(self, row: sa.Row) -> StoryboardVersion:
        values = _load_datetimes(_row_to_dict(row), self.DT_FIELDS)
        return StoryboardVersion.model_validate(values)


class FilmRepo:
    DT_FIELDS = ("created_at",)
    def __init__(self, engine: sa.Engine) -> None:
        self._engine = engine

    def add(self, film: Film) -> None:
        values = _dump_for_db(film.model_dump(mode="json"), self.DT_FIELDS)
        with self._engine.begin() as conn:
            conn.execute(sa.insert(films).values(**values))

    def list_by_project(self, project_id: str) -> list[Film]:
        stmt = (
            sa.select(films)
            .where(films.c.project_id == project_id)
            .order_by(films.c.version_no.desc())
        )
        with self._engine.connect() as conn:
            return [self._to_domain(r) for r in conn.execute(stmt)]

    def save(self, film: Film) -> None:
        values = _dump_for_db(film.model_dump(mode="json"), self.DT_FIELDS)
        with self._engine.begin() as conn:
            conn.execute(sa.update(films).where(films.c.film_id == film.film_id).values(**values))

    def _to_domain(self, row: sa.Row) -> Film:
        values = _load_datetimes(_row_to_dict(row), self.DT_FIELDS)
        return Film.model_validate(values)
