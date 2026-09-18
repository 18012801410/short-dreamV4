"""segment_frame_images: 段关键帧图表（TASK-031 关键帧阶段）

Revision ID: 0004_segment_frame_images
Revises: 0003_films_subtitle_path
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_segment_frame_images"
down_revision: str | None = "0003_films_subtitle_path"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "segment_frame_images",
        sa.Column("frame_image_id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(64), nullable=False, index=True),
        sa.Column("segment_key", sa.String(16), nullable=False, index=True),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("view_label", sa.String(64), nullable=False, server_default="开场锚点"),
        sa.Column("prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("provider", sa.String(64), nullable=False, server_default="minimax-image"),
        sa.Column("provider_ref", sa.String(255), nullable=False, server_default=""),
        sa.Column("file_path", sa.String(512), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("approved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("segment_frame_images")
