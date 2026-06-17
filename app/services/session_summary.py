from __future__ import annotations

import json
from dataclasses import dataclass, field

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
    summary_json: dict[str, list[str] | str]
    summary_blocks: list[dict[str, object]] = field(default_factory=list)

    def trace_payload(self, session_id: str) -> dict[str, object]:
        return {
            "session_id": session_id,
            "checked": self.checked,
            "updated": self.updated,
            "used": self.used,
            "covered_message_count": self.covered_message_count,
            "summary_chars": self.summary_chars,
            "newly_summarized_messages": self.newly_summarized_messages,
            "summary_json": self.summary_json,
            "summary_blocks": self.summary_blocks,
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

        summary_json = self._summarize_json(summary.summary_json if summary is not None else "{}", messages)
        content = self._format_summary(summary_json)
        if summary is None:
            summary = SessionSummary(
                session_id=session_id,
                content=content,
                summary_json=json.dumps(summary_json, ensure_ascii=False),
                covered_message_count=cutoff_count,
            )
            self.db.add(summary)
        else:
            summary.content = content
            summary.summary_json = json.dumps(summary_json, ensure_ascii=False)
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

    def _summarize_json(self, previous_summary: str, messages: list[Message]) -> dict[str, list[str] | str]:
        parsed = self._parse_summary_json(previous_summary)
        for block in (
            "active_goal",
            "confirmed_constraints",
            "user_preferences_seen_this_session",
            "done",
            "pending",
            "open_questions",
            "important_artifacts",
        ):
            parsed.setdefault(block, [])
        for message in messages:
            role = message.role.value if hasattr(message.role, "value") else str(message.role)
            text = self._important_text(message.content)
            if any(term in text for term in ("必须", "不要", "约束")):
                bucket = "confirmed_constraints"
            elif role == "assistant":
                bucket = "done"
            else:
                bucket = "pending"
            parsed.setdefault(bucket, [])
            items = parsed[bucket]
            if isinstance(items, list):
                items.append(f"{role}: {text}")
        return parsed

    def _parse_summary_json(self, previous_summary: str) -> dict[str, list[str] | str]:
        if not previous_summary.strip():
            return {}
        try:
            data = json.loads(previous_summary)
        except json.JSONDecodeError:
            return {"done": [line for line in previous_summary.splitlines() if line.strip()]}
        return data if isinstance(data, dict) else {}

    def _format_summary(self, summary_json: dict[str, list[str] | str]) -> str:
        lines: list[str] = []
        for key in (
            "active_goal",
            "confirmed_constraints",
            "user_preferences_seen_this_session",
            "done",
            "pending",
            "open_questions",
            "important_artifacts",
        ):
            value = summary_json.get(key)
            if not value:
                continue
            lines.append(f"{key}:")
            if isinstance(value, list):
                lines.extend(f"- {item}" for item in value)
            else:
                lines.append(f"- {value}")
        return "\n".join(lines)[: self.settings.summary_max_chars]

    def _important_text(self, content: str) -> str:
        normalized = " ".join(content.split())
        if any(term in normalized for term in self.KEY_TERMS):
            return normalized
        return normalized[:160]

    def _result(
        self,
        summary: SessionSummary | None,
        *,
        checked: bool,
        updated: bool,
        newly_summarized_messages: int,
        force_unused: bool = False,
    ) -> SummaryResult:
        if summary is None:
            return SummaryResult(
                summary=None,
                checked=checked,
                updated=updated,
                used=False and not force_unused,
                covered_message_count=0,
                summary_chars=0,
                newly_summarized_messages=newly_summarized_messages,
                summary_json={},
                summary_blocks=[],
            )
        parsed = self._parse_summary_json(summary.summary_json)
        blocks = [
            {"name": key, "items": value, "count": len(value) if isinstance(value, list) else 1}
            for key, value in parsed.items()
        ]
        content = None if force_unused else summary.content or None
        return SummaryResult(
            summary=content,
            checked=checked,
            updated=updated,
            used=bool(content),
            covered_message_count=summary.covered_message_count,
            summary_chars=len(content or ""),
            newly_summarized_messages=newly_summarized_messages,
            summary_json=parsed,
            summary_blocks=blocks,
        )
