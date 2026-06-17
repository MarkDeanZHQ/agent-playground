import re
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Memory, MemoryStatus, MemoryVersion, utc_now


@dataclass(frozen=True)
class RetrievedMemory:
    id: str
    content: str
    score: int
    matched_terms: list[str]
    reason: str


TECHNICAL_TERMS = {
    "fastapi": "FastAPI",
    "sqlalchemy": "SQLAlchemy",
    "claude": "Claude",
    "openai": "OpenAI",
    "docker": "Docker",
    "sqlite": "SQLite",
}
STOP_WORDS = {"请", "我", "你", "帮我", "什么", "如何", "the", "and", "please"}
CHINESE_KEY_TERMS = (
    "偏好",
    "喜欢",
    "记住",
    "不要",
    "必须",
    "示例",
    "框架",
    "后端",
    "数据库",
    "中文",
    "英文",
)
REPLACEMENT_MARKERS = (
    "改为",
    "不再",
    "以后用",
    "替换成",
    "以后不要",
    "instead",
    " instead of ",
)


class MemoryServiceError(ValueError):
    pass


class MemoryNotFoundError(MemoryServiceError):
    pass


class InvalidMemoryOperationError(MemoryServiceError):
    pass


class InvalidMemoryPayloadError(MemoryServiceError):
    pass


class MemoryPolicy:
    sensitive_markers = ("api key", "apikey", "password", "token", "secret", "密码", "密钥")
    temporary_markers = ("今天", "临时", "一次性", "just this time")

    def should_store(self, text: str) -> bool:
        return self.decision(text)[0]

    def decision(self, text: str) -> tuple[bool, str]:
        normalized = text.lower()
        if not any(marker in normalized for marker in ("记住", "remember", "偏好", "prefer")):
            return False, "文本没有表达稳定偏好或记忆意图"
        if any(marker in normalized for marker in self.sensitive_markers):
            return False, "文本包含敏感信息标记"
        if any(marker in normalized for marker in self.temporary_markers):
            return False, "文本看起来是临时或一次性信息"
        return True, "文本表达了可复用的稳定偏好"


def _add_unique_term(terms: list[str], term: str, max_terms: int) -> bool:
    normalized = term.strip()
    if not normalized or normalized.lower() in STOP_WORDS:
        return False
    if normalized.lower() in {item.lower() for item in terms}:
        return False
    terms.append(normalized)
    return len(terms) >= max_terms


def _technical_terms(text: str) -> list[str]:
    values = []
    for match in re.findall(r"[A-Za-z][A-Za-z0-9_+#.-]*|\d+", text):
        if len(match) >= 2:
            values.append(TECHNICAL_TERMS.get(match.lower(), match))
    return values


def _known_chinese_terms(text: str) -> list[str]:
    return [term for term in CHINESE_KEY_TERMS if term in text]


def _short_chinese_fragments(text: str) -> list[str]:
    fragments = []
    for fragment in re.split(r"[，。！？、；：,.;:!?\s]+", text):
        normalized = fragment.strip()
        if 2 <= len(normalized) <= 12 and re.search(r"[一-鿿]", normalized):
            fragments.append(normalized)
        if len(fragments) >= 3:
            break
    return fragments


def extract_query_terms(text: str, max_terms: int = 5) -> list[str]:
    terms: list[str] = []
    for term in [*_technical_terms(text), *_known_chinese_terms(text), *_short_chinese_fragments(text)]:
        if _add_unique_term(terms, term, max_terms):
            break
    return terms[:max_terms]


def has_replacement_marker(text: str) -> bool:
    normalized = text.lower()
    return ("prefer" in normalized and " over " in normalized) or any(
        marker in normalized for marker in REPLACEMENT_MARKERS
    )


def conflict_key_for_content(content: str) -> str | None:
    normalized = content.lower()
    has_preference = any(marker in normalized for marker in ("偏好", "喜欢", "prefer", "like"))
    if not has_preference:
        return None
    if any(term in normalized for term in ("中文", "英文", "chinese", "english", "language")):
        return "preference:language"
    if any(term in normalized for term in ("sqlalchemy", "orm")):
        return "preference:orm-example"
    if any(term in normalized for term in ("fastapi", "django", "flask", "api", "框架", "后端", "framework")):
        return "preference:framework-example"
    return None


