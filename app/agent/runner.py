import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import AgentRun, AgentStep, RunStatus, ToolCall, utc_now
from app.memory.service import RetrievedMemory, extract_query_terms
from app.models.adapters import ModelAdapter, ModelAdapterError
from app.models.factory import create_model_adapter
from app.schemas.api import ModelTurn, StreamEvent, ToolCallResult
from app.services.session_summary import SummaryResult
from app.tools.registry import ToolRegistry


@dataclass
class _RunLoopState:
    all_tool_results: list[ToolCallResult] = field(default_factory=list)
    pending_tool_results: list[ToolCallResult] = field(default_factory=list)
    used_tools: list[str] = field(default_factory=list)


@dataclass
class _StreamMetrics:
    run_started_at: float = field(default_factory=time.perf_counter)
    first_token_at: float | None = None
    model_stream_duration: float = 0.0
    output_chars: int = 0

    def record_delta(self, text: str) -> None:
        if self.first_token_at is None:
            self.first_token_at = time.perf_counter()
        self.output_chars += len(text)

    def record_model_duration(self, started_at: float) -> None:
        self.model_stream_duration += time.perf_counter() - started_at

    def record_output(self, text: str) -> None:
        self.output_chars += len(text)

    def latency_payload(self) -> dict[str, object]:
        now = time.perf_counter()
        return {
            "time_to_first_token_ms": int((self.first_token_at - self.run_started_at) * 1000)
            if self.first_token_at is not None
            else None,
            "model_stream_duration_ms": int(self.model_stream_duration * 1000),
            "total_run_duration_ms": int((now - self.run_started_at) * 1000),
            "output_chars": self.output_chars,
            "tokens_per_second": None,
        }


@dataclass
class _ToolExecutionTrace:
    tool_call_step: AgentStep
    call: ToolCall
    result: ToolCallResult
    tool_result_step: AgentStep


class ContextBuilder:
    DEFAULT_BUDGETS = {
        "current_user_message": 1200,
        "session_summary": 1600,
        "recent_messages": 1600,
        "memories": 1200,
        "tool_results": 1000,
    }

    def build(
        self,
        user_message: str,
        memories: list[str],
        tool_results: list[ToolCallResult],
        recent_messages: list[tuple[str, str]] | None = None,
        session_summary: str | None = None,
    ) -> tuple[str, dict[str, object]]:
        blocks = [
            self._block("current_user_message", f"current_user_message: {user_message}", priority=100, trim=False),
            self._block("session_summary", self._section("session_summary", self._lines(session_summary)), priority=90),
            self._block(
                "recent_messages",
                self._section("recent_messages", [f"{role}: {content}" for role, content in (recent_messages or [])]),
                priority=70,
            ),
            self._block("memories", self._section("memories", memories), priority=60),
            self._block(
                "tool_results",
                self._section("tool_results", [result.content for result in tool_results]),
                priority=40,
            ),
        ]
        parts: list[str] = []
        trace_blocks: list[dict[str, object]] = []
        for block in blocks:
            raw_content = str(block["content"])
            if not raw_content:
                continue
            budget = self.DEFAULT_BUDGETS[str(block["name"])]
            final_content = raw_content
            dropped = False
            if len(raw_content) > budget and bool(block["trim"]):
                final_content = raw_content[:budget].rstrip()
            elif len(raw_content) > budget:
                dropped = True
                final_content = ""
            if final_content:
                parts.append(final_content)
            trace_blocks.append(
                {
                    "name": block["name"],
                    "priority": block["priority"],
                    "budget_chars": budget,
                    "original_chars": len(raw_content),
                    "final_chars": len(final_content),
                    "dropped": dropped,
                }
            )
        return "\n\n".join(parts), {"blocks": trace_blocks}

    def _section(self, title: str, lines: list[str]) -> str:
        if not lines:
            return ""
        return f"{title}:\n" + "\n".join(f"- {line}" for line in lines if line.strip())

    def _lines(self, text: str | None) -> list[str]:
        return [line for line in (text or "").splitlines() if line.strip()]

    def _block(self, name: str, content: str, priority: int, trim: bool = True) -> dict[str, object]:
        return {"name": name, "content": content, "priority": priority, "trim": trim}


