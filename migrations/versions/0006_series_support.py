"""series 表 + projects.series_id/episode_no：系列分集支持（TASK-047）

series 存全剧大纲（SeriesOutline JSON），每集是挂 series_id/episode_no 的
普通 project，复用既有流水线。存量项目两列取默认值，行为不变。

Revision ID: 0006_series_support
Revises: 0005_frame_reference_paths
Create Date: 2026-09-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from server.infra.tables import metadata

revision: str = "0006_series_support"
down_revision: str | None = "0005_frame_reference_paths"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 新表直接 create_all（幂等，只建缺失的 series）
    metadata.create_all(
        bind=op.get_bind(), tables=[metadata.tables["series"]]
    )
    # 存量 projects 补列：独立项目走默认值（""/0），语义不变
    op.add_column(
        "projects",
        sa.Column("series_id", sa.String(64), nullable=False, server_default=""),
    )
    op.add_column(
        "projects",
        sa.Column("episode_no", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("projects", "episode_no")
    op.drop_column("projects", "series_id")
    op.drop_table("series")
