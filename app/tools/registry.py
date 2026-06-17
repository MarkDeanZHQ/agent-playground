from dataclasses import dataclass, field
from typing import Any, Protocol

from app.schemas.api import ToolCallResult


class ToolHandler(Protocol):
    async def __call__(self, arguments: dict[str, Any]) -> str:
        ...


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler
    examples: list[dict[str, Any]] = field(default_factory=list)
    learning_notes: list[str] = field(default_factory=list)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        self._tools[tool.name] = tool

    def list_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
                "examples": tool.examples,
                "learning_notes": tool.learning_notes,
            }
            for tool in self._tools.values()
        ]

    def get_definition(self, name: str) -> dict[str, Any] | None:
        tool = self._tools.get(name)
        if tool is None:
            return None
        return {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_schema,
            "examples": tool.examples,
            "learning_notes": tool.learning_notes,
        }

    async def execute(self, name: str, arguments: dict[str, Any], tool_call_id: str | None = None) -> ToolCallResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolCallResult(
                id=tool_call_id,
                name=name,
                arguments=arguments,
                content=f"Unknown tool: {name}",
                is_error=True,
            )

        try:
            content = await tool.handler(arguments)
            return ToolCallResult(id=tool_call_id, name=name, arguments=arguments, content=content)
        except Exception as exc:  # noqa: BLE001 - tool errors are observable agent results
            return ToolCallResult(
                id=tool_call_id,
                name=name,
                arguments=arguments,
                content=f"Tool error: {exc}",
                is_error=True,
            )
