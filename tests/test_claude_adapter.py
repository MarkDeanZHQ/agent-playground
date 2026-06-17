from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.models.adapters import ClaudeModelAdapter, OpenAIModelAdapter
from app.schemas.api import ToolCallResult
from app.tools.builtin import build_default_registry


class FakeContentBlock:
    def __init__(self, block_type: str, **kwargs):
        self.type = block_type
        for key, value in kwargs.items():
            setattr(self, key, value)


@pytest.mark.asyncio
async def test_claude_model_adapter_converts_text_response_to_final_turn():
    registry = build_default_registry()
    with patch("app.models.adapters.AsyncAnthropic") as client_cls:
        response = Mock(stop_reason="end_turn", content=[FakeContentBlock("text", text="真实模型回答")])
        client_cls.return_value.messages.create = AsyncMock(return_value=response)
        adapter = ClaudeModelAdapter(registry)

        turn = await adapter.next_turn("你好", "context", [])

    assert turn.kind == "final"
    assert turn.content == "真实模型回答"
    client_cls.return_value.messages.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_claude_model_adapter_marks_max_tokens_as_truncated():
    registry = build_default_registry()
    with patch("app.models.adapters.AsyncAnthropic") as client_cls:
        response = Mock(stop_reason="max_tokens", content=[FakeContentBlock("text", text="部分回答")], usage=None)
        client_cls.return_value.messages.create = AsyncMock(return_value=response)
        adapter = ClaudeModelAdapter(registry)

        turn = await adapter.next_turn("你好", "context", [])

    assert turn.kind == "final"
    assert turn.truncated is True
    assert "可能被截断" in (turn.content or "")


@pytest.mark.asyncio
async def test_claude_model_adapter_converts_tool_use_response_to_tool_call():
    registry = build_default_registry()
    with patch("app.models.adapters.AsyncAnthropic") as client_cls:
        response = Mock(
            stop_reason="tool_use",
            content=[FakeContentBlock("tool_use", id="toolu_1", name="text_stats", input={"text": "hello"})],
        )
        client_cls.return_value.messages.create = AsyncMock(return_value=response)
        adapter = ClaudeModelAdapter(registry)

        turn = await adapter.next_turn("请统计 hello", "context", [])

    assert turn.kind == "tool_call"
    assert turn.tool_call is not None
    assert turn.tool_call.id == "toolu_1"
    assert turn.tool_call.name == "text_stats"
    assert turn.tool_call.arguments == {"text": "hello"}
    assert turn.tool_calls == [turn.tool_call]


@pytest.mark.asyncio
async def test_claude_model_adapter_sends_tool_results_as_final_instruction():
    registry = build_default_registry()
    with patch("app.models.adapters.AsyncAnthropic") as client_cls:
        response = Mock(stop_reason="end_turn", content=[FakeContentBlock("text", text="完成")])
        client_cls.return_value.messages.create = AsyncMock(return_value=response)
        adapter = ClaudeModelAdapter(registry)
        adapter.pending_assistant_content = [
            {"type": "tool_use", "id": "toolu_1", "name": "text_stats", "input": {"text": "hello"}}
        ]

        await adapter.next_turn(
            "请统计 hello",
            "context",
            [
                ToolCallResult(
                    id="toolu_1",
                    name="text_stats",
                    arguments={"text": "hello"},
                    content="characters=5, lines=1, words=1",
                )
            ],
        )

    kwargs = client_cls.return_value.messages.create.await_args.kwargs
    assert kwargs["messages"][1] == {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": "toolu_1", "name": "text_stats", "input": {"text": "hello"}}],
    }
    assert kwargs["messages"][2]["content"][0]["tool_use_id"] == "toolu_1"
    assert kwargs["model"] == "claude-opus-4-8"
    assert kwargs["thinking"] == {"type": "adaptive"}


@pytest.mark.asyncio
async def test_claude_model_adapter_records_multi_tool_use_blocks():
    registry = build_default_registry()
    with patch("app.models.adapters.AsyncAnthropic") as client_cls:
        response = Mock(
            stop_reason="tool_use",
            content=[
                FakeContentBlock("text", text="我会分别调用两个工具。"),
                FakeContentBlock("tool_use", id="toolu_1", name="text_stats", input={"text": "hello"}),
                FakeContentBlock("tool_use", id="toolu_2", name="note_search", input={"query": "demo"}),
            ],
        )
        client_cls.return_value.messages.create = AsyncMock(return_value=response)
        adapter = ClaudeModelAdapter(registry)

        turn = await adapter.next_turn("请统计并搜索", "context", [])

    assert turn.kind == "tool_call"
    assert [tool_call.id for tool_call in turn.tool_calls] == ["toolu_1", "toolu_2"]
    assert adapter.pending_assistant_content is not None
    assert [block["type"] for block in adapter.pending_assistant_content] == ["text", "tool_use", "tool_use"]


