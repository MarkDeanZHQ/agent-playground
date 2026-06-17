import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.tools.registry import ToolDefinition, ToolRegistry


def _require_string(
    arguments: dict[str, Any],
    field_name: str,
    *,
    min_length: int = 1,
    strip_result: bool = True,
) -> str:
    value = arguments.get(field_name)
    if value is None:
        raise ValueError(f"{field_name} is required")
    raw_text = str(value)
    normalized = raw_text.strip()
    if len(normalized) < min_length:
        raise ValueError(f"{field_name} is required")
    return normalized if strip_result else raw_text


def _sandbox_root() -> Path:
    return Path(get_settings().sandbox_dir).parent


def _todo_file() -> Path:
    root = _sandbox_root()
    root.mkdir(parents=True, exist_ok=True)
    return root / "todos.json"


async def text_stats(arguments: dict[str, Any]) -> str:
    text = _require_string(arguments, "text", strip_result=False)
    lines = text.splitlines()
    words = text.split()
    return f"characters={len(text)}, lines={len(lines)}, words={len(words)}"


async def note_search(arguments: dict[str, Any]) -> str:
    query = _require_string(arguments, "query").lower()

    root = Path(get_settings().sandbox_dir)
    root.mkdir(parents=True, exist_ok=True)
    matches: list[str] = []
    for path in root.glob("*.md"):
        content = path.read_text(encoding="utf-8")
        if query in content.lower() or query in path.name.lower():
            matches.append(f"{path.name}: {content[:300]}")
    return "\n---\n".join(matches) if matches else "No matching notes found."


async def json_extract(arguments: dict[str, Any]) -> str:
    text = _require_string(arguments, "text")
    fields = arguments.get("fields")
    if not isinstance(fields, list) or not fields:
        raise ValueError("fields is required")

    extracted: dict[str, str | None] = {}
    source_lines = [line.strip() for line in text.splitlines() if line.strip()]
    lowered_lines = [line.lower() for line in source_lines]
    for raw_field in fields:
        field = str(raw_field).strip()
        if not field:
            raise ValueError("fields must contain non-empty strings")
        target = field.lower()
        value: str | None = None
        for original, lowered in zip(source_lines, lowered_lines, strict=False):
            if lowered.startswith(f"{target}:"):
                value = original.split(":", 1)[1].strip() or None
                break
            if lowered.startswith(f"{target}："):
                value = original.split("：", 1)[1].strip() or None
                break
        extracted[field] = value
    return json.dumps(extracted, ensure_ascii=False, indent=2)


def _load_todos() -> list[dict[str, Any]]:
    todo_file = _todo_file()
    if not todo_file.exists():
        return []
    try:
        payload = json.loads(todo_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("todos storage is corrupted") from exc
    if not isinstance(payload, list):
        raise ValueError("todos storage is corrupted")
    return [item for item in payload if isinstance(item, dict)]


def _save_todos(items: list[dict[str, Any]]) -> None:
    _todo_file().write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


async def todo_create(arguments: dict[str, Any]) -> str:
    title = _require_string(arguments, "title")
    if len(title) > 120:
        raise ValueError("title must be 120 characters or fewer")

    todos = _load_todos()
    todo = {
        "id": f"todo-{len(todos) + 1}",
        "title": title,
        "created_at": datetime.now(UTC).isoformat(),
    }
    todos.append(todo)
    _save_todos(todos)
    return json.dumps(todo, ensure_ascii=False, indent=2)


async def todo_list(arguments: dict[str, Any]) -> str:
    _ = arguments
    todos = _load_todos()
    return json.dumps(todos, ensure_ascii=False, indent=2)


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="text_stats",
            description="Call this when the user asks to count characters, lines, or words in text.",
            input_schema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Text to analyze",
                        "minLength": 1,
                    }
                },
                "required": ["text"],
            },
            handler=text_stats,
            examples=[
                {"title": "统计 hello world", "arguments": {"text": "hello world"}},
                {"title": "统计两行文本", "arguments": {"text": "hello\nworld"}},
            ],
            learning_notes=[
                "这是纯函数工具，适合展示确定性计算。",
                "模型不应该自己猜字符数、行数或词数。",
            ],
        )
    )
    registry.register(
        ToolDefinition(
            name="note_search",
            description="Call this when the user asks to search local demo notes in the sandbox.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search keyword for sandbox notes",
                        "minLength": 1,
                    }
                },
                "required": ["query"],
            },
            handler=note_search,
            examples=[
                {"title": "搜索 demo", "arguments": {"query": "demo"}},
                {"title": "搜索 FastAPI", "arguments": {"query": "fastapi"}},
            ],
            learning_notes=[
                "这是受控本地检索工具，只读取 sandbox/notes/*.md。",
                "工具边界由后端控制，不由模型自由决定。",
            ],
        )
    )
    registry.register(
        ToolDefinition(
            name="json_extract",
            description=(
                "Call this when the user asks to extract a small structured record "
                "from plain text using a field list."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Source text to extract from",
                        "minLength": 1,
                    },
                    "fields": {
                        "type": "array",
                        "description": "Field names to extract",
                        "items": {"type": "string"},
                        "minItems": 1,
                    },
                },
                "required": ["text", "fields"],
            },
            handler=json_extract,
            examples=[
                {
                    "title": "提取联系人信息",
                    "arguments": {
                        "text": "name: Alice\nemail: alice@example.com\ncity: Shanghai",
                        "fields": ["name", "email", "city"],
                    },
                }
            ],
            learning_notes=[
                "模型负责理解意图，工具负责输出稳定的结构化 JSON。",
                "这里用简单规则抽取，重点是协议而不是复杂 NLP。",
            ],
        )
    )
    registry.register(
        ToolDefinition(
            name="todo_create",
            description="Call this when the user asks to create a simple local todo item in the sandbox.",
            input_schema={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Todo title",
                        "minLength": 1,
                    }
                },
                "required": ["title"],
            },
            handler=todo_create,
            examples=[
                {"title": "创建学习任务", "arguments": {"title": "复盘 tool_call trace"}},
            ],
            learning_notes=[
                "这是安全副作用工具，只写入 sandbox/todos.json。",
                "它演示了可审计副作用，而不是任意文件写入。",
            ],
        )
    )
    registry.register(
        ToolDefinition(
            name="todo_list",
            description="Call this when the user asks to list local todo items previously created in the sandbox.",
            input_schema={
                "type": "object",
                "properties": {},
            },
            handler=todo_list,
            examples=[
                {"title": "查看 todo 列表", "arguments": {}},
            ],
            learning_notes=[
                "和 todo_create 配对，展示副作用工具如何被读取和复盘。",
                "这个工具无参数，适合观察空对象 schema 的表现。",
            ],
        )
    )
    return registry
