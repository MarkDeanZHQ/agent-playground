import httpx

from app.tui.client import AgentPlaygroundClient
from app.tui.main import AgentPlaygroundTui
from app.tui.screens.chat_lab import ChatLabScreen
from app.tui.screens.dashboard import DashboardScreen
from app.tui.screens.memory_lab import MemoryLabScreen
from app.tui.screens.run_trace import RunTraceScreen
from app.tui.screens.tools_lab import (
    ToolsLabScreen,
    format_history_entry,
    format_schema_validation_error,
    format_tool_json_error,
    format_tool_result,
    make_example_prompt,
    sample_arguments_for_schema,
    sample_arguments_for_tool,
    validate_arguments_against_schema,
)
from app.tui.screens.validation_lab import (
    CHECKS_BY_GROUP,
    GROUPS,
    SKIPPED,
    ValidationLabScreen,
    ValidationResult,
)
from app.tui.widgets import (
    NAV_ITEMS,
    CopyableText,
    ScreenNavBar,
    empty_state,
    format_http_error,
    page_shortcuts,
    page_title,
)


def binding_key(binding):
    return binding.key if hasattr(binding, "key") else binding[0]


def test_tui_app_title_and_subtitle_are_learning_oriented():
    assert AgentPlaygroundTui.TITLE == "Agent Playground"
    assert "学习控制台" in AgentPlaygroundTui.SUB_TITLE


def test_tui_text_helpers_format_compact_guidance():
    assert page_title("A", "B") == "A\nB"
    assert page_title("A") == "A"
    assert page_shortcuts("r 刷新", "F2 开始对话") == "本页快捷键：r 刷新 · F2 开始对话"
    assert empty_state("暂无数据。", "发送一条消息。", "请统计 hello world") == (
        "暂无数据。\n下一步：发送一条消息。\n示例：请统计 hello world"
    )


def test_copyable_text_is_read_only_and_keeps_written_lines_copyable():
    output = CopyableText("first")

    output.write("second")

    assert output.read_only is True
    assert output.show_cursor is False
    assert output.text == "first\nsecond"


def test_screen_nav_bar_items_match_global_page_switch_keys():
    assert NAV_ITEMS == [
        ("f1", "dashboard", "Dashboard"),
        ("f2", "chat_lab", "Chat"),
        ("f3", "run_trace", "Trace"),
        ("f4", "tools_lab", "Tools"),
        ("f5", "memory_lab", "Memory"),
        ("f6", "validation_lab", "Validation"),
    ]


def test_global_page_switch_keys_have_priority_over_focused_widgets():
    page_keys = {"f1", "f2", "f3", "f4", "f5", "f6"}
    bindings = [binding for binding in AgentPlaygroundTui.BINDINGS if binding_key(binding) in page_keys]

    assert {binding_key(binding) for binding in bindings} == page_keys
    assert all(binding.priority for binding in bindings)


def test_page_local_shortcuts_do_not_shadow_global_page_switch_keys():
    page_keys = {"f1", "f2", "f3", "f4", "f5", "f6"}
    screen_classes = [
        DashboardScreen,
        ChatLabScreen,
        RunTraceScreen,
        ToolsLabScreen,
        MemoryLabScreen,
        ValidationLabScreen,
    ]

    for screen_class in screen_classes:
        local_keys = {binding_key(binding) for binding in screen_class.BINDINGS}
        assert local_keys.isdisjoint(page_keys), screen_class.__name__


def test_screen_nav_bar_marks_active_page_and_uses_clickable_ids():
    nav = ScreenNavBar("validation_lab")

    buttons = list(nav.compose())

    assert [button.id for button in buttons] == [
        "nav-dashboard",
        "nav-chat_lab",
        "nav-run_trace",
        "nav-tools_lab",
        "nav-memory_lab",
        "nav-validation_lab",
    ]
    assert [str(button.label) for button in buttons] == [
        "F1 Dashboard",
        "F2 Chat",
        "F3 Trace",
        "F4 Tools",
        "F5 Memory",
        "F6 Validation",
    ]
    assert buttons[-1].has_class("active")
    assert not buttons[0].has_class("active")


def test_screens_render_fixed_shortcuts_separately_from_dynamic_status():
    app_css = AgentPlaygroundTui.CSS

    assert ".page-shortcuts" in app_css
    assert ".page-status" in app_css


def test_tui_http_error_formatter_classifies_connection_timeout_and_status_errors():
    connect_error = httpx.ConnectError("refused")
    timeout = httpx.ReadTimeout("slow")
    request = httpx.Request("GET", "http://test")
    response = httpx.Response(500, request=request)
    status_error = httpx.HTTPStatusError("boom", request=request, response=response)

    assert "无法连接 API" in format_http_error(connect_error)
    assert "uv run uvicorn app.main:app --reload" in format_http_error(connect_error)
    assert "请求超时" in format_http_error(timeout)
    assert "API 返回错误状态：500" in format_http_error(status_error)


