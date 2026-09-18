"""segment_frame_images.grid_cell: 多宫格分镜板的格号（方案A 逐格生成+程序拼宫格）

Revision ID: 0007_frame_grid_cell
Revises: 0006_series_support
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_frame_grid_cell"
down_revision: str | None = "0006_series_support"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """加 grid_cell 列：格号 1 起，NULL=非格子行（存量单张关键帧不受影响）。"""
    op.add_column("segment_frame_images", sa.Column("grid_cell", sa.Integer(), nullable=True))


def downgrade() -> None:
    """删 grid_cell 列（回滚后宫格行无法区分格号，需回退到旧单帧口径）。"""
    op.drop_column("segment_frame_images", "grid_cell")