@pytest.mark.asyncio
async def test_claude_model_adapter_forces_adaptive_thinking_for_fable(monkeypatch):
    monkeypatch.setenv("AGENT_PLAYGROUND_CLAUDE_MODEL", "claude-fable-5")
    monkeypatch.setenv("AGENT_PLAYGROUND_CLAUDE_THINKING", "disabled")
    from app.core.config import get_settings

    get_settings.cache_clear()
    registry = build_default_registry()
    with patch("app.models.adapters.AsyncAnthropic") as client_cls:
        response = Mock(stop_reason="end_turn", content=[FakeContentBlock("text", text="ok")])
        client_cls.return_value.messages.create = AsyncMock(return_value=response)
        adapter = ClaudeModelAdapter(registry)

        await adapter.next_turn("你好", "context", [])

    kwargs = client_cls.return_value.messages.create.await_args.kwargs
    assert kwargs["model"] == "claude-fable-5"
    assert kwargs["thinking"] == {"type": "adaptive"}
    monkeypatch.delenv("AGENT_PLAYGROUND_CLAUDE_MODEL")
    monkeypatch.delenv("AGENT_PLAYGROUND_CLAUDE_THINKING")
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_claude_model_adapter_streams_text_deltas_before_final_turn():
    class FakeStream:
        def __init__(self, response):
            self.response = response
            self.text_stream = self._text_stream()

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def _text_stream(self):
            yield "真实"
            yield "流式"

        async def get_final_message(self):
            return self.response

    registry = build_default_registry()
    with patch("app.models.adapters.AsyncAnthropic") as client_cls:
        response = Mock(stop_reason="end_turn", content=[FakeContentBlock("text", text="真实流式")])
        client_cls.return_value.messages.stream = Mock(return_value=FakeStream(response))
        adapter = ClaudeModelAdapter(registry)

        parts = [part async for part in adapter.stream_turn("你好", "context", [])]

    assert parts[:2] == ["真实", "流式"]
    assert parts[2].kind == "final"
    assert parts[2].content == "真实流式"


@pytest.mark.asyncio
async def test_openai_model_adapter_converts_text_response_to_final_turn():
    registry = build_default_registry()
    with patch("app.models.adapters.AsyncOpenAI") as client_cls:
        message = Mock(content="OpenAI 回答", tool_calls=None)
        response = Mock(choices=[Mock(message=message)])
        client_cls.return_value.chat.completions.create = AsyncMock(return_value=response)
        adapter = OpenAIModelAdapter(registry)

        turn = await adapter.next_turn("你好", "context", [])

    assert turn.kind == "final"
    assert turn.content == "OpenAI 回答"
    client_cls.return_value.chat.completions.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_openai_model_adapter_converts_tool_call_response_to_tool_call():
    registry = build_default_registry()
    with patch("app.models.adapters.AsyncOpenAI") as client_cls:
        function = SimpleNamespace(name="text_stats", arguments='{"text":"hello"}')
        tool_call = SimpleNamespace(id="call_1", function=function)
        message = Mock(content=None, tool_calls=[tool_call])
        response = Mock(choices=[Mock(message=message)])
        client_cls.return_value.chat.completions.create = AsyncMock(return_value=response)
        adapter = OpenAIModelAdapter(registry)

        turn = await adapter.next_turn("请统计 hello", "context", [])

    assert turn.kind == "tool_call"
    assert turn.tool_call is not None
    assert turn.tool_call.id == "call_1"
    assert turn.tool_call.name == "text_stats"
    assert turn.tool_call.arguments == {"text": "hello"}
    assert turn.tool_calls == [turn.tool_call]


@pytest.mark.asyncio
async def test_openai_model_adapter_handles_invalid_tool_arguments_as_observable_parse_error():
    registry = build_default_registry()
    with patch("app.models.adapters.AsyncOpenAI") as client_cls:
        function = SimpleNamespace(name="text_stats", arguments="{invalid")
        tool_call = SimpleNamespace(id="call_1", function=function)
        message = Mock(content=None, tool_calls=[tool_call])
        response = Mock(choices=[Mock(message=message, finish_reason="tool_calls")], usage=None)
        client_cls.return_value.chat.completions.create = AsyncMock(return_value=response)
        adapter = OpenAIModelAdapter(registry)

        turn = await adapter.next_turn("请统计 hello", "context", [])

    assert turn.kind == "tool_call"
    assert turn.tool_call is not None
    assert turn.tool_call.arguments["_parse_error"] == "invalid_json"
    assert turn.tool_call.arguments["_raw_arguments"] == "{invalid"


