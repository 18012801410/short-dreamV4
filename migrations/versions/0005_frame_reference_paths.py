"""segment_frame_images.reference_paths: 关键帧记录实际使用的参考图（TASK-035）

页面要能摊开"这张关键帧参考了谁"，并支持按帧追溯/手工指定参考图重抽。

Revision ID: 0005_frame_reference_paths
Revises: 0004_segment_frame_images
Create Date: 2026-09-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_frame_reference_paths"
down_revision: str | None = "0004_segment_frame_images"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "segment_frame_images",
        sa.Column(
            "reference_paths", sa.JSON(), nullable=False, server_default="[]"
        ),
    )


def downgrade() -> None:
    op.drop_column("segment_frame_images", "reference_paths")
