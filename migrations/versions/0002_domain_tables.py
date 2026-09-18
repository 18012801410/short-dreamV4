"""domain tables: projects/script_versions/assets/asset_images/storyboard_versions/
jobs/clips/films/provider_calls

单一事实来源是 server.infra.tables.metadata，本迁移直接 create_all（幂等，
跳过 0001 已建的 settings）。降级 drop_all 会连同 settings 一起移除，
仅用于开发期回滚。

Revision ID: 0002_domain_tables
Revises: 0001_baseline
Create Date: 2026-09-12
"""

from collections.abc import Sequence

from alembic import op

from server.infra.tables import metadata

revision: str = "0002_domain_tables"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    metadata.drop_all(bind=op.get_bind())
