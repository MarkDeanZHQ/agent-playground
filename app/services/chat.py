import json
from collections.abc import AsyncIterator

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.runner import AgentRunner
from app.db.models import AgentRun, AgentStep, Message, MessageRole, Session
from app.memory.service import MemoryService
from app.schemas.api import ChatResponse, StreamEvent
from app.services.session_summary import SessionSummaryService
from app.tools.builtin import build_default_registry


class ChatService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.memory = MemoryService(db)
        self.session_summary = SessionSummaryService(db)
        self.tools = build_default_registry()

    async def create_session(self, title: str | None = None) -> Session:
        session = Session(title=title)
        self.db.add(session)
        await self.db.commit()
        return session

    async def chat(self, user_message: str, session_id: str | None = None) -> ChatResponse:
        session = await self._get_or_create_session(session_id)
        summary_result = await self.session_summary.maybe_update_summary_before_current_turn(session.id)
        user_db_message = Message(session_id=session.id, role=MessageRole.user, content=user_message)
        self.db.add(user_db_message)
        await self.db.flush()

        recent_messages = await self._recent_messages(session.id)
        memories = await self.memory.retrieve_matches(user_message)
        await self.memory.mark_used([memory.id for memory in memories])
        runner = AgentRunner(self.db, self.tools)
        run = await runner.run(session.id, user_message, memories, recent_messages, summary_result)

        assistant_message = Message(
            session_id=session.id,
            role=MessageRole.assistant,
            content=run.final_answer or "",
        )
        self.db.add(assistant_message)
        await self.db.flush()
        await self._extract_and_trace_memory(run, user_db_message.id, user_message)
        await self.db.commit()

        return ChatResponse(
            session_id=session.id,
            run_id=run.id,
            message_id=assistant_message.id,
            answer=assistant_message.content,
            used_tools=getattr(run, "used_tools", []),
            used_memories=[memory.id for memory in memories],
        )

    async def stream_chat(
        self,
        user_message: str,
        session_id: str | None = None,
    ) -> AsyncIterator[str]:
        run_id: str | None = None
        assistant_text = ""
        try:
            session = await self._get_or_create_session(session_id)
            summary_result = await self.session_summary.maybe_update_summary_before_current_turn(session.id)
            user_db_message = Message(session_id=session.id, role=MessageRole.user, content=user_message)
            self.db.add(user_db_message)
            await self.db.flush()

            recent_messages = await self._recent_messages(session.id)
            memories = await self.memory.retrieve_matches(user_message)
            await self.memory.mark_used([memory.id for memory in memories])
            if memories:
                yield self._encode_sse(
                    StreamEvent(
                        event="memory_used",
                        data={
                            "memories": [memory.content for memory in memories],
                            "memory_ids": [memory.id for memory in memories],
                            "matches": [
                                {
                                    "memory_id": memory.id,
                                    "content": memory.content,
                                    "score": memory.score,
                                    "matched_terms": memory.matched_terms,
                                    "reason": memory.reason,
                                }
                                for memory in memories
                            ],
                        },
                    )
                )
            runner = AgentRunner(self.db, self.tools)
            async for event in runner.stream(
                session.id,
                user_message,
                memories,
                recent_messages,
                summary_result,
            ):
                if run_id is None and event.data.get("run_id"):
                    run_id = str(event.data["run_id"])
                if event.event == "message_delta" and isinstance(event.data.get("text"), str):
                    assistant_text += str(event.data["text"])
                if event.event == "run_finished" and not assistant_text:
                    assistant_text = str(event.data.get("text", ""))
                yield self._encode_sse(event)

            if assistant_text:
                assistant_message = Message(
                    session_id=session.id,
                    role=MessageRole.assistant,
                    content=assistant_text,
                )
                self.db.add(assistant_message)
                await self.db.flush()
            if run_id is not None:
                await self._extract_and_trace_memory_id(run_id, user_db_message.id, user_message)
            else:
                await self.memory.extract_and_store(user_db_message.id, user_message)
            await self.db.commit()
        except Exception as exc:
            try:
                await self.db.rollback()
            except Exception:
                pass
            data: dict[str, object] = {
                "message": f"流式响应中断：{exc.__class__.__name__}",
                "detail": str(exc),
            }
            if run_id is not None:
                data["run_id"] = run_id
            yield self._encode_sse(StreamEvent(event="stream_error", data=data))

    async def get_run(self, run_id: str) -> AgentRun | None:
        result = await self.db.execute(select(AgentRun).where(AgentRun.id == run_id))
        return result.scalar_one_or_none()

    async def _get_or_create_session(self, session_id: str | None) -> Session:
        if session_id:
            result = await self.db.execute(select(Session).where(Session.id == session_id))
            session = result.scalar_one_or_none()
            if session is not None:
                return session
        session = Session()
        self.db.add(session)
        await self.db.flush()
        return session

    async def _recent_messages(self, session_id: str, limit: int = 6) -> list[tuple[str, str]]:
        result = await self.db.execute(
            select(Message).where(Message.session_id == session_id).order_by(Message.created_at.desc()).limit(limit)
        )
        messages = list(result.scalars())
        recent: list[tuple[str, str]] = []
        for message in reversed(messages):
            role = message.role.value if hasattr(message.role, "value") else str(message.role)
            recent.append((role, message.content))
        return recent

    async def _extract_and_trace_memory(self, run: AgentRun, message_id: str, text: str) -> None:
        await self._trace_memory_step(run.id, "memory_extraction_started", {"message_id": message_id})
        should_store, reason = self.memory.policy.decision(text)
        await self._trace_memory_step(
            run.id,
            "memory_policy_decision",
            {"should_store": should_store, "reason": reason},
        )
        candidate = self.memory.candidate_content(text)
        superseded = []
        if should_store:
            superseded = await self.memory.find_supersede_candidates(candidate, text)
        memories = await self.memory.extract_and_store(message_id, text)
        for memory in superseded:
            await self._trace_memory_step(
                run.id,
                "memory_superseded",
                {"memory_id": memory.id, "content": memory.content, "conflict_key": memory.conflict_key},
            )
        if memories:
            for memory in memories:
                await self._trace_memory_step(
                    run.id,
                    "memory_saved",
                    {"memory_id": memory.id, "content": memory.content, "conflict_key": memory.conflict_key},
                )
        else:
            await self._trace_memory_step(run.id, "memory_skipped", {"reason": reason})

    async def _extract_and_trace_memory_id(self, run_id: str, message_id: str, text: str) -> None:
        result = await self.db.execute(select(AgentRun).where(AgentRun.id == run_id))
        run = result.scalar_one_or_none()
        if run is None:
            await self.memory.extract_and_store(message_id, text)
            return
        await self._extract_and_trace_memory(run, message_id, text)

    async def _trace_memory_step(self, run_id: str, kind: str, data: dict[str, object]) -> None:
        result = await self.db.execute(select(func.max(AgentStep.step_index)).where(AgentStep.run_id == run_id))
        next_index = (result.scalar_one_or_none() or -1) + 1
        self.db.add(
            AgentStep(
                run_id=run_id,
                step_index=next_index,
                kind=kind,
                content=json.dumps(data, ensure_ascii=False),
            )
        )
        await self.db.flush()

    def _encode_sse(self, event: StreamEvent) -> str:
        return f"event: {event.event}\ndata: {json.dumps(event.data, ensure_ascii=False)}\n\n"
