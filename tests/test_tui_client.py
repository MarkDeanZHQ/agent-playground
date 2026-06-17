import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.tui.client import AgentPlaygroundClient, SseEvent


@pytest.mark.asyncio
async def test_tui_client_parses_sse_events():
    class FakeResponse:
        async def aiter_lines(self):
            lines = [
                "event: run_started",
                'data: {"run_id":"run_1"}',
                "",
                "event: message_delta",
                'data: {"text":"ok"}',
                "",
            ]
            for line in lines:
                yield line

    client = AgentPlaygroundClient()

    events = [event async for event in client._iter_sse(FakeResponse())]

    assert events == [
        SseEvent(event="run_started", data={"run_id": "run_1"}),
        SseEvent(event="message_delta", data={"text": "ok"}),
    ]


@pytest.mark.asyncio
async def test_tui_client_converts_stream_errors_to_sse_event():
    class FakeResponse:
        async def aiter_lines(self):
            yield "event: run_started"
            yield 'data: {"run_id":"run_1"}'
            yield ""
            raise httpx.RemoteProtocolError("incomplete chunked read")

    client = AgentPlaygroundClient()

    events = [event async for event in client._iter_sse(FakeResponse())]

    assert events[0] == SseEvent(event="run_started", data={"run_id": "run_1"})
    assert events[1].event == "stream_error"
    assert events[1].data["message"] == "流式响应中断：RemoteProtocolError"
    assert events[1].data["detail"] == "incomplete chunked read"


@pytest.mark.asyncio
async def test_tui_client_lists_runs_with_asgi_transport(monkeypatch):
    transport = ASGITransport(app=app)

    def make_client(*args, **kwargs):
        kwargs["transport"] = transport
        kwargs["base_url"] = "http://test"
        return AsyncClient(*args, **kwargs)

    monkeypatch.setattr("app.tui.client.httpx.AsyncClient", make_client)
    client = AgentPlaygroundClient("http://test")

    runs = await client.list_runs()

    assert isinstance(runs, list)
    filtered_runs = await client.list_runs(status="failed", tool_name="text_stats")
    assert isinstance(filtered_runs, list)


@pytest.mark.asyncio
async def test_tui_client_tools_and_memory_methods_use_api(monkeypatch):
    captured_timeouts: list[float | None] = []
    transport = ASGITransport(app=app)

    def make_client(*args, **kwargs):
        captured_timeouts.append(kwargs.get("timeout"))
        kwargs["transport"] = transport
        kwargs["base_url"] = "http://test"
        return AsyncClient(*args, **kwargs)

    monkeypatch.setattr("app.tui.client.httpx.AsyncClient", make_client)
    client = AgentPlaygroundClient("http://test")

    tools = await client.list_tools()
    model_health = await client.model_health()
    dashboard_stats = await client.dashboard_run_stats()
    result = await client.invoke_tool("text_stats", '{"text":"hello"}')
    chat = await client.chat("请记住：我偏好 FastAPI 示例")
    memories = await client.list_memories(query="FastAPI", status="active")
    created = await client.create_memory("TUI client 手动记忆")
    updated = await client.update_memory(created["id"], content="TUI client 更新记忆")
    archived = await client.archive_memory(created["id"])
    deleted = await client.soft_delete_memory(created["id"])
    restored = await client.restore_memory(created["id"])

    assert {tool["name"] for tool in tools} >= {"text_stats", "note_search"}
    assert model_health["provider"] == "fake"
    assert model_health["status"] == "ok"
    assert "sample_size" in dashboard_stats
    assert result["content"] == "characters=5, lines=1, words=1"
    assert chat["run_id"]
    assert any("FastAPI" in memory["content"] for memory in memories)
    assert created["content"] == "TUI client 手动记忆"
    assert updated["content"] == "TUI client 更新记忆"
    assert archived["status"] == "archived"
    assert deleted["status"] == "deleted"
    assert restored["status"] == "active"
    assert 90.0 in captured_timeouts


@pytest.mark.asyncio
async def test_tui_client_chat_accepts_custom_timeout(monkeypatch):
    captured_timeouts: list[float | None] = []
    transport = ASGITransport(app=app)

    def make_client(*args, **kwargs):
        captured_timeouts.append(kwargs.get("timeout"))
        kwargs["transport"] = transport
        kwargs["base_url"] = "http://test"
        return AsyncClient(*args, **kwargs)

    monkeypatch.setattr("app.tui.client.httpx.AsyncClient", make_client)
    client = AgentPlaygroundClient("http://test")

    response = await client.chat("请统计 hello world", timeout=12.5)

    assert response["run_id"]
    assert captured_timeouts[-1] == 12.5


@pytest.mark.asyncio
async def test_tui_client_rejects_non_object_tool_arguments():
    client = AgentPlaygroundClient("http://test")

    with pytest.raises(ValueError, match="tool arguments must be a JSON object"):
        await client.invoke_tool("text_stats", '["not", "object"]')