@pytest.mark.asyncio
async def test_openai_model_adapter_sends_function_tools_and_tool_results(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("AGENT_PLAYGROUND_OPENAI_MODEL", "gpt-4.1")
    monkeypatch.setenv("AGENT_PLAYGROUND_OPENAI_COMPATIBILITY_MODE", "off")
    monkeypatch.setenv("AGENT_PLAYGROUND_OPENAI_TOKEN_PARAMETER", "max_completion_tokens")
    get_settings.cache_clear()
    registry = build_default_registry()
    try:
        with patch("app.models.adapters.AsyncOpenAI") as client_cls:
            message = Mock(content="完成", tool_calls=None)
            response = Mock(choices=[Mock(message=message)])
            client_cls.return_value.chat.completions.create = AsyncMock(return_value=response)
            adapter = OpenAIModelAdapter(registry)
            adapter.pending_assistant_message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "text_stats", "arguments": '{"text":"hello"}'},
                    }
                ],
            }

            await adapter.next_turn(
                "请统计 hello",
                "context",
                [
                    ToolCallResult(
                        id="call_1",
                        name="text_stats",
                        arguments={"text": "hello"},
                        content="characters=5, lines=1, words=1",
                    )
                ],
            )

        kwargs = client_cls.return_value.chat.completions.create.await_args.kwargs
        assert kwargs["model"] == "gpt-4.1"
        assert kwargs["max_completion_tokens"] == 16000
        assert kwargs["tools"][0]["type"] == "function"
        assert kwargs["messages"][2]["tool_calls"][0]["id"] == "call_1"
        assert kwargs["messages"][3] == {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": "characters=5, lines=1, words=1",
        }
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_openai_model_adapter_auto_uses_compatibility_mode_for_custom_base_url(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("AGENT_PLAYGROUND_OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("AGENT_PLAYGROUND_OPENAI_TOOL_CALLING", "true")
    monkeypatch.setenv("AGENT_PLAYGROUND_OPENAI_TOKEN_PARAMETER", "max_completion_tokens")
    get_settings.cache_clear()
    try:
        with patch("app.models.adapters.AsyncOpenAI") as client_cls:
            adapter = OpenAIModelAdapter(build_default_registry())

        assert adapter.compatibility_mode is True
        assert adapter.protocol_mode == "auto"
        assert adapter.token_parameter == "max_tokens"
        assert {tool["function"]["name"] for tool in adapter.tool_definitions} >= {"text_stats", "note_search"}
        headers = client_cls.call_args.kwargs["default_headers"]
        assert headers["User-Agent"].startswith("Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_openai_model_adapter_compat_stream_uses_raw_chunks(monkeypatch):
    from app.core.config import get_settings

    class FakeStream:
        def __init__(self) -> None:
            self.kwargs = None

        async def create(self, **kwargs):
            self.kwargs = kwargs

            async def chunks() -> AsyncIterator[object]:
                yield SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content="你"), finish_reason=None)], usage=None
                )
                yield SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content="好"), finish_reason="stop")], usage=None
                )

            return chunks()

    fake_stream = FakeStream()
    monkeypatch.setenv("AGENT_PLAYGROUND_OPENAI_BASE_URL", "https://example.test/v1")
    get_settings.cache_clear()
    try:
        with patch("app.models.adapters.AsyncOpenAI") as client_cls:
            client_cls.return_value.chat.completions = fake_stream
            adapter = OpenAIModelAdapter(build_default_registry())

            parts = [part async for part in adapter.stream_turn("你好", "context", [])]

        assert parts[:2] == ["你", "好"]
        assert parts[2].kind == "final"
        assert parts[2].content == "你好"
        assert fake_stream.kwargs["stream"] is True
        assert fake_stream.kwargs["max_tokens"] == 16000
        assert fake_stream.kwargs["tools"][0]["function"]["name"] == "text_stats"
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_openai_model_adapter_can_disable_tool_calling_independently(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("AGENT_PLAYGROUND_OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("AGENT_PLAYGROUND_OPENAI_PROTOCOL_MODE", "off")
    monkeypatch.setenv("AGENT_PLAYGROUND_OPENAI_TOOL_CALLING", "false")
    get_settings.cache_clear()
    try:
        with patch("app.models.adapters.AsyncOpenAI"):
            adapter = OpenAIModelAdapter(build_default_registry())

        assert adapter.compatibility_mode is False
        assert adapter.tool_calling_enabled is False
        assert adapter.tool_definitions == []
    finally:
        get_settings.cache_clear()
