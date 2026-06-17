from collections.abc import AsyncIterator
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine = create_async_engine(settings.database_url, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


def _alembic_head_revision() -> str:
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    if not heads:
        raise RuntimeError("Alembic head revision is missing.")
    if len(heads) > 1:
        raise RuntimeError(f"Multiple Alembic heads are not supported: {', '.join(heads)}")
    return heads[0]


def _revision_error_message(current_revision: str | None, head_revision: str) -> str:
    current = current_revision or "<uninitialized>"
    return (
        "Database revision check failed.\n"
        f"Current revision: {current}\n"
        f"Code head revision: {head_revision}\n"
        "Run: uv run alembic upgrade head\n"
        "Or switch back to learning mode with AGENT_PLAYGROUND_DB_INIT_MODE=create_all"
    )


async def _ensure_alembic_revision() -> None:
    head_revision = _alembic_head_revision()
    async with engine.connect() as conn:
        has_version_table = await conn.run_sync(
            lambda sync_conn: sync_conn.dialect.has_table(sync_conn, "alembic_version")
        )
        if not has_version_table:
            raise RuntimeError(_revision_error_message(None, head_revision))
        revision_result = await conn.execute(text("SELECT version_num FROM alembic_version"))
        current_revision = revision_result.scalar_one_or_none()
        if current_revision != head_revision:
            raise RuntimeError(_revision_error_message(current_revision, head_revision))


async def _sqlite_add_column_if_missing(conn, table: str, columns: set[str], column: str, definition: str) -> None:
    if column in columns:
        return
    await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))


async def init_db() -> None:
    import app.db.models  # noqa: F401

    mode = get_settings().db_init_mode
    if mode == "alembic":
        await _ensure_alembic_revision()
        return

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if conn.dialect.name == "sqlite":
            summary_result = await conn.execute(text("PRAGMA table_info(session_summaries)"))
            summary_columns = {row[1] for row in summary_result}
            await _sqlite_add_column_if_missing(
                conn,
                "session_summaries",
                summary_columns,
                "summary_json",
                "TEXT NOT NULL DEFAULT '{}'",
            )

            result = await conn.execute(text("PRAGMA table_info(memories)"))
            columns = {row[1] for row in result}
            memory_columns = {
                "scope": "VARCHAR(20) NOT NULL DEFAULT 'project'",
                "category": "VARCHAR(50) NOT NULL DEFAULT 'preference'",
                "source_kind": "VARCHAR(50) NOT NULL DEFAULT 'manual'",
                "confidence": "INTEGER NOT NULL DEFAULT 3",
                "session_id": "VARCHAR",
                "owner_id": "VARCHAR(120)",
                "sensitivity": "VARCHAR(20) NOT NULL DEFAULT 'public'",
                "supersedes_memory_id": "VARCHAR",
                "expires_at": "DATETIME",
                "use_count": "INTEGER NOT NULL DEFAULT 0",
                "last_used_at": "DATETIME",
                "conflict_key": "VARCHAR(120)",
            }
            for column, definition in memory_columns.items():
                await _sqlite_add_column_if_missing(conn, "memories", columns, column, definition)
            await conn.execute(
                text("CREATE INDEX IF NOT EXISTS ix_memories_conflict_key ON memories (conflict_key)")
            )
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_memories_session_id ON memories (session_id)"))
            await conn.execute(
                text("CREATE INDEX IF NOT EXISTS ix_memories_supersedes_memory_id ON memories (supersedes_memory_id)")
            )


async def get_session() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        yield session
