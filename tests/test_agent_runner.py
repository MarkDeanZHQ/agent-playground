import pytest
from sqlalchemy import select

from app.agent.runner import AgentRunner
from app.db.models import AgentStep, ToolCall
from app.db.session import AsyncSessionLocal
from app.tools.builtin import build_default_registry


@pytest.mark.asyncio
async def test_agent_loop_without_tool():
    async with AsyncSessionLocal() as db:
        runner = AgentRunner(db, build_default_registry())

        run = await runner.run("ses_test", "你好", [])

        assert str(run.status) == "RunStatus.completed"
        assert run.final_answer is not None
        assert getattr(run, "used_tools", []) == []


@pytest.mark.asyncio
async def test_agent_loop_with_tool_call():
    async with AsyncSessionLocal() as db:
        runner = AgentRunner(db, build_default_registry())

        run = await runner.run("ses_test", "请统计 hello world", [])

        assert str(run.status) == "RunStatus.completed"
        assert "text_stats" in getattr(run, "used_tools", [])
        assert "characters" in (run.final_answer or "")


@pytest.mark.asyncio
async def test_agent_loop_can_trigger_json_extract_tool():
    async with AsyncSessionLocal() as db:
        runner = AgentRunner(db, build_default_registry())

        run = await runner.run(
            "ses_test",
            "请提取 name/email/city 字段：name: Alice\nemail: alice@example.com\ncity: Shanghai",
            [],
        )

        assert str(run.status) == "RunStatus.completed"
        assert "json_extract" in getattr(run, "used_tools", [])
        assert "Alice" in (run.final_answer or "")


@pytest.mark.asyncio
async def test_agent_loop_can_trigger_todo_side_effect_tools():
    async with AsyncSessionLocal() as db:
        runner = AgentRunner(db, build_default_registry())

        create_run = await runner.run("ses_test", "请创建待办：复盘 todo tool", [])
        list_run = await runner.run("ses_test", "请列出待办列表", [])

        tool_call_result = await db.execute(select(ToolCall).where(ToolCall.run_id == create_run.id))
        tool_calls = list(tool_call_result.scalars())
        assert str(create_run.status) == "RunStatus.completed"
        assert "todo_create" in getattr(create_run, "used_tools", [])
        assert tool_calls[0].name == "todo_create"
        assert str(list_run.status) == "RunStatus.completed"
        assert "todo_list" in getattr(list_run, "used_tools", [])
        assert "复盘 todo tool" in (list_run.final_answer or "")


@pytest.mark.asyncio
async def test_agent_loop_supports_two_round_tool_calls():
    from app.schemas.api import ModelTurn, ToolCallRequest

    class TwoRoundToolModel:
        def __init__(self):
            self.turn_count = 0

        async def next_turn(self, user_message, context, tool_results):
            self.turn_count += 1
            if self.turn_count == 1:
                return ModelTurn(
                    kind="tool_call",
                    tool_calls=[ToolCallRequest(name="text_stats", arguments={"text": "hello world"})],
                    finish_reason="test_tool_call_1",
                )
            if self.turn_count == 2:
                return ModelTurn(
                    kind="tool_call",
                    tool_calls=[ToolCallRequest(name="note_search", arguments={"query": "demo"})],
                    finish_reason="test_tool_call_2",
                )
            return ModelTurn(kind="final", content="two tools done", finish_reason="stop")

    async with AsyncSessionLocal() as db:
        runner = AgentRunner(db, build_default_registry(), model=TwoRoundToolModel())

        run = await runner.run("ses_test", "请连续使用两个工具", [])

        tool_call_result = await db.execute(select(ToolCall).where(ToolCall.run_id == run.id))
        step_result = await db.execute(select(AgentStep.kind).where(AgentStep.run_id == run.id))

        step_kinds = list(step_result.scalars())
        used_tools = getattr(run, "used_tools", [])
        assert str(run.status) == "RunStatus.completed"
        assert run.final_answer == "two tools done"
        assert used_tools == ["text_stats", "note_search"]
        assert len(list(tool_call_result.scalars())) == 2
        assert step_kinds.count("tool_call") == 2
        assert step_kinds.count("tool_result") == 2


@pytest.mark.asyncio
async def test_agent_loop_records_token_usage_step_for_model_usage():
    from app.schemas.api import ModelTurn

    class UsageModel:
        async def next_turn(self, user_message, context, tool_results):
            return ModelTurn(kind="final", content="ok", finish_reason="stop", usage={"total_tokens": 3})

    async with AsyncSessionLocal() as db:
        runner = AgentRunner(db, build_default_registry(), model=UsageModel())

        run = await runner.run("ses_test", "你好", [])

        result = await db.execute(select(AgentStep.kind).where(AgentStep.run_id == run.id))

        assert str(run.status) == "RunStatus.completed"
        assert "token_usage" in set(result.scalars())


@pytest.mark.asyncio
async def test_agent_loop_converts_model_errors_to_failed_run():
    from app.models.adapters import ModelAdapterError

    class FailingModel:
        async def next_turn(self, user_message, context, tool_results):
            raise ModelAdapterError("provider unavailable")

    async with AsyncSessionLocal() as db:
        runner = AgentRunner(db, build_default_registry(), model=FailingModel())

        run = await runner.run("ses_test", "你好", [])

        result = await db.execute(select(AgentStep.kind).where(AgentStep.run_id == run.id))

        assert str(run.status) == "RunStatus.failed"
        assert run.final_answer == "provider unavailable"
        assert "model_error" in set(result.scalars())


@pytest.mark.asyncio
async def test_stream_converts_unexpected_model_errors_to_failed_events():
    class ExplodingStreamModel:
        async def stream_turn(self, user_message, context, tool_results):
            if False:
                yield ""
            raise RuntimeError("upstream closed")

    async with AsyncSessionLocal() as db:
        runner = AgentRunner(db, build_default_registry(), model=ExplodingStreamModel())

        events = [event async for event in runner.stream("ses_test", "你好", [])]

        assert any(event.event == "model_error" for event in events)
        assert events[-1].event == "run_finished"
        assert events[-1].data["status"] == "failed"
        assert "upstream closed" in str(events[-1].data["text"])


@pytest.mark.asyncio
async def test_stream_records_latency_metric_step():
    from app.schemas.api import ModelTurn

    class StreamingModel:
        async def stream_turn(self, user_message, context, tool_results):
            yield "ok"
            yield ModelTurn(kind="final", content="ok", finish_reason="stop")

    async with AsyncSessionLocal() as db:
        runner = AgentRunner(db, build_default_registry(), model=StreamingModel())

        events = [event async for event in runner.stream("ses_test", "你好", [])]
        run_id = events[0].data["run_id"]
        result = await db.execute(
            select(AgentStep).where(AgentStep.run_id == run_id, AgentStep.kind == "latency_metric")
        )
        latency_step = result.scalar_one()

        assert any(event.event == "latency_metric" for event in events)
        assert "time_to_first_token_ms" in latency_step.content
        assert "total_run_duration_ms" in latency_step.content
