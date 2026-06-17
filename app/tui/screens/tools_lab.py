from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Input, Label, ListItem, ListView, Static, TextArea

from app.tui.client import AgentPlaygroundClient
from app.tui.widgets import (
    CopyableText,
    ScreenNavBar,
    empty_state,
    format_http_error,
    page_shortcuts,
    page_title,
    pretty_json,
    short_text,
)


@dataclass(slots=True)
class ValidationIssue:
    kind: str
    message: str
    next_step: str


def sample_value_for_schema(schema: dict[str, Any]) -> Any:
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        schema_type = next((item for item in schema_type if item != "null"), schema_type[0] if schema_type else None)
    if schema_type == "string":
        return "hello world"
    if schema_type == "integer":
        return 1
    if schema_type == "number":
        return 1.0
    if schema_type == "boolean":
        return True
    if schema_type == "array":
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            return [sample_value_for_schema(item_schema)]
        return []
    if schema_type == "object" or "properties" in schema:
        return {}
    return "hello world"


def normalize_schema_type(schema: dict[str, Any]) -> Any:
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        return next(
            (item for item in schema_type if item != "null"),
            schema_type[0] if schema_type else None,
        )
    return schema_type


def sample_arguments_for_schema(input_schema: dict[str, Any]) -> dict[str, Any]:
    properties = input_schema.get("properties", {})
    if not isinstance(properties, dict):
        return {}
    return {
        name: sample_value_for_schema(property_schema)
        for name, property_schema in properties.items()
        if isinstance(property_schema, dict)
    }


def sample_arguments_for_tool(tool: dict[str, Any]) -> dict[str, Any]:
    examples = tool.get("examples", [])
    if isinstance(examples, list):
        for example in examples:
            if isinstance(example, dict) and isinstance(example.get("arguments"), dict):
                return example["arguments"]
    return sample_arguments_for_schema(tool.get("input_schema", {}))


def format_tool_json_error(exc: ValueError, sample_arguments: dict[str, Any]) -> str:
    return (
        "错误类型：JSON_PARSE_ERROR\n"
        "当前参数不是合法 JSON 对象。\n\n"
        "当前工具参数示例：\n"
        f"{pretty_json(sample_arguments)}\n\n"
        "下一步建议：修正 JSON 语法，保持最外层是对象。\n\n"
        f"原始错误：{exc}"
    )


