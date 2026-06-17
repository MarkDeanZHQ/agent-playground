import re
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Memory, MemoryStatus, MemoryVersion, Message, utc_now


@dataclass(frozen=True)
class RetrievedMemory:
    id: str
    content: str
    score: int
    matched_terms: list[str]
    reason: str
    scope: str
    category: str
    source_kind: str
    confidence: int
    conflict_key: str | None
    rank_signals: dict[str, int]


@dataclass(frozen=True)
class MemoryConflictDecision:
    resolution: str
    reason: str
    conflict_key: str | None
    candidate_ids: list[str]
    superseded_ids: list[str]


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
        session_id: str | None = None,
    ) -> list[Memory]:
        candidates, terms = await self._candidate_memories(query, limit, status, session_id)
        return self._rank_memories(candidates, terms)[:limit]

    async def retrieve_matches(
        self,
        query: str = "",
        limit: int = 3,
        session_id: str | None = None,
    ) -> list[RetrievedMemory]:
        candidates, terms = await self._candidate_memories(query, limit, MemoryStatus.active, session_id)
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
        scope: str = "project",
        category: str = "preference",
        source_kind: str = "manual",
        confidence: int = 3,
        session_id: str | None = None,
        owner_id: str | None = None,
        sensitivity: str = "public",
        expires_at=None,
    ) -> Memory:
        validated_content = self._validate_content(content)
        memory = Memory(
            content=validated_content,
            importance=self._validate_importance(importance),
            memory_type=self._validate_memory_type(memory_type),
            scope=self._validate_scope(scope),
            category=self._validate_category(category),
            source_kind=self._validate_source_kind(source_kind),
            confidence=self._validate_confidence(confidence),
            session_id=session_id,
            owner_id=owner_id,
            sensitivity=self._validate_sensitivity(sensitivity),
            expires_at=expires_at,
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
        scope: str | None = None,
        category: str | None = None,
        source_kind: str | None = None,
        confidence: int | None = None,
        session_id: str | None = None,
        owner_id: str | None = None,
        sensitivity: str | None = None,
        expires_at=None,
    ) -> Memory:
        if (
            content is None
            and importance is None
            and memory_type is None
            and scope is None
            and category is None
            and source_kind is None
            and confidence is None
            and session_id is None
            and owner_id is None
            and sensitivity is None
            and expires_at is None
        ):
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
        if scope is not None:
            memory.scope = self._validate_scope(scope)
        if category is not None:
            memory.category = self._validate_category(category)
        if source_kind is not None:
            memory.source_kind = self._validate_source_kind(source_kind)
        if confidence is not None:
            memory.confidence = self._validate_confidence(confidence)
        if session_id is not None:
            memory.session_id = session_id
        if owner_id is not None:
            memory.owner_id = owner_id
        if sensitivity is not None:
            memory.sensitivity = self._validate_sensitivity(sensitivity)
        if expires_at is not None:
            memory.expires_at = expires_at
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

    async def extract_and_store(self, message_id: str, text: str, session_id: str | None = None) -> list[Memory]:
        should_store, _reason = self.policy.decision(text)
        if not should_store:
            return []

        effective_session_id = session_id or await self._session_id_for_message(message_id)
        content = self.candidate_content(text)
        conflict_key = conflict_key_for_content(content)
        decision = await self.resolve_conflict(content, text, effective_session_id)
        existing = await self.find_supersede_candidates(content, text, effective_session_id)
        for memory in existing:
            if memory.content != content:
                memory.status = MemoryStatus.superseded
                memory.updated_at = utc_now()
                self._record_version(memory, "superseded")

        memory = Memory(
            content=content,
            importance=2,
            source_message_id=message_id,
            scope="session",
            category="preference",
            source_kind="user_message",
            confidence=3,
            session_id=effective_session_id,
            supersedes_memory_id=decision.superseded_ids[0] if decision.superseded_ids else None,
            conflict_key=conflict_key,
        )
        self.db.add(memory)
        await self.db.flush()
        self._record_version(memory, "created")
        await self.db.flush()
        return [memory]

    async def resolve_conflict(
        self,
        content: str,
        source_text: str,
        session_id: str | None = None,
    ) -> MemoryConflictDecision:
        conflict_key = conflict_key_for_content(content)
        if conflict_key is None:
            return MemoryConflictDecision("no_conflict", "未派生出冲突键，按普通新增处理", None, [], [])
        candidates = await self._conflict_candidates(conflict_key, session_id)
        candidate_ids = [memory.id for memory in candidates if memory.content != content]
        if not candidate_ids:
            return MemoryConflictDecision(
                "no_conflict",
                "存在冲突键但没有同范围可替代的 active 记忆",
                conflict_key,
                [],
                [],
            )
        if not has_replacement_marker(source_text):
            return MemoryConflictDecision(
                "pending_confirmation",
                "命中同一冲突键，但用户没有表达替换意图，保守并存并等待后续确认",
                conflict_key,
                candidate_ids,
                [],
            )
        return MemoryConflictDecision(
            "supersedes",
            "命中同一冲突键且用户表达了替换意图，旧记忆标记为 superseded",
            conflict_key,
            candidate_ids,
            candidate_ids,
        )

    async def find_supersede_candidates(
        self,
        content: str,
        source_text: str,
        session_id: str | None = None,
    ) -> list[Memory]:
        decision = await self.resolve_conflict(content, source_text, session_id)
        if decision.resolution != "supersedes":
            return []
        return await self._memories_by_ids(decision.superseded_ids)

    async def _conflict_candidates(self, conflict_key: str, session_id: str | None) -> list[Memory]:
        result = await self.db.execute(
            select(Memory).where(
                Memory.status == MemoryStatus.active,
                Memory.conflict_key == conflict_key,
                self._visible_scope_filter(session_id),
            )
        )
        return list(result.scalars())

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
            if self._is_expired(memory):
                memory.status = MemoryStatus.invalidated
                memory.updated_at = now
                self._record_version(memory, "invalidated")
                continue
            memory.use_count += 1
            memory.last_used_at = now
        await self.db.flush()

    async def _candidate_memories(
        self,
        query: str,
        limit: int,
        status: MemoryStatus,
        session_id: str | None,
    ) -> tuple[list[Memory], list[str]]:
        safe_limit = min(max(limit, 1), 100)
        candidate_limit = min(max(safe_limit * 5, 20), 100)
        terms = extract_query_terms(query)
        await self.invalidate_expired()
        statement = select(Memory).where(
            Memory.status == status,
            self._visible_scope_filter(session_id),
            self._not_expired_filter(),
        )
        if terms:
            conditions = [func.lower(Memory.content).contains(term.lower()) for term in terms[:5]]
            statement = statement.where(or_(*conditions))
        statement = statement.order_by(Memory.importance.desc(), Memory.updated_at.desc()).limit(candidate_limit)
        result = await self.db.execute(statement)
        return list(result.scalars()), terms

    def _visible_scope_filter(self, session_id: str | None):
        stable_scopes = Memory.scope.in_(("project", "user"))
        if session_id is None:
            return stable_scopes
        return or_(stable_scopes, Memory.session_id == session_id)

    def _not_expired_filter(self):
        return or_(Memory.expires_at.is_(None), Memory.expires_at > utc_now())

    async def invalidate_expired(self) -> None:
        now = utc_now()
        result = await self.db.execute(
            select(Memory).where(
                Memory.status == MemoryStatus.active,
                Memory.expires_at.is_not(None),
                Memory.expires_at <= now,
            )
        )
        changed = False
        for memory in result.scalars():
            memory.status = MemoryStatus.invalidated
            memory.updated_at = now
            self._record_version(memory, "invalidated")
            changed = True
        if changed:
            await self.db.flush()

    def _rank_memories(self, memories: list[Memory], terms: list[str]) -> list[Memory]:
        return sorted(
            memories,
            key=lambda memory: (self._score_memory(memory, terms), self._updated_at_timestamp(memory)),
            reverse=True,
        )

    def _to_retrieved_memory(self, memory: Memory, terms: list[str]) -> RetrievedMemory:
        matched_terms = self._matched_terms(memory, terms)
        score = self._score_memory(memory, terms)
        rank_signals = self._rank_signals(memory, terms)
        if matched_terms:
            reason = (
                f"命中 {len(matched_terms)} 个关键词，"
                f"importance={memory.importance}，use_count={memory.use_count}，scope={memory.scope}"
            )
        else:
            reason = (
                f"无关键词命中，按 importance={memory.importance}，"
                f"use_count={memory.use_count}，recency={rank_signals['recency']} 排序"
            )
        return RetrievedMemory(
            id=memory.id,
            content=memory.content,
            score=score,
            matched_terms=matched_terms,
            reason=reason,
            scope=memory.scope,
            category=memory.category,
            source_kind=memory.source_kind,
            confidence=memory.confidence,
            conflict_key=memory.conflict_key,
            rank_signals=rank_signals,
        )

    def _score_memory(self, memory: Memory, terms: list[str]) -> int:
        signals = self._rank_signals(memory, terms)
        return (
            signals["term_hits"] * 10
            + signals["importance"] * 3
            + signals["use_count"]
            + signals["confidence"]
            + signals["scope"]
            + signals["recency"]
        )

    def _rank_signals(self, memory: Memory, terms: list[str]) -> dict[str, int]:
        hit_count = len(self._matched_terms(memory, terms))
        return {
            "term_hits": hit_count,
            "importance": memory.importance,
            "use_count": min(memory.use_count, 10),
            "confidence": memory.confidence,
            "scope": self._scope_bonus(memory.scope),
            "recency": self._recency_bonus(memory),
        }

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

    def _scope_bonus(self, scope: str) -> int:
        return {"session": 3, "project": 2, "user": 2, "working": 1}.get(scope, 0)

    async def _memories_by_ids(self, memory_ids: list[str]) -> list[Memory]:
        if not memory_ids:
            return []
        result = await self.db.execute(select(Memory).where(Memory.id.in_(memory_ids)))
        memories_by_id = {memory.id: memory for memory in result.scalars()}
        return [memories_by_id[memory_id] for memory_id in memory_ids if memory_id in memories_by_id]

    async def _session_id_for_message(self, message_id: str) -> str | None:
        result = await self.db.execute(select(Message.session_id).where(Message.id == message_id))
        return result.scalar_one_or_none()

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

    def _validate_scope(self, scope: str) -> str:
        normalized = scope.strip()
        if normalized not in {"working", "session", "user", "project"}:
            raise InvalidMemoryPayloadError("Memory scope must be working/session/user/project")
        return normalized

    def _validate_category(self, category: str) -> str:
        normalized = category.strip()
        allowed = {"preference", "fact", "constraint", "goal", "decision", "todo", "summary_note"}
        if normalized not in allowed:
            message = "Memory category must be preference/fact/constraint/goal/decision/todo/summary_note"
            raise InvalidMemoryPayloadError(message)
        return normalized

    def _validate_source_kind(self, source_kind: str) -> str:
        normalized = source_kind.strip()
        if normalized not in {"user_message", "assistant_inference", "tool_result", "manual"}:
            message = "Memory source_kind must be user_message/assistant_inference/tool_result/manual"
            raise InvalidMemoryPayloadError(message)
        return normalized

    def _validate_confidence(self, confidence: int) -> int:
        if not 1 <= confidence <= 5:
            raise InvalidMemoryPayloadError("Memory confidence must be between 1 and 5")
        return confidence

    def _validate_sensitivity(self, sensitivity: str) -> str:
        normalized = sensitivity.strip()
        if normalized not in {"public", "private", "secret"}:
            raise InvalidMemoryPayloadError("Memory sensitivity must be public/private/secret")
        return normalized

    def _record_version(self, memory: Memory, operation: str) -> None:
        self.db.add(MemoryVersion(memory_id=memory.id, content=memory.content, operation=operation))

    def _status_value(self, status: MemoryStatus | str) -> str:
        return getattr(status, "value", status)

    def _is_expired(self, memory: Memory) -> bool:
        if memory.expires_at is None:
            return False
        expires_at = memory.expires_at
        now = utc_now()
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=now.tzinfo)
        return expires_at <= now
