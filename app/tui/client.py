from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(slots=True)
class SseEvent:
    event: str
    data: dict[str, Any]


class AgentPlaygroundClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8000") -> None:
        self.base_url = base_url.rstrip("/")

    async def health(self) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            response = await client.get("/health")
            response.raise_for_status()
            return response.json()

    async def model_health(self, live: bool = False) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=None) as client:
            response = await client.get("/api/v1/models/health", params={"live": live})
            response.raise_for_status()
            return response.json()

    async def chat(
        self,
        message: str,
        session_id: str | None = None,
        timeout: float | None = 90.0,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=timeout) as client:
            response = await client.post("/api/v1/chat", json={"message": message, "session_id": session_id})
            response.raise_for_status()
            return response.json()

    async def list_tools(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            response = await client.get("/api/v1/tools")
            response.raise_for_status()
            return response.json()

    async def invoke_tool(self, name: str, arguments_json: str) -> dict[str, Any]:
        arguments = json.loads(arguments_json or "{}")
        if not isinstance(arguments, dict):
            raise ValueError("tool arguments must be a JSON object")
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            response = await client.post(f"/api/v1/tools/{name}/invoke", json={"arguments": arguments})
            response.raise_for_status()
            return response.json()

    async def list_memories(
        self,
        query: str | None = None,
        status: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        params = {"limit": limit}
        if query:
            params["query"] = query
        if status:
            params["status"] = status
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            response = await client.get("/api/v1/memories", params=params)
            response.raise_for_status()
            return response.json()


    async def create_memory(
        self,
        content: str,
        importance: int = 2,
        memory_type: str = "preference",
    ) -> dict[str, Any]:
        payload = {"content": content, "importance": importance, "memory_type": memory_type}
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            response = await client.post("/api/v1/memories", json=payload)
            response.raise_for_status()
            return response.json()

    async def update_memory(
        self,
        memory_id: str,
        content: str | None = None,
        importance: int | None = None,
        memory_type: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            key: value
            for key, value in {"content": content, "importance": importance, "memory_type": memory_type}.items()
            if value is not None
        }
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            response = await client.patch(f"/api/v1/memories/{memory_id}", json=payload)
            response.raise_for_status()
            return response.json()

    async def archive_memory(self, memory_id: str) -> dict[str, Any]:
        return await self._memory_action(memory_id, "archive")

    async def soft_delete_memory(self, memory_id: str) -> dict[str, Any]:
        return await self._memory_action(memory_id, "delete")

    async def restore_memory(self, memory_id: str) -> dict[str, Any]:
        return await self._memory_action(memory_id, "restore")

    async def _memory_action(self, memory_id: str, action: str) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            response = await client.post(f"/api/v1/memories/{memory_id}/{action}")
            response.raise_for_status()
            return response.json()

    async def list_runs(self, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            response = await client.get("/api/v1/runs", params={"limit": limit, "offset": offset})
            response.raise_for_status()
            return response.json()

    async def get_run(self, run_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            response = await client.get(f"/api/v1/runs/{run_id}")
            response.raise_for_status()
            return response.json()

    async def stream_chat(self, message: str, session_id: str | None = None) -> AsyncIterator[SseEvent]:
        payload = {"message": message, "session_id": session_id}
        async with httpx.AsyncClient(base_url=self.base_url, timeout=None) as client:
            async with client.stream("POST", "/api/v1/chat/stream", json=payload) as response:
                response.raise_for_status()
                async for event in self._iter_sse(response):
                    yield event

    async def _iter_sse(self, response: httpx.Response) -> AsyncIterator[SseEvent]:
        event_name = "message"
        data_lines: list[str] = []
        try:
            async for line in response.aiter_lines():
                if line == "":
                    if data_lines:
                        yield SseEvent(event=event_name, data=json.loads("\n".join(data_lines)))
                    event_name = "message"
                    data_lines = []
                    continue
                if line.startswith("event: "):
                    event_name = line.removeprefix("event: ")
                elif line.startswith("data: "):
                    data_lines.append(line.removeprefix("data: "))

            if data_lines:
                yield SseEvent(event=event_name, data=json.loads("\n".join(data_lines)))
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            yield SseEvent(
                event="stream_error",
                data={
                    "message": f"流式响应中断：{exc.__class__.__name__}",
                    "detail": str(exc),
                },
            )