def validate_arguments_against_schema(arguments: dict[str, Any], schema: dict[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        properties = {}
    required = schema.get("required", [])
    if not isinstance(required, list):
        required = []

    for name in required:
        if name not in arguments:
            issues.append(
                ValidationIssue(
                    kind="SCHEMA_VALIDATION_ERROR",
                    message=f"缺少必填字段：{name}",
                    next_step=f"补上 `{name}` 字段后再调用。",
                )
            )

    for name, value in arguments.items():
        property_schema = properties.get(name)
        if not isinstance(property_schema, dict):
            continue
        expected_type = normalize_schema_type(property_schema)
        if expected_type == "string":
            if not isinstance(value, str):
                issues.append(
                    ValidationIssue(
                        kind="SCHEMA_VALIDATION_ERROR",
                        message=f"字段 `{name}` 必须是 string。",
                        next_step="把该字段改成字符串。",
                    )
                )
                continue
            min_length = property_schema.get("minLength")
            if isinstance(min_length, int) and len(value.strip()) < min_length:
                issues.append(
                    ValidationIssue(
                        kind="SCHEMA_VALIDATION_ERROR",
                        message=f"字段 `{name}` 不能是空字符串。",
                        next_step="填入非空文本。",
                    )
                )
        elif expected_type == "integer" and not isinstance(value, int):
            issues.append(
                ValidationIssue(
                    kind="SCHEMA_VALIDATION_ERROR",
                    message=f"字段 `{name}` 必须是 integer。",
                    next_step="把该字段改成整数。",
                )
            )
        elif expected_type == "number" and not isinstance(value, int | float):
            issues.append(
                ValidationIssue(
                    kind="SCHEMA_VALIDATION_ERROR",
                    message=f"字段 `{name}` 必须是 number。",
                    next_step="把该字段改成数字。",
                )
            )
        elif expected_type == "boolean" and not isinstance(value, bool):
            issues.append(
                ValidationIssue(
                    kind="SCHEMA_VALIDATION_ERROR",
                    message=f"字段 `{name}` 必须是 boolean。",
                    next_step="把该字段改成 true 或 false。",
                )
            )
        elif expected_type == "array":
            if not isinstance(value, list):
                issues.append(
                    ValidationIssue(
                        kind="SCHEMA_VALIDATION_ERROR",
                        message=f"字段 `{name}` 必须是 array。",
                        next_step="把该字段改成 JSON 数组。",
                    )
                )
                continue
            min_items = property_schema.get("minItems")
            if isinstance(min_items, int) and len(value) < min_items:
                issues.append(
                    ValidationIssue(
                        kind="SCHEMA_VALIDATION_ERROR",
                        message=f"字段 `{name}` 至少需要 {min_items} 个元素。",
                        next_step="补充数组内容。",
                    )
                )
        elif expected_type == "object" and not isinstance(value, dict):
            issues.append(
                ValidationIssue(
                    kind="SCHEMA_VALIDATION_ERROR",
                    message=f"字段 `{name}` 必须是 object。",
                    next_step="把该字段改成 JSON 对象。",
                )
            )
    return issues


def format_schema_validation_error(issues: list[ValidationIssue], sample_arguments: dict[str, Any]) -> str:
    summary = "\n".join(f"- {issue.message}" for issue in issues)
    next_steps = "\n".join(f"- {issue.next_step}" for issue in issues)
    return (
        "错误类型：SCHEMA_VALIDATION_ERROR\n"
        f"{summary}\n\n"
        "当前工具参数示例：\n"
        f"{pretty_json(sample_arguments)}\n\n"
        "下一步建议：\n"
        f"{next_steps}"
    )


def format_tool_result(result: dict[str, Any]) -> str:
    error_type = "TOOL_EXECUTION_ERROR" if result.get("is_error") else "OK"
    return (
        f"Status\n{error_type}\n\n"
        "Arguments\n"
        f"{pretty_json(result.get('arguments', {}))}\n\n"
        "Content\n"
        f"{result.get('content', '')}\n\n"
        "Raw response\n"
        f"{pretty_json(result)}"
    )


def format_history_entry(item: dict[str, Any]) -> str:
    return (
        f"[{item['time']}] {item['tool']} {item['status']}\n"
        f"参数摘要：{item['arguments_summary']}\n"
        f"结果摘要：{item['result_summary']}"
    )


def make_example_prompt(tool: dict[str, Any], arguments: dict[str, Any]) -> str:
    name = tool.get("name", "")
    if name == "text_stats":
        return f"请统计这段文本：{arguments.get('text', '')}"
    if name == "note_search":
        return f"请帮我搜索本地 demo 笔记，关键词是 {arguments.get('query', '')}"
    if name == "json_extract":
        fields = ", ".join(arguments.get("fields", []))
        return f"请从下面文本提取 {fields} 字段：\n{arguments.get('text', '')}"
    if name == "todo_create":
        return f"请创建一个待办：{arguments.get('title', '')}"
    if name == "todo_list":
        return "请列出当前 sandbox 里的待办列表。"
    return f"请调用 {name} 工具处理这些参数：{pretty_json(arguments)}"


class ToolsLabScreen(Screen[None]):
    BINDINGS = [
        ("r", "refresh", "刷新"),
        ("i", "invoke", "调用"),
        ("ctrl+enter", "invoke", "调用"),
        ("e", "next_example", "示例"),
        ("s", "send_to_chat", "送到 Chat"),
        ("t", "view_latest_trace", "最近 Trace"),
    ]

    def __init__(self, client: AgentPlaygroundClient) -> None:
        super().__init__()
        self.client = client
        self.tools: list[dict[str, Any]] = []
        self.example_indices: dict[str, int] = {}
        self.history: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="tools-lab"):
            yield Static(
                page_title(
                    "Tools Lab｜工具实验",
                    "查看工具 schema、学习点、示例参数和错误类型，完成手动调用与自动触发对照",
                ),
                id="tools-title",
                classes="page-title",
            )
            yield Static(
                page_shortcuts(
                    "方向键选择工具",
                    "Ctrl+Enter/i 调用",
                    "e 切换示例",
                    "s 送到 Chat",
                    "t 最近 Trace",
                    "r 刷新",
                ),
                id="tools-shortcuts",
                classes="page-shortcuts",
            )
            yield Static(
                "准备就绪：选择工具后先看学习点，再编辑 JSON 参数。",
                id="tools-status",
                classes="page-status",
            )
            with Horizontal(id="tools-panels"):
                with Vertical(id="tools-list-panel", classes="panel"):
                    yield Static("Tools｜已注册工具", classes="panel-title")
                    yield ListView(id="tools-list")
                with Vertical(id="tool-detail-panel", classes="panel"):
                    yield Static("Learning Panel｜摘要、参数、学习点与原始 Schema", classes="panel-title")
                    yield CopyableText(language="markdown", id="tool-detail")
            with Vertical(id="tools-actions"):
                yield Static("Invoke｜输入 JSON 参数后调用选中工具（Ctrl+Enter/i）", classes="panel-title")
                with Horizontal(id="tool-form"):
                    yield TextArea(
                        language="json",
                        soft_wrap=True,
                        show_line_numbers=False,
                        compact=True,
                        placeholder='Args JSON，例如 {"text":"hello world"}',
                        id="tool-args",
                    )
                    with Vertical(id="tool-buttons"):
                        yield Button("Invoke selected tool", id="invoke-tool", variant="primary")
                        yield Button("Use next example", id="next-example")
                        yield Button("Send to Chat", id="send-to-chat")
                        yield Button("View latest trace", id="view-latest-trace")
                with Horizontal(id="tools-result-panels"):
                    with Vertical(classes="panel"):
                        yield Static("Result｜状态、参数、内容与原始响应", classes="panel-title")
                        yield CopyableText(language="markdown", id="tool-result")
                    with Vertical(classes="panel"):
                        yield Static("History｜最近 10 次手动调用", classes="panel-title")
                        yield CopyableText(language="markdown", id="tool-history")
            yield ScreenNavBar("tools_lab")

    async def on_mount(self) -> None:
        self.run_worker(self.refresh_tools(), exclusive=True)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "invoke-tool":
            await self.invoke()
        elif button_id == "next-example":
            self.action_next_example()
        elif button_id == "send-to-chat":
            self.action_send_to_chat()
        elif button_id == "view-latest-trace":
            self.action_view_latest_trace()

    def action_refresh(self) -> None:
        self.run_worker(self.refresh_tools(), exclusive=True)

    def action_invoke(self) -> None:
        self.run_worker(self.invoke(), exclusive=True)

    def action_next_example(self) -> None:
        tool = self._selected_tool()
        if tool is None:
            return
        self._load_next_example(tool)

    def action_send_to_chat(self) -> None:
        tool = self._selected_tool()
        if tool is None:
            return
        try:
            arguments = self._parse_arguments_json(self.query_one("#tool-args", TextArea).text)
        except ValueError as exc:
            self._write_error_output(
                format_tool_json_error(exc, sample_arguments_for_tool(tool)),
                "JSON 参数格式错误，不能送到 Chat。",
            )
            return
        chat_screen = self.app.get_screen("chat_lab")
        prompt = make_example_prompt(tool, arguments)
        if hasattr(chat_screen, "query_one"):
            input_box = chat_screen.query_one("#chat-input", Input)
            if hasattr(input_box, "value"):
                input_box.value = prompt
            input_box.focus()
        self.app.switch_screen("chat_lab")
        self.query_one("#tools-status", Static).update("已把自然语言示例送到 Chat Lab，去看模型会不会自动调工具。")

    def action_view_latest_trace(self) -> None:
        self.app.switch_screen("run_trace")
        chat_screen = self.app.get_screen("chat_lab")
        run_id = getattr(chat_screen, "last_run_id", None)
        if run_id:
            trace_screen = self.app.screen
            if hasattr(trace_screen, "load_run"):
                trace_screen.run_worker(trace_screen.load_run(run_id), exclusive=True)
        self.query_one("#tools-status", Static).update("已切到 Run Trace。手动调用不会伪装成 Agent Run。")

    async def refresh_tools(self) -> None:
        status = self.query_one("#tools-status", Static)
        detail = self.query_one("#tool-detail", CopyableText)
        result = self.query_one("#tool-result", CopyableText)
        tools_list = self.query_one("#tools-list", ListView)
        status.update("正在加载工具列表...")
        detail.clear()
        result.clear()
        await tools_list.clear()
        try:
            self.tools = await self.client.list_tools()
        except httpx.HTTPError as exc:
            self.tools = []
            status.update("加载工具失败。")
            detail.write(format_http_error(exc))
            return
        self.example_indices = {tool["name"]: 0 for tool in self.tools if "name" in tool}
        for tool in self.tools:
            await tools_list.append(ListItem(Label(tool["name"])))
        if self.tools:
            tools_list.index = 0
            self._show_tool(self.tools[0])
            status.update("工具列表已刷新。")
            return
        status.update("暂无已注册工具。")
        detail.write(
            empty_state(
                "暂无工具。",
                "检查 API /api/v1/tools 是否正常，或确认后端已注册教学工具。",
                "uv run uvicorn app.main:app --reload",
            )
        )

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id == "tools-list" and event.list_view.index is not None:
            self._show_tool(self.tools[event.list_view.index])

    async def invoke(self) -> None:
        tool = self._selected_tool()
        result = self.query_one("#tool-result", CopyableText)
        status = self.query_one("#tools-status", Static)
        if tool is None:
            result.clear()
            result.write("请先在左侧选择一个工具。")
            status.update("尚未选择工具。")
            return
        sample = sample_arguments_for_tool(tool)
        try:
            arguments = self._parse_arguments_json(self.query_one("#tool-args", TextArea).text)
        except ValueError as exc:
            self._write_error_output(format_tool_json_error(exc, sample), "JSON 参数格式错误。")
            return
        issues = validate_arguments_against_schema(arguments, tool.get("input_schema", {}))
        if issues:
            self._write_error_output(format_schema_validation_error(issues, sample), "参数未通过 schema 校验。")
            return
        status.update(f"正在调用工具：{tool['name']}")
        try:
            payload = await self.client.invoke_tool(tool["name"], pretty_json(arguments))
        except httpx.HTTPError as exc:
            self._write_error_output(
                f"错误类型：HTTP_ERROR\n{format_http_error(exc)}",
                "工具调用请求失败。",
            )
            self._add_history(tool["name"], arguments, "HTTP_ERROR", str(exc))
            return
        result.clear()
        result.write(format_tool_result(payload))
        status_message = "工具调用完成。"
        if payload.get("is_error"):
            status_message = "工具执行返回可观察错误。"
        status.update(status_message)
        self._add_history(
            tool["name"],
            arguments,
            "TOOL_EXECUTION_ERROR" if payload.get("is_error") else "OK",
            str(payload.get("content", "")),
        )

    def _selected_tool(self) -> dict[str, Any] | None:
        tools_list = self.query_one("#tools-list", ListView)
        if tools_list.index is None or tools_list.index >= len(self.tools):
            return None
        return self.tools[tools_list.index]

    def _show_tool(self, tool: dict[str, Any]) -> None:
        detail = self.query_one("#tool-detail", CopyableText)
        detail.clear()
        detail.write(self._render_tool_detail(tool))
        args = self.query_one("#tool-args", TextArea)
        args.load_text(pretty_json(self._current_example_arguments(tool)))
        result = self.query_one("#tool-result", CopyableText)
        result.clear()
        result.write(
            "Status\nREADY\n\n"
            "Arguments\n"
            f"{pretty_json(self._current_example_arguments(tool))}\n\n"
            "Content\n"
            f"Selected: {tool['name']} {short_text(tool.get('description'), 120)}\n\n"
            "Raw response\n{}"
        )
        self._render_history()
        self.query_one("#tools-status", Static).update(f"已选择工具：{tool['name']}")

    def _render_tool_detail(self, tool: dict[str, Any]) -> str:
        input_schema = tool.get("input_schema", {})
        properties = input_schema.get("properties", {})
        required_field = input_schema.get("required", [])
        required = set(required_field) if isinstance(required_field, list) else set()
        lines = [
            f"Tool: {tool.get('name', '')}",
            f"When to use: {tool.get('description', '')}",
            "",
            "Parameters",
        ]
        if isinstance(properties, dict) and properties:
            for name, property_schema in properties.items():
                if not isinstance(property_schema, dict):
                    continue
                field_type = property_schema.get("type", "unknown")
                required_text = "required" if name in required else "optional"
                description = property_schema.get("description", "")
                lines.append(f"- {name} | {field_type} | {required_text} | {description}")
        else:
            lines.append("- 无参数 | object | optional | 当前工具不需要输入字段")
        learning_notes = tool.get("learning_notes", [])
        lines.extend(["", "Learning notes"])
        if isinstance(learning_notes, list) and learning_notes:
            lines.extend(f"- {note}" for note in learning_notes)
        else:
            lines.append("- 暂无学习提示。")
        examples = tool.get("examples", [])
        lines.extend(["", "Examples"])
        if isinstance(examples, list) and examples:
            current_index = self.example_indices.get(tool.get("name", ""), 0)
            for index, example in enumerate(examples):
                if not isinstance(example, dict):
                    continue
                marker = "*" if index == current_index else "-"
                title = example.get("title", f"示例 {index + 1}")
                arguments = pretty_json(example.get("arguments", {}))
                lines.append(f"{marker} {title}: {arguments}")
        else:
            lines.append(f"- {pretty_json(sample_arguments_for_schema(input_schema))}")
        lines.extend(["", "Raw schema", pretty_json(tool)])
        return "\n".join(lines)

    def _current_example_arguments(self, tool: dict[str, Any]) -> dict[str, Any]:
        examples = tool.get("examples", [])
        if isinstance(examples, list) and examples:
            index = self.example_indices.get(tool.get("name", ""), 0) % len(examples)
            example = examples[index]
            if isinstance(example, dict) and isinstance(example.get("arguments"), dict):
                return example["arguments"]
        return sample_arguments_for_schema(tool.get("input_schema", {}))

    def _load_next_example(self, tool: dict[str, Any]) -> None:
        examples = tool.get("examples", [])
        if not isinstance(examples, list) or not examples:
            args = self.query_one("#tool-args", TextArea)
            args.load_text(pretty_json(sample_arguments_for_schema(tool.get("input_schema", {}))))
            self.query_one("#tools-status", Static).update("当前工具没有 examples，已回退到 schema 自动样例。")
            return
        current = self.example_indices.get(tool["name"], 0)
        self.example_indices[tool["name"]] = (current + 1) % len(examples)
        self._show_tool(tool)
        self.query_one("#tools-status", Static).update(f"已切换到 {tool['name']} 的下一个示例。")

    def _parse_arguments_json(self, arguments_json: str) -> dict[str, Any]:
        try:
            arguments = json.loads(arguments_json or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc
        if not isinstance(arguments, dict):
            raise ValueError("tool arguments must be a JSON object")
        return arguments

    def _write_error_output(self, message: str, status_text: str) -> None:
        result = self.query_one("#tool-result", CopyableText)
        result.clear()
        result.write(message)
        self.query_one("#tools-status", Static).update(status_text)

    def _add_history(self, tool_name: str, arguments: dict[str, Any], status: str, result_summary: str) -> None:
        self.history.insert(
            0,
            {
                "time": datetime.now().strftime("%H:%M:%S"),
                "tool": tool_name,
                "status": status,
                "arguments_summary": short_text(pretty_json(arguments), 80),
                "result_summary": short_text(result_summary, 120),
            },
        )
        self.history = self.history[:10]
        self._render_history()

    def _render_history(self) -> None:
        history = self.query_one("#tool-history", CopyableText)
        history.clear()
        if not self.history:
            history.write("暂无手动调用历史。")
            return
        history.write("\n\n".join(format_history_entry(item) for item in self.history))
