"""SQLAlchemy Core 表定义（TASK-004）：与 DOMAIN_MODEL「序列化」一一对应。

单一事实来源：Alembic 迁移直接复用本 metadata；content/payload/snapshot/log
一律 JSON 列，schema 由领域 Pydantic 模型在边界校验。
媒体路径一律 POSIX 相对路径字符串。
"""

from __future__ import annotations

import sqlalchemy as sa

metadata = sa.MetaData()

settings = sa.Table(
    "settings",
    metadata,
    sa.Column("key", sa.String(length=64), primary_key=True),
    sa.Column("value", sa.JSON(), nullable=False),
)

projects = sa.Table(
    "projects",
    metadata,
    sa.Column("project_id", sa.String(64), primary_key=True),
    sa.Column("title", sa.String(255), nullable=False),
    sa.Column("idea", sa.Text(), nullable=False, server_default=""),
    sa.Column("params", sa.JSON(), nullable=False, server_default="{}"),
    sa.Column("status", sa.String(32), nullable=False),
    # 系列分集（TASK-047）：独立项目 series_id="" / episode_no=0
    sa.Column("series_id", sa.String(64), nullable=False, server_default=""),
    sa.Column("episode_no", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Column("updated_at", sa.DateTime(), nullable=False),
)

series = sa.Table(
    "series",
    metadata,
    sa.Column("series_id", sa.String(64), primary_key=True),
    sa.Column("title", sa.String(255), nullable=False),
    sa.Column("idea", sa.Text(), nullable=False, server_default=""),
    sa.Column("params", sa.JSON(), nullable=False, server_default="{}"),
    sa.Column("outline", sa.JSON(), nullable=True),
    sa.Column("status", sa.String(32), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Column("updated_at", sa.DateTime(), nullable=False),
)

script_versions = sa.Table(
    "script_versions",
    metadata,
    sa.Column("script_version_id", sa.String(64), primary_key=True),
    sa.Column("project_id", sa.String(64), nullable=False, index=True),
    sa.Column("version_no", sa.Integer(), nullable=False),
    sa.Column("content", sa.JSON(), nullable=False),
    sa.Column("source", sa.String(64), nullable=False, server_default="llm_call"),
    sa.Column("status", sa.String(16), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
)

assets = sa.Table(
    "assets",
    metadata,
    sa.Column("asset_id", sa.String(64), primary_key=True),
    sa.Column("project_id", sa.String(64), nullable=False, index=True),
    sa.Column("kind", sa.String(16), nullable=False),
    sa.Column("name", sa.String(255), nullable=False),
    sa.Column("description", sa.Text(), nullable=False, server_default=""),
    sa.Column("visual_anchor", sa.Text(), nullable=False, server_default=""),
    sa.Column("image_plan", sa.JSON(), nullable=False, server_default="[]"),
    sa.Column("updated_at", sa.DateTime(), nullable=False),
    sa.UniqueConstraint("project_id", "kind", "name", name="uq_assets_project_kind_name"),
)

asset_images = sa.Table(
    "asset_images",
    metadata,
    sa.Column("asset_image_id", sa.String(64), primary_key=True),
    sa.Column("asset_id", sa.String(64), nullable=False, index=True),
    sa.Column("version_no", sa.Integer(), nullable=False),
    sa.Column("view_label", sa.String(64), nullable=False),
    sa.Column("prompt", sa.Text(), nullable=False, server_default=""),
    sa.Column("provider", sa.String(64), nullable=False, server_default="minimax-image"),
    sa.Column("provider_ref", sa.String(255), nullable=False, server_default=""),
    sa.Column("file_path", sa.String(512), nullable=False, server_default=""),
    sa.Column("status", sa.String(16), nullable=False),
    sa.Column("approved", sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column("created_at", sa.DateTime(), nullable=False),
)

storyboard_versions = sa.Table(
    "storyboard_versions",
    metadata,
    sa.Column("storyboard_version_id", sa.String(64), primary_key=True),
    sa.Column("project_id", sa.String(64), nullable=False, index=True),
    sa.Column("version_no", sa.Integer(), nullable=False),
    sa.Column("content", sa.JSON(), nullable=False),
    sa.Column("status", sa.String(16), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
)

segment_frame_images = sa.Table(
    "segment_frame_images",
    metadata,
    sa.Column("frame_image_id", sa.String(64), primary_key=True),
    sa.Column("project_id", sa.String(64), nullable=False, index=True),
    sa.Column("segment_key", sa.String(16), nullable=False, index=True),
    sa.Column("version_no", sa.Integer(), nullable=False),
    sa.Column("view_label", sa.String(64), nullable=False, server_default="开场锚点"),
    sa.Column("prompt", sa.Text(), nullable=False, server_default=""),
    sa.Column("reference_paths", sa.JSON(), nullable=False, server_default="[]"),
    sa.Column("provider", sa.String(64), nullable=False, server_default="minimax-image"),
    sa.Column("provider_ref", sa.String(255), nullable=False, server_default=""),
    sa.Column("file_path", sa.String(512), nullable=False, server_default=""),
    sa.Column("status", sa.String(16), nullable=False),
    sa.Column("approved", sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column("created_at", sa.DateTime(), nullable=False),
)

jobs = sa.Table(
    "jobs",
    metadata,
    sa.Column("job_id", sa.String(64), primary_key=True),
    sa.Column("project_id", sa.String(64), nullable=False, index=True),
    sa.Column("type", sa.String(32), nullable=False),
    sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
    sa.Column("status", sa.String(16), nullable=False, index=True),
    sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("phase", sa.String(64), nullable=False, server_default=""),
    sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("depends_on", sa.JSON(), nullable=False, server_default="[]"),
    sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
    sa.Column("provider_task_id", sa.String(128), nullable=False, server_default=""),
    sa.Column("input_snapshot", sa.JSON(), nullable=False, server_default="{}"),
    sa.Column("error", sa.JSON(), nullable=True),
    sa.Column("log", sa.JSON(), nullable=False, server_default="[]"),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Column("started_at", sa.DateTime(), nullable=True),
    sa.Column("finished_at", sa.DateTime(), nullable=True),
    # 退避重试排程：仅存在于表行（领域模型不带），NULL = 立即可领取
    sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
)

clips = sa.Table(
    "clips",
    metadata,
    sa.Column("clip_id", sa.String(64), primary_key=True),
    sa.Column("project_id", sa.String(64), nullable=False, index=True),
    sa.Column("segment_key", sa.String(16), nullable=False),
    sa.Column("video_job_id", sa.String(64), nullable=False),
    sa.Column("storyboard_version_id", sa.String(64), nullable=False),
    sa.Column("file_path", sa.String(512), nullable=False),
    sa.Column("thumbnail_path", sa.String(512), nullable=False, server_default=""),
    sa.Column("tail_frame_path", sa.String(512), nullable=False, server_default=""),
    sa.Column("duration_sec", sa.Float(), nullable=False),
    sa.Column("mode", sa.String(8), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
)

films = sa.Table(
    "films",
    metadata,
    sa.Column("film_id", sa.String(64), primary_key=True),
    sa.Column("project_id", sa.String(64), nullable=False, index=True),
    sa.Column("version_no", sa.Integer(), nullable=False),
    sa.Column("file_path", sa.String(512), nullable=False, server_default=""),
    sa.Column("subtitle_path", sa.String(512), nullable=False, server_default=""),
    sa.Column("segment_keys", sa.JSON(), nullable=False, server_default="[]"),
    sa.Column("duration_sec", sa.Float(), nullable=False, server_default="0"),
    sa.Column("status", sa.String(16), nullable=False),
    sa.Column("error", sa.Text(), nullable=False, server_default=""),
    sa.Column("created_at", sa.DateTime(), nullable=False),
)

provider_calls = sa.Table(
    "provider_calls",
    metadata,
    sa.Column("call_id", sa.String(64), primary_key=True),
    sa.Column("job_id", sa.String(64), nullable=False, index=True),
    sa.Column("provider", sa.String(64), nullable=False),
    sa.Column("model", sa.String(64), nullable=False, server_default=""),
    sa.Column("kind", sa.String(16), nullable=False, server_default=""),
    sa.Column("usage", sa.JSON(), nullable=False, server_default="{}"),
    sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.true()),
    sa.Column("created_at", sa.DateTime(), nullable=False),
)