def test_tool_json_error_includes_schema_generated_sample_and_original_error():
    sample = sample_arguments_for_schema({"properties": {"query": {"type": "string"}}})

    message = format_tool_json_error(ValueError("line 1, column 2: bad json"), sample)

    assert "JSON_PARSE_ERROR" in message
    assert "当前工具参数示例" in message
    assert '"query": "hello world"' in message
    assert "line 1, column 2" in message


def test_tool_sample_arguments_are_schema_driven_not_tool_name_driven():
    assert sample_arguments_for_schema({"properties": {"custom_flag": {"type": "boolean"}}}) == {"custom_flag": True}


def test_tool_sample_arguments_use_examples_first():
    assert sample_arguments_for_tool(
        {
            "examples": [{"title": "示例", "arguments": {"query": "demo"}}],
            "input_schema": {"properties": {"query": {"type": "string"}}},
        }
    ) == {"query": "demo"}


def test_tool_schema_validation_reports_required_and_type_errors():
    issues = validate_arguments_against_schema(
        {"fields": "oops"},
        {
            "properties": {
                "text": {"type": "string", "minLength": 1},
                "fields": {"type": "array", "minItems": 1},
            },
            "required": ["text", "fields"],
        },
    )

    message = format_schema_validation_error(issues, {"text": "name: Alice", "fields": ["name"]})

    assert any(issue.kind == "SCHEMA_VALIDATION_ERROR" for issue in issues)
    assert "缺少必填字段：text" in message
    assert "字段 `fields` 必须是 array" in message


def test_tool_result_formatter_separates_status_arguments_content_and_raw_response():
    message = format_tool_result(
        {
            "name": "todo_create",
            "arguments": {"title": "复盘 trace"},
            "content": '{"id":"todo-1"}',
            "is_error": False,
        }
    )

    assert "Status" in message
    assert "OK" in message
    assert "Arguments" in message
    assert '"title": "复盘 trace"' in message
    assert "Content" in message
    assert '{"id":"todo-1"}' in message
    assert "Raw response" in message


def test_tool_history_entry_is_compact_and_observable():
    entry = format_history_entry(
        {
            "time": "12:34:56",
            "tool": "json_extract",
            "status": "OK",
            "arguments_summary": '{"text":"name: Alice"}',
            "result_summary": '{"name":"Alice"}',
        }
    )

    assert "[12:34:56] json_extract OK" in entry
    assert "参数摘要：" in entry
    assert "结果摘要：" in entry


def test_make_example_prompt_covers_structured_and_side_effect_tools():
    assert "提取 name, email" in make_example_prompt(
        {
            "name": "json_extract",
        },
        {"text": "name: Alice", "fields": ["name", "email"]},
    )
    assert make_example_prompt({"name": "todo_list"}, {}) == "请列出当前 sandbox 里的待办列表。"



def test_memory_lab_parse_query_supports_management_statuses():
    screen = MemoryLabScreen(AgentPlaygroundClient("http://test"))

    assert screen._parse_query("FastAPI status:archived") == ("FastAPI", "archived")
    assert screen._parse_query("status:deleted") == (None, "deleted")


def test_validation_lab_uses_explicit_tool_prompts_and_longer_chat_timeout():
    screen = ValidationLabScreen(AgentPlaygroundClient("http://test"))

    assert screen.chat_validation_timeout == 120.0


def test_validation_lab_title_keeps_lab_name_consistent_with_other_pages():
    assert page_title("Validation Lab｜学习验收") == "Validation Lab｜学习验收"


def test_validation_lab_groups_checks_by_learning_stage():
    assert list(GROUPS) == ["core_path", "environment", "dev_quality"]
    assert [check.name for check in CHECKS_BY_GROUP["core_path"]] == [
        "api_health",
        "chat_no_tool",
        "chat_text_stats",
        "chat_note_search",
        "chat_json_extract",
        "chat_todo_roundtrip",
        "memory_roundtrip",
        "run_trace",
    ]
    assert [check.name for check in CHECKS_BY_GROUP["environment"]] == ["claude_config", "docker_config"]
    assert [check.name for check in CHECKS_BY_GROUP["dev_quality"]] == ["pytest", "ruff"]


def test_validation_lab_result_render_includes_next_step_only_when_present():
    passed = ValidationResult(status="✓ 通过", summary="ok", detail="{}", next_step="")
    failed = ValidationResult(status="✗ 失败", summary="bad", detail="trace", next_step="去修它")

    assert passed.render() == "✓ 通过: ok\n{}"
    assert failed.render() == "✗ 失败: bad\ntrace\n下一步：去修它"


def test_validation_lab_claude_skip_uses_explicit_skip_status():
    assert SKIPPED == "○ 跳过"
