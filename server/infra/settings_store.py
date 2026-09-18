"""DB 设置覆盖（API-009）：settings 表 key/value，覆盖 .env 默认。"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from server.infra.config import Settings
from server.infra.tables import settings as settings_table

OVERRIDE_KEY = "app"

MASK_KEYS = {"llm_api_key", "runninghub_api_key", "minimax_api_key"}


def load_overrides(engine) -> dict[str, Any]:
    stmt = sa.select(settings_table.c.value).where(settings_table.c.key == OVERRIDE_KEY)
    with engine.connect() as conn:
        row = conn.execute(stmt).first()
    return dict(row.value) if row else {}


def save_overrides(engine, overrides: dict[str, Any]) -> None:
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    stmt = sqlite_insert(settings_table).values({"key": OVERRIDE_KEY, "value": overrides})
    stmt = stmt.on_conflict_do_update(
        index_elements=[settings_table.c.key],
        set_={"value": overrides},
    )
    with engine.begin() as conn:
        conn.execute(stmt)


def apply_overrides(base: Settings, overrides: dict[str, Any]) -> Settings:
    clean = {k: v for k, v in overrides.items() if v is not None}
    return base.model_copy(update=clean)


def mask_settings(data: dict[str, Any]) -> dict[str, Any]:
    out = dict(data)
    for key in MASK_KEYS:
        value = out.get(key)
        if value:
            out[key] = f"***{value[-4:]}"
    return out
