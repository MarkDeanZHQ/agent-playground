import os

import pytest
from sqlalchemy import text

os.environ["AGENT_PLAYGROUND_DATABASE_URL"] = "sqlite+aiosqlite:///./test_agent_playground.db"

from app.core.config import get_settings  # noqa: E402
from app.db.session import engine, init_db  # noqa: E402


@pytest.mark.asyncio
async def test_init_db_create_all_mode_creates_tables(monkeypatch):
    monkeypatch.setenv("AGENT_PLAYGROUND_DB_INIT_MODE", "create_all")
    get_settings.cache_clear()

    await init_db()

    async with engine.connect() as conn:
        has_table = await conn.run_sync(lambda sync_conn: sync_conn.dialect.has_table(sync_conn, "agent_runs"))

    assert has_table is True
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_init_db_alembic_mode_requires_head_revision(monkeypatch):
    monkeypatch.setenv("AGENT_PLAYGROUND_DB_INIT_MODE", "alembic")
    get_settings.cache_clear()

    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))

    with pytest.raises(RuntimeError, match="uv run alembic upgrade head"):
        await init_db()

    get_settings.cache_clear()