class TraceRecorder:
    def __init__(self, db: AsyncSession, run: AgentRun) -> None:
        self.db = db
        self.run = run
        self.step_index = 0

    async def step(self, kind: str, content: str) -> AgentStep:
        step = AgentStep(
            run_id=self.run.id,
            step_index=self.step_index,
            kind=kind,
            content=content,
        )
        self.step_index += 1
        self.db.add(step)
        await self.db.flush()
        return step

    async def tool_call(self, result: ToolCallResult) -> ToolCall:
        call = ToolCall(
            run_id=self.run.id,
            name=result.name,
            arguments_json=json.dumps(result.arguments, ensure_ascii=False),
            result_json=json.dumps({"content": result.content}, ensure_ascii=False),
            is_error=result.is_error,
        )
        self.db.add(call)
        await self.db.flush()
        return call

    def event_for_step(self, step: AgentStep) -> StreamEvent:
        data: dict[str, object] = {"run_id": self.run.id, "step_index": step.step_index, "content": step.content}
        if step.kind == "run_started":
            data.update({"session_id": self.run.session_id, "message": step.content})
        elif step.kind in {
            "context_built",
            "model_request",
            "model_response",
            "model_tool_use",
            "model_final",
            "model_turn",
            "tool_call",
            "tool_result",
            "memory_retrieval_started",
            "memory_retrieved",
            "session_summary_checked",
            "session_summary_updated",
            "session_summary_used",
            "model_error",
            "token_usage",
            "latency_metric",
        }:
            data.update(self._json_content(step.content))
        elif step.kind == "run_finished":
            data.update({"text": step.content})
        return StreamEvent(event=step.kind, data=data)

    def event_for_tool_result(self, call: ToolCall, result: ToolCallResult) -> StreamEvent:
        return StreamEvent(
            event="tool_result",
            data={
                "run_id": self.run.id,
                "tool_call_id": call.id,
                "name": result.name,
                "arguments": result.arguments,
                "content": result.content,
                "is_error": result.is_error,
            },
        )

    def _json_content(self, content: str) -> dict[str, object]:
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            return parsed
        return {"value": parsed}


