"""films: 增加 subtitle_path（烧录字幕版成片路径，空 = 未生成）

Revision ID: 0003_films_subtitle_path
Revises: 0002_domain_tables
Create Date: 2026-09-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_films_subtitle_path"
down_revision: str | None = "0002_domain_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "films",
        sa.Column("subtitle_path", sa.String(512), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("films", "subtitle_path")
