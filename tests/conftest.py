import os

import pytest

os.environ["AGENT_PLAYGROUND_DATABASE_URL"] = "sqlite+aiosqlite:///./test_agent_playground.db"
os.environ["AGENT_PLAYGROUND_MODEL_PROVIDER"] = "fake"

from app.core.config import get_settings  # noqa: E402
from app.db.session import init_db  # noqa: E402

get_settings.cache_clear()


@pytest.fixture(autouse=True)
async def _init_test_db():
    get_settings.cache_clear()
    await init_db()
    yield
    get_settings.cache_clear()