class AgentRunner:
    def __init__(
        self,
        db: AsyncSession,
        tools: ToolRegistry,
        model: ModelAdapter | None = None,
    ) -> None:
        self.db = db
        self.tools = tools
        self.model = model or self._build_default_model(tools)
        self.context_builder = ContextBuilder()
        self.max_loops = get_settings().max_agent_loops

    def _build_default_model(self, tools: ToolRegistry) -> ModelAdapter:
        return create_model_adapter(tools)

    def _model_request_payload(self, pending_tool_results: list[ToolCallResult]) -> dict[str, object]:
        return {
            "model_adapter": self.model.__class__.__name__,
            "model": getattr(self.model, "model", None),
            "message_count": 1 + (2 if pending_tool_results else 0),
            "tools_count": len(getattr(self.model, "tool_definitions", [])),
            "pending_tool_results": len(pending_tool_results),
            "compatibility_mode": bool(getattr(self.model, "compatibility_mode", False)),
            "token_parameter": getattr(self.model, "token_parameter", None),
        }

    def _memory_contents(self, memories: list[RetrievedMemory | str]) -> list[str]:
        return [memory.content if isinstance(memory, RetrievedMemory) else memory for memory in memories]

    def _memory_trace_payload(self, user_message: str, memories: list[RetrievedMemory | str]) -> dict[str, object]:
        matches = []
        for memory in memories:
            if isinstance(memory, RetrievedMemory):
                matches.append(
                    {
                        "memory_id": memory.id,
                        "content": memory.content,
                        "score": memory.score,
                        "matched_terms": memory.matched_terms,
                        "reason": memory.reason,
                        "scope": memory.scope,
                        "category": memory.category,
                        "source_kind": memory.source_kind,
                        "confidence": memory.confidence,
                    }
                )
            else:
                matches.append(
                    {
                        "memory_id": None,
                        "content": memory,
                        "score": None,
                        "matched_terms": [],
                        "reason": "legacy memory content",
                    }
                )
        return {
            "query": user_message,
            "terms": extract_query_terms(user_message),
            "count": len(memories),
            "matches": matches,
        }

    def _session_summary_text(self, session_summary: SummaryResult | None) -> str | None:
        if session_summary and session_summary.used:
            return session_summary.summary
        return None

    async def _initialize_run(
        self,
        session_id: str,
        user_message: str,
        memories: list[RetrievedMemory | str],
        recorder: TraceRecorder,
        session_summary: SummaryResult | None,
    ) -> list[AgentStep]:
        steps = [
            await recorder.step("run_started", user_message),
            await recorder.step("memory_retrieval_started", json.dumps({"query": user_message}, ensure_ascii=False)),
            await recorder.step(
            "memory_retrieved",
            json.dumps(self._memory_trace_payload(user_message, memories), ensure_ascii=False),
            ),
        ]
        if session_summary is not None:
            steps.extend(await self._record_session_summary_trace(recorder, session_id, session_summary))
        return steps

    def _build_context(
        self,
        user_message: str,
        memories: list[RetrievedMemory | str],
        recent_messages: list[tuple[str, str]] | None,
        session_summary: SummaryResult | None,
        state: _RunLoopState,
    ) -> tuple[str, dict[str, object]]:
        return self.context_builder.build(
            user_message,
            self._memory_contents(memories),
            state.all_tool_results,
            recent_messages,
            self._session_summary_text(session_summary),
        )

    async def _record_context(
        self,
        recorder: TraceRecorder,
        context: str,
        context_trace: dict[str, object],
    ) -> AgentStep:
        return await recorder.step(
            "context_built",
            json.dumps({"context": context, "context_trace": context_trace}, ensure_ascii=False),
        )

    async def _record_model_request(
        self,
        recorder: TraceRecorder,
        pending_tool_results: list[ToolCallResult],
    ) -> None:
        await recorder.step(
            "model_request",
            json.dumps(self._model_request_payload(pending_tool_results), ensure_ascii=False),
        )

    async def _next_turn(
        self,
        user_message: str,
        context: str,
        pending_tool_results: list[ToolCallResult],
    ) -> ModelTurn:
        return await self.model.next_turn(user_message, context, pending_tool_results)

    async def _record_model_turn(self, recorder: TraceRecorder, turn: ModelTurn) -> AgentStep:
        await recorder.step(
            "model_response",
            json.dumps(
                {
                    "kind": turn.kind,
                    "finish_reason": turn.finish_reason,
                    "tool_call_count": len(turn.tool_calls),
                    "truncated": turn.truncated,
                },
                ensure_ascii=False,
            ),
        )
        if turn.kind == "tool_call":
            for tool_call in turn.tool_calls:
                await recorder.step("model_tool_use", tool_call.model_dump_json())
        elif turn.kind == "final":
            await recorder.step(
                "model_final",
                json.dumps({"content": turn.content or "", "truncated": turn.truncated}, ensure_ascii=False),
            )
        step = await recorder.step("model_turn", turn.model_dump_json())
        if turn.usage:
            await recorder.step(
                "token_usage",
                json.dumps({"usage": turn.usage, "finish_reason": turn.finish_reason}, ensure_ascii=False),
            )
        return step

    async def _execute_tool_calls(
        self,
        recorder: TraceRecorder,
        turn: ModelTurn,
        state: _RunLoopState,
    ) -> list[_ToolExecutionTrace]:
        traces: list[_ToolExecutionTrace] = []
        current_tool_results: list[ToolCallResult] = []
        for tool_call in turn.tool_calls:
            tool_call_step = await recorder.step("tool_call", tool_call.model_dump_json())
            result = await self.tools.execute(tool_call.name, tool_call.arguments, tool_call.id)
            state.used_tools.append(result.name)
            state.all_tool_results.append(result)
            current_tool_results.append(result)
            call = await recorder.tool_call(result)
            tool_result_step = await recorder.step("tool_result", result.model_dump_json())
            traces.append(_ToolExecutionTrace(tool_call_step, call, result, tool_result_step))
        state.pending_tool_results = current_tool_results
        return traces

    async def _complete_run(
        self,
        run: AgentRun,
        recorder: TraceRecorder,
        status: RunStatus,
        final_answer: str,
    ) -> None:
        run.status = status
        run.final_answer = final_answer
        run.finished_at = utc_now()
        await recorder.step("run_finished", final_answer)
        await self.db.flush()

    async def _fail_run(
        self,
        run: AgentRun,
        recorder: TraceRecorder,
        message: str,
        used_tools: list[str],
    ) -> AgentRun:
        await recorder.step("model_error", json.dumps({"message": message}, ensure_ascii=False))
        await self._complete_run(run, recorder, RunStatus.failed, message)
        run.used_tools = used_tools  # type: ignore[attr-defined]
        return run

    async def _record_session_summary_trace(
        self,
        recorder: TraceRecorder,
        session_id: str,
        session_summary: SummaryResult,
    ) -> list[AgentStep]:
        steps = [
            await recorder.step(
                "session_summary_checked",
                json.dumps(session_summary.trace_payload(session_id), ensure_ascii=False),
            )
        ]
        if session_summary.updated:
            steps.append(
                await recorder.step(
                    "session_summary_updated",
                    json.dumps(session_summary.trace_payload(session_id), ensure_ascii=False),
                )
            )
        if session_summary.used and session_summary.summary:
            steps.append(
                await recorder.step(
                    "session_summary_used",
                    json.dumps(
                        {
                            **session_summary.trace_payload(session_id),
                            "summary": session_summary.summary,
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
        return steps

    async def run(
        self,
        session_id: str,
        user_message: str,
        memories: list[RetrievedMemory | str],
        recent_messages: list[tuple[str, str]] | None = None,
        session_summary: SummaryResult | None = None,
    ) -> AgentRun:
        run = AgentRun(session_id=session_id)
        self.db.add(run)
        await self.db.flush()
        recorder = TraceRecorder(self.db, run)
        await self._initialize_run(session_id, user_message, memories, recorder, session_summary)

        state = _RunLoopState()
        for _ in range(self.max_loops):
            context, context_trace = self._build_context(
                user_message,
                memories,
                recent_messages,
                session_summary,
                state,
            )
            await self._record_context(recorder, context, context_trace)
            await self._record_model_request(recorder, state.pending_tool_results)
            try:
                turn = await self._next_turn(user_message, context, state.pending_tool_results)
            except ModelAdapterError as exc:
                return await self._fail_run(run, recorder, str(exc), state.used_tools)
            await self._record_model_turn(recorder, turn)

            if turn.kind == "final":
                await self._complete_run(run, recorder, RunStatus.completed, turn.content or "")
                run.used_tools = state.used_tools  # type: ignore[attr-defined]
                return run

            if not turn.tool_calls:
                message = "Model requested a tool call without tool_call payload."
                return await self._fail_run(run, recorder, message, state.used_tools)

            await self._execute_tool_calls(recorder, turn, state)

        final_answer = "达到最大循环次数，Agent 已降级终止。"
        await self._complete_run(run, recorder, RunStatus.max_loops, final_answer)
        run.used_tools = state.used_tools  # type: ignore[attr-defined]
        return run

    async def stream(
        self,
        session_id: str,
        user_message: str,
        memories: list[RetrievedMemory | str],
        recent_messages: list[tuple[str, str]] | None = None,
        session_summary: SummaryResult | None = None,
    ) -> AsyncIterator[StreamEvent]:
        metrics = _StreamMetrics()
        run = AgentRun(session_id=session_id)
        self.db.add(run)
        await self.db.flush()
        recorder = TraceRecorder(self.db, run)
        for step in await self._initialize_run(session_id, user_message, memories, recorder, session_summary):
            yield recorder.event_for_step(step)

        state = _RunLoopState()
        for _ in range(self.max_loops):
            context, context_trace = self._build_context(
                user_message,
                memories,
                recent_messages,
                session_summary,
                state,
            )
            context_step = await self._record_context(recorder, context, context_trace)
            yield recorder.event_for_step(context_step)
            model_request = await recorder.step(
                "model_request",
                json.dumps(self._model_request_payload(state.pending_tool_results), ensure_ascii=False),
            )
            yield recorder.event_for_step(model_request)

            streamed_text = ""
            turn: ModelTurn | None = None
            model_stream_started_at = time.perf_counter()
            try:
                async for part in self.model.stream_turn(user_message, context, state.pending_tool_results):
                    if isinstance(part, str):
                        streamed_text += part
                        metrics.record_delta(part)
                        yield StreamEvent(event="message_delta", data={"run_id": run.id, "text": part})
                    else:
                        turn = part
            except ModelAdapterError as exc:
                metrics.record_model_duration(model_stream_started_at)
                async for event in self._fail_stream_run(
                    run,
                    recorder,
                    str(exc),
                    state.used_tools,
                    metrics.latency_payload(),
                ):
                    yield event
                return
            except Exception as exc:
                metrics.record_model_duration(model_stream_started_at)
                message = f"模型流式执行异常：{exc.__class__.__name__}: {exc}"
                async for event in self._fail_stream_run(
                    run,
                    recorder,
                    message,
                    state.used_tools,
                    metrics.latency_payload(),
                ):
                    yield event
                return
            metrics.record_model_duration(model_stream_started_at)

            if turn is None:
                message = "Model stream ended without a final turn."
                async for event in self._fail_stream_run(
                    run,
                    recorder,
                    message,
                    state.used_tools,
                    metrics.latency_payload(),
                ):
                    yield event
                return

            model_turn = await self._record_model_turn(recorder, turn)
            yield recorder.event_for_step(model_turn)

            if turn.kind == "final":
                final_answer = turn.content or streamed_text
                emit_delta = not streamed_text
                if emit_delta:
                    metrics.record_output(final_answer)
                async for event in self._finish_stream_run(
                    run,
                    recorder,
                    RunStatus.completed,
                    final_answer,
                    emit_delta,
                    metrics.latency_payload(),
                ):
                    yield event
                run.used_tools = state.used_tools  # type: ignore[attr-defined]
                return

            if not turn.tool_calls:
                message = "Model requested a tool call without tool_call payload."
                async for event in self._fail_stream_run(
                    run,
                    recorder,
                    message,
                    state.used_tools,
                    metrics.latency_payload(),
                ):
                    yield event
                return

            for trace in await self._execute_tool_calls(recorder, turn, state):
                yield recorder.event_for_step(trace.tool_call_step)
                yield recorder.event_for_tool_result(trace.call, trace.result)
                yield recorder.event_for_step(trace.tool_result_step)

        final_answer = "达到最大循环次数，Agent 已降级终止。"
        metrics.record_output(final_answer)
        async for event in self._finish_stream_run(
            run,
            recorder,
            RunStatus.max_loops,
            final_answer,
            latency_metric=metrics.latency_payload(),
        ):
            yield event
        run.used_tools = state.used_tools  # type: ignore[attr-defined]

    async def _fail_stream_run(
        self,
        run: AgentRun,
        recorder: TraceRecorder,
        message: str,
        used_tools: list[str],
        latency_metric: dict[str, object] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        model_error = await recorder.step("model_error", json.dumps({"message": message}, ensure_ascii=False))
        yield recorder.event_for_step(model_error)
        async for event in self._finish_stream_run(
            run,
            recorder,
            RunStatus.failed,
            message,
            latency_metric=latency_metric,
        ):
            yield event
        run.used_tools = used_tools  # type: ignore[attr-defined]

    async def _finish_stream_run(
        self,
        run: AgentRun,
        recorder: TraceRecorder,
        status: RunStatus,
        final_answer: str,
        emit_message_delta: bool = True,
        latency_metric: dict[str, object] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        if latency_metric is not None:
            latency_step = await recorder.step("latency_metric", json.dumps(latency_metric, ensure_ascii=False))
            yield recorder.event_for_step(latency_step)
        run.status = status
        run.final_answer = final_answer
        run.finished_at = utc_now()
        run_finished = await recorder.step("run_finished", final_answer)
        await self.db.flush()
        if emit_message_delta:
            yield StreamEvent(event="message_delta", data={"run_id": run.id, "text": final_answer})
        yield StreamEvent(
            event="run_finished",
            data={**recorder.event_for_step(run_finished).data, "run_id": run.id, "status": status.value},
        )

