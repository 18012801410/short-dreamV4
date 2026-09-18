"""SQLite 引擎与会话（WAL）；领域层不得 import 本模块。"""

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from server.infra.config import get_settings

_engine: Engine | None = None
_session_factory: sessionmaker | None = None


def get_engine() -> Engine:
    global _engine, _session_factory
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(
            settings.db_url,
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(_engine, "connect")
        def _set_sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            # 多 Worker 进程并发跑图/跑视频（任务认领靠条件 UPDATE 抢占）时写事务
            # 可能短暂冲突：busy_timeout 让等待方重试而不是立刻报 database is locked
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def make_session():
    if _session_factory is None:
        get_engine()
    assert _session_factory is not None
    return _session_factory()
