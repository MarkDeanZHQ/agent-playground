from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import Message, SessionSummary, utc_now


@dataclass(frozen=True)
class SummaryResult:
    summary: str | None
    checked: bool
    updated: bool
    used: bool
    covered_message_count: int
    summary_chars: int
    newly_summarized_messages: int

    def trace_payload(self, session_id: str) -> dict[str, object]:
        return {
            "session_id": session_id,
            "checked": self.checked,
            "updated": self.updated,
            "used": self.used,
            "covered_message_count": self.covered_message_count,
            "summary_chars": self.summary_chars,
            "newly_summarized_messages": self.newly_summarized_messages,
        }


class SessionSummaryService:
    KEY_TERMS = ("要求", "偏好", "目标", "不要", "必须", "记住", "约束", "待办", "需要")

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.settings = get_settings()

    async def get_summary(self, session_id: str) -> SessionSummary | None:
        result = await self.db.execute(select(SessionSummary).where(SessionSummary.session_id == session_id))
        return result.scalar_one_or_none()

    async def maybe_update_summary_before_current_turn(self, session_id: str) -> SummaryResult:
        summary = await self.get_summary(session_id)
        message_count = await self._message_count(session_id)
        if not self.settings.summary_enabled:
            return self._result(summary, checked=True, updated=False, newly_summarized_messages=0, force_unused=True)

        if message_count <= self.settings.summary_trigger_message_count:
            return self._result(summary, checked=True, updated=False, newly_summarized_messages=0)

        cutoff_count = max(message_count - self.settings.summary_recent_message_keep, 0)
        covered_count = summary.covered_message_count if summary is not None else 0
        newly_summarized_messages = cutoff_count - covered_count
        if newly_summarized_messages <= 0:
            return self._result(summary, checked=True, updated=False, newly_summarized_messages=0)

        messages = await self._messages_by_position(session_id, covered_count, newly_summarized_messages)
        if not messages:
            return self._result(summary, checked=True, updated=False, newly_summarized_messages=0)

        content = self._summarize(summary.content if summary is not None else "", messages)
        if summary is None:
            summary = SessionSummary(session_id=session_id, content=content, covered_message_count=cutoff_count)
            self.db.add(summary)
        else:
            summary.content = content
            summary.covered_message_count = cutoff_count
            summary.updated_at = utc_now()
        await self.db.flush()
        return self._result(
            summary,
            checked=True,
            updated=True,
            newly_summarized_messages=len(messages),
        )

    async def build_context_summary(self, session_id: str) -> SummaryResult:
        summary = await self.get_summary(session_id)
        return self._result(summary, checked=True, updated=False, newly_summarized_messages=0)

    async def _message_count(self, session_id: str) -> int:
        result = await self.db.execute(
            select(func.count()).select_from(Message).where(Message.session_id == session_id)
        )
        return int(result.scalar_one())

    async def _messages_by_position(self, session_id: str, offset: int, limit: int) -> list[Message]:
        result = await self.db.execute(
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(Message.created_at.asc(), Message.id.asc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars())

    def _summarize(self, previous_summary: str, messages: list[Message]) -> str:
        lines: list[str] = []
        if previous_summary.strip():
            lines.append("历史摘要：")
            lines.extend(previous_summary.strip().splitlines())
        lines.append("新增历史消息：")
        for message in messages:
            role = message.role.value if hasattr(message.role, "value") else str(message.role)
            content = self._important_text(message.content)
            lines.append(f"- {role}: {content}")
        text = "\n".join(line for line in lines if line.strip())
        max_chars = self.settings.summary_max_chars
        if len(text) <= max_chars:
            return text
        return text[-max_chars:].lstrip()

    def _important_text(self, content: str) -> str:
        normalized = " ".join(content.split())
        if any(term in normalized for term in self.KEY_TERMS):
            return normalized
        return normalized[:160]

    def _result(
        self,
        summary: SessionSummary | None,
        checked: bool,
        updated: bool,
        newly_summarized_messages: int,
        force_unused: bool = False,
    ) -> SummaryResult:
        content = summary.content if summary is not None and summary.content else None
        used = bool(content) and not force_unused
        return SummaryResult(
            summary=content if used else None,
            checked=checked,
            updated=updated,
            used=used,
            covered_message_count=summary.covered_message_count if summary is not None else 0,
            summary_chars=len(content or ""),
            newly_summarized_messages=newly_summarized_messages,
        )