class MemoryService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.policy = MemoryPolicy()

    def candidate_content(self, text: str) -> str:
        return text.replace("请记住", "").replace("记住", "").replace("remember", "").strip(" ：:")

    async def retrieve(
        self,
        query: str = "",
        limit: int = 3,
        status: MemoryStatus = MemoryStatus.active,
    ) -> list[Memory]:
        candidates, terms = await self._candidate_memories(query, limit, status)
        return self._rank_memories(candidates, terms)[:limit]

    async def retrieve_matches(self, query: str = "", limit: int = 3) -> list[RetrievedMemory]:
        candidates, terms = await self._candidate_memories(query, limit, MemoryStatus.active)
        ranked = self._rank_memories(candidates, terms)[:limit]
        return [self._to_retrieved_memory(memory, terms) for memory in ranked]

    async def list_memories(
        self,
        query: str | None = None,
        status: MemoryStatus | None = None,
        limit: int = 20,
    ) -> list[Memory]:
        safe_limit = min(max(limit, 1), 100)
        statement = select(Memory).order_by(Memory.updated_at.desc())
        if status is not None:
            statement = statement.where(Memory.status == status)
        result = await self.db.execute(statement)
        memories = list(result.scalars())
        if query:
            terms = extract_query_terms(query)
            if terms:
                memories = [memory for memory in memories if self._matched_terms(memory, terms)]
        return memories[:safe_limit]

    async def create_memory(
        self,
        content: str,
        importance: int = 2,
        memory_type: str = "preference",
    ) -> Memory:
        validated_content = self._validate_content(content)
        memory = Memory(
            content=validated_content,
            importance=self._validate_importance(importance),
            memory_type=self._validate_memory_type(memory_type),
            status=MemoryStatus.active,
            conflict_key=conflict_key_for_content(validated_content),
        )
        self.db.add(memory)
        await self.db.flush()
        self._record_version(memory, "created")
        await self.db.flush()
        return memory

    async def update_memory(
        self,
        memory_id: str,
        content: str | None = None,
        importance: int | None = None,
        memory_type: str | None = None,
    ) -> Memory:
        if content is None and importance is None and memory_type is None:
            raise InvalidMemoryPayloadError("At least one memory field must be provided")

        memory = await self._get_memory(memory_id)
        status = self._status_value(memory.status)
        if status in {MemoryStatus.deleted.value, MemoryStatus.superseded.value}:
            raise InvalidMemoryOperationError(f"Cannot update {status} memory")

        if content is not None:
            memory.content = self._validate_content(content)
            memory.conflict_key = conflict_key_for_content(memory.content)
        if importance is not None:
            memory.importance = self._validate_importance(importance)
        if memory_type is not None:
            memory.memory_type = self._validate_memory_type(memory_type)
        memory.updated_at = utc_now()
        self._record_version(memory, "updated")
        await self.db.flush()
        return memory

    async def archive_memory(self, memory_id: str) -> Memory:
        memory = await self._get_memory(memory_id)
        status = self._status_value(memory.status)
        if status == MemoryStatus.archived.value:
            return memory
        if status != MemoryStatus.active.value:
            raise InvalidMemoryOperationError(f"Cannot archive {status} memory")

        memory.status = MemoryStatus.archived
        memory.updated_at = utc_now()
        self._record_version(memory, "archived")
        await self.db.flush()
        return memory

    async def soft_delete_memory(self, memory_id: str) -> Memory:
        memory = await self._get_memory(memory_id)
        status = self._status_value(memory.status)
        if status == MemoryStatus.deleted.value:
            return memory
        if status == MemoryStatus.superseded.value:
            raise InvalidMemoryOperationError("Cannot delete superseded memory")

        memory.status = MemoryStatus.deleted
        memory.updated_at = utc_now()
        self._record_version(memory, "deleted")
        await self.db.flush()
        return memory

    async def restore_memory(self, memory_id: str) -> Memory:
        memory = await self._get_memory(memory_id)
        status = self._status_value(memory.status)
        if status == MemoryStatus.active.value:
            return memory
        if status == MemoryStatus.superseded.value:
            raise InvalidMemoryOperationError("Cannot restore superseded memory")
        if status not in {MemoryStatus.archived.value, MemoryStatus.deleted.value}:
            raise InvalidMemoryOperationError(f"Cannot restore {status} memory")

        memory.status = MemoryStatus.active
        memory.updated_at = utc_now()
        self._record_version(memory, "restored")
        await self.db.flush()
        return memory

    async def extract_and_store(self, message_id: str, text: str) -> list[Memory]:
        should_store, _reason = self.policy.decision(text)
        if not should_store:
            return []

        content = self.candidate_content(text)
        conflict_key = conflict_key_for_content(content)
        existing = await self.find_supersede_candidates(content, text)
        for memory in existing:
            if memory.content != content:
                memory.status = MemoryStatus.superseded
                memory.updated_at = utc_now()
                self._record_version(memory, "superseded")

        memory = Memory(
            content=content,
            importance=2,
            source_message_id=message_id,
            conflict_key=conflict_key,
        )
        self.db.add(memory)
        await self.db.flush()
        self._record_version(memory, "created")
        await self.db.flush()
        return [memory]

    async def find_supersede_candidates(self, content: str, source_text: str) -> list[Memory]:
        conflict_key = conflict_key_for_content(content)
        if conflict_key is None or not has_replacement_marker(source_text):
            return []
        result = await self.db.execute(
            select(Memory).where(
                Memory.status == MemoryStatus.active,
                Memory.conflict_key == conflict_key,
            )
        )
        return [memory for memory in result.scalars() if memory.content != content]

    async def mark_used(self, memory_ids: Sequence[str]) -> None:
        unique_ids = {memory_id for memory_id in memory_ids if memory_id}
        if not unique_ids:
            return
        result = await self.db.execute(
            select(Memory).where(
                Memory.id.in_(unique_ids),
                Memory.status == MemoryStatus.active,
            )
        )
        now = utc_now()
        for memory in result.scalars():
            memory.use_count += 1
            memory.last_used_at = now
        await self.db.flush()

    async def _candidate_memories(
        self,
        query: str,
        limit: int,
        status: MemoryStatus,
    ) -> tuple[list[Memory], list[str]]:
        safe_limit = min(max(limit, 1), 100)
        candidate_limit = min(max(safe_limit * 5, 20), 100)
        terms = extract_query_terms(query)
        statement = select(Memory).where(Memory.status == status)
        if terms:
            conditions = [func.lower(Memory.content).contains(term.lower()) for term in terms[:5]]
            statement = statement.where(or_(*conditions))
        statement = statement.order_by(Memory.importance.desc(), Memory.updated_at.desc()).limit(candidate_limit)
        result = await self.db.execute(statement)
        return list(result.scalars()), terms

    def _rank_memories(self, memories: list[Memory], terms: list[str]) -> list[Memory]:
        return sorted(
            memories,
            key=lambda memory: (self._score_memory(memory, terms), self._updated_at_timestamp(memory)),
            reverse=True,
        )

    def _to_retrieved_memory(self, memory: Memory, terms: list[str]) -> RetrievedMemory:
        matched_terms = self._matched_terms(memory, terms)
        score = self._score_memory(memory, terms)
        if matched_terms:
            reason = (
                f"命中 {len(matched_terms)} 个关键词，"
                f"importance={memory.importance}，use_count={memory.use_count}"
            )
        else:
            reason = f"无关键词命中，按 importance={memory.importance}，use_count={memory.use_count} 排序"
        return RetrievedMemory(
            id=memory.id,
            content=memory.content,
            score=score,
            matched_terms=matched_terms,
            reason=reason,
        )

    def _score_memory(self, memory: Memory, terms: list[str]) -> int:
        hit_count = len(self._matched_terms(memory, terms))
        return hit_count * 10 + memory.importance * 3 + min(memory.use_count, 10) + self._recency_bonus(memory)

    def _matched_terms(self, memory: Memory, terms: list[str]) -> list[str]:
        normalized_content = memory.content.lower()
        return [term for term in terms if term.lower() in normalized_content]

    def _recency_bonus(self, memory: Memory) -> int:
        now = utc_now()
        updated_at = memory.updated_at
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=now.tzinfo)
        age = now - updated_at
        if age.days <= 1:
            return 3
        if age.days <= 7:
            return 2
        if age.days <= 30:
            return 1
        return 0

    def _updated_at_timestamp(self, memory: Memory) -> float:
        updated_at = memory.updated_at
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=utc_now().tzinfo)
        return updated_at.timestamp()

    async def _get_memory(self, memory_id: str) -> Memory:
        result = await self.db.execute(select(Memory).where(Memory.id == memory_id))
        memory = result.scalar_one_or_none()
        if memory is None:
            raise MemoryNotFoundError("Memory not found")
        return memory

    def _validate_content(self, content: str) -> str:
        normalized = content.strip()
        if not normalized:
            raise InvalidMemoryPayloadError("Memory content must not be empty")
        return normalized

    def _validate_importance(self, importance: int) -> int:
        if not 1 <= importance <= 5:
            raise InvalidMemoryPayloadError("Memory importance must be between 1 and 5")
        return importance

    def _validate_memory_type(self, memory_type: str) -> str:
        normalized = memory_type.strip()
        if not normalized:
            raise InvalidMemoryPayloadError("Memory type must not be empty")
        return normalized

    def _record_version(self, memory: Memory, operation: str) -> None:
        self.db.add(MemoryVersion(memory_id=memory.id, content=memory.content, operation=operation))

    def _status_value(self, status: MemoryStatus | str) -> str:
        return getattr(status, "value", status)
