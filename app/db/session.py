from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from alembic.config import Config
from alembic.script import ScriptDirectory

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
        has_version_table = await conn.run_sync(lambda sync_conn: sync_conn.dialect.has_table(sync_conn, "alembic_version"))
        if not has_version_table:
            raise RuntimeError(_revision_error_message(None, head_revision))
        revision_result = await conn.execute(text("SELECT version_num FROM alembic_version"))
        current_revision = revision_result.scalar_one_or_none()
        if current_revision != head_revision:
            raise RuntimeError(_revision_error_message(current_revision, head_revision))


async def init_db() -> None:
    import app.db.models  # noqa: F401

    mode = get_settings().db_init_mode
    if mode == "alembic":
        await _ensure_alembic_revision()
        return

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if conn.dialect.name == "sqlite":
            result = await conn.execute(text("PRAGMA table_info(memories)"))
            columns = {row[1] for row in result}
            if "use_count" not in columns:
                await conn.execute(text("ALTER TABLE memories ADD COLUMN use_count INTEGER NOT NULL DEFAULT 0"))
            if "last_used_at" not in columns:
                await conn.execute(text("ALTER TABLE memories ADD COLUMN last_used_at DATETIME"))
            if "conflict_key" not in columns:
                await conn.execute(text("ALTER TABLE memories ADD COLUMN conflict_key VARCHAR(120)"))
            await conn.execute(
                text("CREATE INDEX IF NOT EXISTS ix_memories_conflict_key ON memories (conflict_key)")
            )


async def get_session() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        yield session
