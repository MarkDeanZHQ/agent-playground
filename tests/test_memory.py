import pytest
from sqlalchemy import select

from app.db.models import Memory, MemoryStatus, MemoryVersion, Message, MessageRole, Session
from app.db.session import AsyncSessionLocal
from app.memory.service import (
    InvalidMemoryOperationError,
    InvalidMemoryPayloadError,
    MemoryPolicy,
    MemoryService,
    conflict_key_for_content,
    extract_query_terms,
    has_replacement_marker,
)


def test_memory_policy_stores_stable_preference():
    policy = MemoryPolicy()

    assert policy.should_store("请记住：我偏好使用 FastAPI 做后端示例") is True
    should_store, reason = policy.decision("请记住：我偏好使用 FastAPI 做后端示例")
    assert should_store is True
    assert "稳定偏好" in reason


def test_memory_policy_rejects_sensitive_or_temporary_content():
    policy = MemoryPolicy()

    assert policy.should_store("请记住我的 API key 是 abc") is False
    assert policy.should_store("今天临时记住这个命令") is False
    assert policy.should_store("这是一次普通问题") is False
    assert policy.decision("请记住我的 API key 是 abc") == (False, "文本包含敏感信息标记")


@pytest.mark.asyncio
async def test_memory_service_records_created_version():
    async with AsyncSessionLocal() as db:
        session = Session()
        db.add(session)
        await db.flush()
        message = Message(session_id=session.id, role=MessageRole.user, content="请记住：我偏好 FastAPI 示例")
        db.add(message)
        await db.flush()

        memories = await MemoryService(db).extract_and_store(message.id, message.content)

        result = await db.execute(select(MemoryVersion).where(MemoryVersion.memory_id == memories[0].id))
        versions = list(result.scalars())

    assert [version.operation for version in versions] == ["created"]
    assert versions[0].content == memories[0].content


@pytest.mark.asyncio
async def test_memory_service_records_superseded_version():
    async with AsyncSessionLocal() as db:
        session = Session()
        db.add(session)
        await db.flush()
        first = Message(session_id=session.id, role=MessageRole.user, content="请记住：我偏好 FastAPI 示例")
        second = Message(session_id=session.id, role=MessageRole.user, content="请记住：我偏好以后用 FastAPI 示例 v2")
        db.add_all([first, second])
        await db.flush()
        service = MemoryService(db)
        old_memory = (await service.extract_and_store(first.id, first.content))[0]

        await service.extract_and_store(second.id, second.content)

        result = await db.execute(select(MemoryVersion).where(MemoryVersion.memory_id == old_memory.id))
        operations = [version.operation for version in result.scalars()]

    assert old_memory.status == MemoryStatus.superseded
    assert operations == ["created", "superseded"]


async def _version_operations(db, memory_id: str) -> list[str]:
    result = await db.execute(select(MemoryVersion).where(MemoryVersion.memory_id == memory_id))
    return [version.operation for version in result.scalars()]


@pytest.mark.asyncio
async def test_memory_service_create_update_archive_delete_restore_flow():
    async with AsyncSessionLocal() as db:
        service = MemoryService(db)
        memory = await service.create_memory("  手动记忆：偏好 FastAPI  ", importance=3, memory_type="preference")
        assert memory.content == "手动记忆：偏好 FastAPI"
        assert memory.status == MemoryStatus.active

        updated = await service.update_memory(memory.id, content="手动记忆：偏好 SQLAlchemy", importance=4)
        assert updated.content == "手动记忆：偏好 SQLAlchemy"
        assert updated.importance == 4
        assert updated.status == MemoryStatus.active

        archived = await service.archive_memory(memory.id)
        assert archived.status == MemoryStatus.archived

        updated_archived = await service.update_memory(memory.id, content="归档后仍可编辑")
        assert updated_archived.content == "归档后仍可编辑"
        assert updated_archived.status == MemoryStatus.archived

        deleted = await service.soft_delete_memory(memory.id)
        assert deleted.status == MemoryStatus.deleted

        restored = await service.restore_memory(memory.id)
        assert restored.status == MemoryStatus.active

        assert await _version_operations(db, memory.id) == [
            "created",
            "updated",
            "archived",
            "updated",
            "deleted",
            "restored",
        ]


@pytest.mark.asyncio
async def test_memory_service_idempotent_actions_do_not_duplicate_versions():
    async with AsyncSessionLocal() as db:
        service = MemoryService(db)
        memory = await service.create_memory("幂等测试记忆")

        await service.archive_memory(memory.id)
        await service.archive_memory(memory.id)
        await service.soft_delete_memory(memory.id)
        await service.soft_delete_memory(memory.id)
        await service.restore_memory(memory.id)
        await service.restore_memory(memory.id)

        assert await _version_operations(db, memory.id) == ["created", "archived", "deleted", "restored"]


