import pytest

from app.tools.builtin import build_default_registry


@pytest.mark.asyncio
async def test_text_stats_tool_returns_counts():
    registry = build_default_registry()

    result = await registry.execute("text_stats", {"text": "hello world\nagain"})

    assert result.is_error is False
    assert "characters=17" in result.content
    assert "lines=2" in result.content
    assert "words=3" in result.content


@pytest.mark.asyncio
async def test_text_stats_tool_rejects_blank_text():
    registry = build_default_registry()

    result = await registry.execute("text_stats", {"text": "   "})

    assert result.is_error is True
    assert "text is required" in result.content


@pytest.mark.asyncio
async def test_note_search_tool_reads_only_sandbox_notes(monkeypatch, tmp_path):
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir(parents=True)
    (notes_dir / "demo.md").write_text("FastAPI demo note", encoding="utf-8")
    (notes_dir / "ignore.txt").write_text("should not be read", encoding="utf-8")
    monkeypatch.setenv("AGENT_PLAYGROUND_SANDBOX_DIR", str(notes_dir))
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        registry = build_default_registry()
        result = await registry.execute("note_search", {"query": "demo"})
    finally:
        get_settings.cache_clear()

    assert result.is_error is False
    assert "demo.md" in result.content
    assert "FastAPI demo note" in result.content
    assert "ignore.txt" not in result.content


@pytest.mark.asyncio
async def test_note_search_tool_rejects_blank_query():
    registry = build_default_registry()

    result = await registry.execute("note_search", {"query": "   "})

    assert result.is_error is True
    assert "query is required" in result.content


@pytest.mark.asyncio
async def test_unknown_tool_is_observable_error():
    registry = build_default_registry()

    result = await registry.execute("missing", {})

    assert result.is_error is True
    assert result.content == "Unknown tool: missing"


@pytest.mark.asyncio
async def test_json_extract_tool_returns_structured_json():
    registry = build_default_registry()

    result = await registry.execute(
        "json_extract",
        {"text": "name: Alice\nemail: alice@example.com\ncity: Shanghai", "fields": ["name", "email", "city"]},
    )

    assert result.is_error is False
    assert '"name": "Alice"' in result.content
    assert '"email": "alice@example.com"' in result.content


@pytest.mark.asyncio
async def test_json_extract_tool_requires_non_empty_fields():
    registry = build_default_registry()

    result = await registry.execute("json_extract", {"text": "name: Alice", "fields": []})

    assert result.is_error is True
    assert "fields is required" in result.content


@pytest.mark.asyncio
async def test_todo_tools_write_and_read_sandbox_storage():
    registry = build_default_registry()

    create_result = await registry.execute("todo_create", {"title": "复盘 tool_call trace"})
    list_result = await registry.execute("todo_list", {})

    assert create_result.is_error is False
    assert list_result.is_error is False
    assert "复盘 tool_call trace" in create_result.content
    assert "复盘 tool_call trace" in list_result.content


@pytest.mark.asyncio
async def test_todo_create_rejects_overlong_titles():
    registry = build_default_registry()

    result = await registry.execute("todo_create", {"title": "x" * 121})

    assert result.is_error is True
    assert "120 characters or fewer" in result.content


def test_tui_tool_sample_arguments_match_json_schema_types():
    from app.tui.screens.tools_lab import sample_arguments_for_schema

    assert sample_arguments_for_schema(
        {
            "properties": {
                "text": {"type": "string"},
                "count": {"type": "integer"},
                "ratio": {"type": "number"},
                "enabled": {"type": "boolean"},
                "items": {"type": "array"},
                "options": {"type": "object"},
            }
        }
    ) == {
        "text": "hello world",
        "count": 1,
        "ratio": 1.0,
        "enabled": True,
        "items": [],
        "options": {},
    }


def test_tui_tool_sample_arguments_prefers_examples_when_available():
    from app.tui.screens.tools_lab import sample_arguments_for_tool

    assert sample_arguments_for_tool(
        {
            "name": "text_stats",
            "examples": [{"title": "demo", "arguments": {"text": "hello"}}],
            "input_schema": {"properties": {"text": {"type": "string"}}},
        }
    ) == {"text": "hello"}
