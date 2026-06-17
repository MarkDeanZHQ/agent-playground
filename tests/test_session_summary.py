from datetime import timedelta

import pytest
from sqlalchemy import select

from app.db.models import Message, MessageRole, Session, SessionSummary, utc_now
from app.db.session import AsyncSessionLocal
from app.services.session_summary import SessionSummaryService


async def _session_with_messages(db, contents: list[tuple[MessageRole, str]]) -> Session:
    session = Session()
    db.add(session)
    await db.flush()
    base_time = utc_now()
    for role, content in contents:
        db.add(
            Message(
                session_id=session.id,
                role=role,
                content=content,
                created_at=base_time,
            )
        )
        base_time = base_time + timedelta(microseconds=1)
        await db.flush()
    return session


@pytest.mark.asyncio
async def test_summary_does_not_update_below_threshold(monkeypatch):
    monkeypatch.setenv("AGENT_PLAYGROUND_SUMMARY_TRIGGER_MESSAGE_COUNT", "4")
    from app.core.config import get_settings

    get_settings.cache_clear()
    async with AsyncSessionLocal() as db_session:
        session = await _session_with_messages(
            db_session,
            [
                (MessageRole.user, "第一轮：记住我的偏好是 FastAPI"),
                (MessageRole.assistant, "好的"),
            ],
        )

        result = await SessionSummaryService(db_session).maybe_update_summary_before_current_turn(session.id)

    assert result.checked is True
    assert result.updated is False
    assert result.used is False
    assert result.summary is None


@pytest.mark.asyncio
async def test_summary_updates_old_messages_and_keeps_current_turn_out(monkeypatch):
    monkeypatch.setenv("AGENT_PLAYGROUND_SUMMARY_TRIGGER_MESSAGE_COUNT", "4")
    monkeypatch.setenv("AGENT_PLAYGROUND_SUMMARY_RECENT_MESSAGE_KEEP", "2")
    monkeypatch.setenv("AGENT_PLAYGROUND_SUMMARY_MAX_CHARS", "300")
    from app.core.config import get_settings

    get_settings.cache_clear()
    async with AsyncSessionLocal() as db_session:
        session = await _session_with_messages(
            db_session,
            [
                (MessageRole.user, "第一轮：记住我的偏好是 FastAPI"),
                (MessageRole.assistant, "已记录 FastAPI 偏好"),
                (MessageRole.user, "第二轮：目标是学习 Agent 上下文压缩"),
                (MessageRole.assistant, "会围绕上下文压缩说明"),
                (MessageRole.user, "第三轮：最近消息应该保留"),
            ],
        )

        result = await SessionSummaryService(db_session).maybe_update_summary_before_current_turn(session.id)

        assert result.updated is True
        assert result.used is True
        assert result.covered_message_count == 3
        assert result.newly_summarized_messages == 3
        assert result.summary is not None
        assert "FastAPI" in result.summary
        assert "Agent 上下文压缩" in result.summary
        assert "第三轮：最近消息应该保留" not in result.summary
        assert len(result.summary) <= 300
        stored = (
            await db_session.execute(select(SessionSummary).where(SessionSummary.session_id == session.id))
        ).scalar_one()
        assert stored.covered_message_count == 3


@pytest.mark.asyncio
async def test_summary_disabled_returns_existing_summary_as_unused(monkeypatch):
    monkeypatch.setenv("AGENT_PLAYGROUND_SUMMARY_ENABLED", "false")
    from app.core.config import get_settings

    get_settings.cache_clear()
    async with AsyncSessionLocal() as db_session:
        session = await _session_with_messages(
            db_session,
            [(MessageRole.user, "第一轮：记住我的偏好是 FastAPI")],
        )
        db_session.add(
            SessionSummary(
                session_id=session.id,
                content="历史摘要：FastAPI",
                covered_message_count=1,
            )
        )
        await db_session.flush()

        result = await SessionSummaryService(db_session).maybe_update_summary_before_current_turn(session.id)

    assert result.checked is True
    assert result.updated is False
    assert result.used is False
    assert result.summary is None
