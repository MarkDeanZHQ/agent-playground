from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine = create_async_engine(settings.database_url, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    import app.db.models  # noqa: F401

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