@pytest.mark.asyncio
async def test_memory_service_rejects_deleted_and_superseded_operations():
    async with AsyncSessionLocal() as db:
        service = MemoryService(db)
        deleted = await service.create_memory("删除态不可编辑")
        await service.soft_delete_memory(deleted.id)
        with pytest.raises(InvalidMemoryOperationError):
            await service.update_memory(deleted.id, content="should fail")

        superseded = Memory(content="历史替代记忆", status=MemoryStatus.superseded)
        db.add(superseded)
        await db.flush()

        with pytest.raises(InvalidMemoryOperationError):
            await service.update_memory(superseded.id, content="should fail")
        with pytest.raises(InvalidMemoryOperationError):
            await service.archive_memory(superseded.id)
        with pytest.raises(InvalidMemoryOperationError):
            await service.soft_delete_memory(superseded.id)
        with pytest.raises(InvalidMemoryOperationError):
            await service.restore_memory(superseded.id)


@pytest.mark.asyncio
async def test_memory_service_retrieve_only_returns_active_by_default():
    async with AsyncSessionLocal() as db:
        service = MemoryService(db)
        active = await service.create_memory("retrieval-marker active")
        archived = await service.create_memory("retrieval-marker archived")
        deleted = await service.create_memory("retrieval-marker deleted")
        superseded = Memory(content="retrieval-marker superseded", status=MemoryStatus.superseded, importance=5)
        db.add(superseded)
        await db.flush()
        await service.archive_memory(archived.id)
        await service.soft_delete_memory(deleted.id)

        retrieved = await service.retrieve("retrieval-marker", limit=10)

        assert [memory.id for memory in retrieved] == [active.id]


@pytest.mark.asyncio
async def test_extract_query_terms_is_conservative_for_chinese_and_english():
    terms = extract_query_terms("我偏好 FastAPI 示例，尤其喜欢 SQLAlchemy，也请用中文")

    assert terms == ["FastAPI", "SQLAlchemy", "偏好", "喜欢", "示例"]
    assert len(extract_query_terms("请帮我看看这个没有明显分隔的超长中文句子不要爆炸", max_terms=5)) <= 5


@pytest.mark.asyncio
async def test_memory_service_retrieve_matches_scores_and_explains_results():
    async with AsyncSessionLocal() as db:
        service = MemoryService(db)
        fastapi = await service.create_memory("我偏好 FastAPI retrieval-score 示例", importance=2)
        sqlalchemy = await service.create_memory("我偏好 SQLAlchemy ORM retrieval-score 示例", importance=5)
        await service.mark_used([fastapi.id, fastapi.id, sqlalchemy.id])

        matches = await service.retrieve_matches("FastAPI retrieval-score 示例", limit=2)

        assert matches[0].id == fastapi.id
        assert matches[0].score > 0
        assert "FastAPI" in matches[0].matched_terms
        assert "命中" in matches[0].reason
        assert fastapi.use_count == 1
        assert fastapi.last_used_at is not None


@pytest.mark.asyncio
async def test_mark_used_ignores_duplicate_and_non_active_memories():
    async with AsyncSessionLocal() as db:
        service = MemoryService(db)
        active = await service.create_memory("我偏好 FastAPI 示例")
        archived = await service.create_memory("我偏好 Django 示例")
        await service.archive_memory(archived.id)

        await service.mark_used([active.id, active.id, archived.id])

        assert active.use_count == 1
        assert active.last_used_at is not None
        assert archived.use_count == 0
        assert archived.last_used_at is None


@pytest.mark.asyncio
async def test_conflict_key_does_not_supersede_without_replacement_marker():
    async with AsyncSessionLocal() as db:
        session = Session()
        db.add(session)
        await db.flush()
        first = Message(session_id=session.id, role=MessageRole.user, content="请记住：我偏好 FastAPI 示例")
        second = Message(session_id=session.id, role=MessageRole.user, content="请记住：我偏好 Django 示例")
        db.add_all([first, second])
        await db.flush()
        service = MemoryService(db)
        old_memory = (await service.extract_and_store(first.id, first.content))[0]

        await service.extract_and_store(second.id, second.content)

        assert conflict_key_for_content(old_memory.content) == "preference:framework-example"
        assert has_replacement_marker(second.content) is False
        assert old_memory.status == MemoryStatus.active
        assert await _version_operations(db, old_memory.id) == ["created"]


@pytest.mark.asyncio
async def test_conflict_key_supersedes_only_with_replacement_marker():
    async with AsyncSessionLocal() as db:
        session = Session()
        db.add(session)
        await db.flush()
        first = Message(session_id=session.id, role=MessageRole.user, content="请记住：我偏好 FastAPI 示例")
        second = Message(session_id=session.id, role=MessageRole.user, content="请记住：我偏好以后用 Django 示例")
        db.add_all([first, second])
        await db.flush()
        service = MemoryService(db)
        old_memory = (await service.extract_and_store(first.id, first.content))[0]

        await service.extract_and_store(second.id, second.content)

        assert has_replacement_marker(second.content) is True
        assert old_memory.status == MemoryStatus.superseded
        assert await _version_operations(db, old_memory.id) == ["created", "superseded"]


@pytest.mark.asyncio
async def test_memory_service_validates_payloads():
    async with AsyncSessionLocal() as db:
        service = MemoryService(db)

        with pytest.raises(InvalidMemoryPayloadError):
            await service.create_memory("   ")
        with pytest.raises(InvalidMemoryPayloadError):
            await service.create_memory("bad importance", importance=6)
        with pytest.raises(InvalidMemoryPayloadError):
            await service.update_memory("missing")
