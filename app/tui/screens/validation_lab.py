from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Label, ListItem, ListView, Static

from app.tui.client import AgentPlaygroundClient
from app.tui.widgets import (
    CopyableText,
    ScreenNavBar,
    format_http_error,
    page_shortcuts,
    page_title,
    pretty_json,
    short_text,
)


@dataclass(frozen=True)
class ValidationCheck:
    name: str
    description: str
    group: str


@dataclass(frozen=True)
class ValidationGroup:
    name: str
    title: str
    description: str


GROUPS = {
    "core_path": ValidationGroup(
        "core_path",
        "Core Path｜核心学习闭环",
        "先确认 API、Agent、记忆和 Trace 这条主链路。",
    ),
    "environment": ValidationGroup(
        "environment",
        "Environment｜环境配置",
        "再看本地运行条件是否满足演示和学习。",
    ),
    "dev_quality": ValidationGroup(
        "dev_quality",
        "Developer Quality｜开发质量",
        "最后看测试和 lint 这类附加自检。",
    ),
}

CHECKS = [
    ValidationCheck("api_health", "请求 /health", "core_path"),
    ValidationCheck("chat_no_tool", "普通 Chat 不触发工具", "core_path"),
    ValidationCheck("chat_text_stats", "Chat 触发 text_stats", "core_path"),
    ValidationCheck("chat_note_search", "Chat 触发 note_search", "core_path"),
    ValidationCheck("memory_roundtrip", "记忆写入后可查询", "core_path"),
    ValidationCheck("run_trace", "run_id 可查询 trace", "core_path"),
    ValidationCheck("claude_config", "Claude provider 配置检查", "environment"),
    ValidationCheck("docker_config", "Docker Compose 配置检查", "environment"),
    ValidationCheck("pytest", "uv run pytest", "dev_quality"),
    ValidationCheck("ruff", "uv run ruff check .", "dev_quality"),
]

CHECKS_BY_GROUP = {
    group: [check for check in CHECKS if check.group == group]
    for group in GROUPS
}

NOT_RUN = "? 未运行"
RUNNING = "… 运行中"
PASSED = "✓ 通过"
FAILED = "✗ 失败"
SKIPPED = "○ 跳过"


@dataclass(frozen=True)
class ValidationResult:
    status: str
    summary: str
    detail: str
    next_step: str

    def render(self) -> str:
        lines = [f"{self.status}: {self.summary}", self.detail]
        if self.next_step:
            lines.append(f"下一步：{self.next_step}")
        return "\n".join(lines)


class ValidationLabScreen(Screen[None]):
    BINDINGS = [
        ("r", "run_selected", "运行选中项"),
        ("c", "run_core_path", "运行核心闭环"),
        ("a", "run_all", "运行全部"),
    ]

    def __init__(self, client: AgentPlaygroundClient) -> None:
        super().__init__()
        self.client = client
        self.statuses = {check.name: NOT_RUN for check in CHECKS}
        self.list_items: list[ValidationCheck | None] = []
        self.chat_validation_timeout = 120.0

    def compose(self) -> ComposeResult:
        with Vertical(id="validation-lab"):
            yield Static(
                page_title("Validation Lab｜学习验收"),
                id="validation-title",
                classes="page-title",
            )
            yield Static(
                page_shortcuts("c 核心闭环", "r 运行选中项", "a 运行全部"),
                id="validation-shortcuts",
                classes="page-shortcuts",
            )
            yield Static(
                "建议先运行核心闭环，再看环境和开发质量。",
                id="validation-status",
                classes="page-status",
            )
            with Horizontal(id="validation-panels"):
                with Vertical(id="validation-list-panel", classes="panel"):
                    yield Static("Checks｜学习验收台", classes="panel-title")
                    yield ListView(id="validation-list")
                with Vertical(id="validation-output-panel", classes="panel"):
                    yield Static("Output｜结论、详情与下一步", classes="panel-title")
                    yield CopyableText(id="validation-output")
            yield Button("Run core path", id="run-core-path", variant="primary")
            yield Button("Run selected", id="run-validation")
            yield ScreenNavBar("validation_lab")

    async def on_mount(self) -> None:
        await self._render_checks()
        output = self.query_one("#validation-output", CopyableText)
        output.write("初始状态：所有检查项均未运行。")
        output.write("下一步：先按 c 运行核心闭环，再按 a 看完整自检。")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run-core-path":
            self.run_worker(self.run_core_path(), exclusive=True)
        elif event.button.id == "run-validation":
            self.run_worker(self.run_selected(), exclusive=True)

    def action_run_selected(self) -> None:
        self.run_worker(self.run_selected(), exclusive=True)

    def action_run_core_path(self) -> None:
        self.run_worker(self.run_core_path(), exclusive=True)

    def action_run_all(self) -> None:
        self.run_worker(self.run_all(), exclusive=True)

    async def run_core_path(self) -> None:
        await self._run_group("core_path")

    async def run_all(self) -> None:
        await self._run_groups(["core_path", "environment", "dev_quality"])

    async def run_selected(self) -> None:
        checks = self.query_one("#validation-list", ListView)
        if checks.index is None:
            return
        check = self._check_at_index(checks.index)
        if check is None:
            return
        await self._run_checks([check])

    async def _run_groups(self, group_names: list[str]) -> None:
        checks = [check for group_name in group_names for check in CHECKS_BY_GROUP[group_name]]
        await self._run_checks(checks)

    async def _run_group(self, group_name: str) -> None:
        await self._run_checks(CHECKS_BY_GROUP[group_name])

    async def _run_checks(self, checks_to_run: list[ValidationCheck]) -> None:
        output = self.query_one("#validation-output", CopyableText)
        status = self.query_one("#validation-status", Static)
        output.clear()
        for check in checks_to_run:
            status.update(f"正在运行验证项：{check.name}")
            output.write(f"\n## {self._check_label(check)}")
            await self._set_status(check.name, RUNNING)
            result = await self._run_check(check.name)
            await self._set_status(check.name, result.status)
            output.write(result.render())
        status.update("验证完成。先看 Core Path，再看 Environment，最后看 Developer Quality。")

    async def _render_checks(self) -> None:
        checks = self.query_one("#validation-list", ListView)
        current_index = checks.index
        await checks.clear()
        self.list_items = []
        for group_name, group in GROUPS.items():
            await checks.append(ListItem(Label(group.title)))
            self.list_items.append(None)
            await checks.append(ListItem(Label(f"  {group.description}")))
            self.list_items.append(None)
            for check in CHECKS_BY_GROUP[group_name]:
                status = self.statuses[check.name]
                await checks.append(ListItem(Label(f"    [{status}] {check.name}: {check.description}")))
                self.list_items.append(check)
        checks.index = current_index if current_index is not None else 0

    async def _set_status(self, name: str, status: str) -> None:
        self.statuses[name] = status
        await self._render_checks()

    def _check_at_index(self, index: int) -> ValidationCheck | None:
        if index < 0 or index >= len(self.list_items):
            return None
        return self.list_items[index]

    def _check_label(self, check: ValidationCheck) -> str:
        return f"{GROUPS[check.group].title} / {check.name}"

    async def _run_check(self, name: str) -> ValidationResult:
        handlers: dict[str, Callable[[], Awaitable[ValidationResult]]] = {
            "api_health": self._check_api_health,
            "chat_no_tool": self._check_chat_no_tool,
            "chat_text_stats": self._check_chat_text_stats,
            "chat_note_search": self._check_chat_note_search,
            "memory_roundtrip": self._check_memory_roundtrip,
            "run_trace": self._check_run_trace,
            "claude_config": self._check_claude_config,
            "docker_config": self._check_docker_config,
            "pytest": self._check_pytest,
            "ruff": self._check_ruff,
        }
        handler = handlers.get(name)
        if handler is None:
            return ValidationResult(
                status=FAILED,
                summary=f"Unknown validation check: {name}",
                detail="检查 Validation Lab 的 CHECKS 与 handlers 是否同步。",
                next_step="同步检查项定义与执行处理器。",
            )
        try:
            return await handler()
        except httpx.HTTPError as exc:
            return ValidationResult(
                status=FAILED,
                summary="API 请求失败",
                detail=format_http_error(exc),
                next_step="检查后端服务、路由或网络连通性。",
            )
        except KeyError as exc:
            return ValidationResult(
                status=FAILED,
                summary=f"API 响应缺少字段：{exc}",
                detail="检查后端响应 schema 或 TUI 读取字段是否已同步。",
                next_step="核对后端响应结构与 TUI 解析逻辑。",
            )
        except subprocess.SubprocessError as exc:
            return ValidationResult(
                status=FAILED,
                summary=f"命令执行失败：{exc}",
                detail="检查本地命令是否可用，并查看上方输出。",
                next_step="根据命令输出修复失败项后重试。",
            )

    async def _check_api_health(self) -> ValidationResult:
        payload = await self.client.health()
        return self._format_payload_result(
            "api_health",
            True,
            payload,
            "API 和后端主服务可用。",
            "",
        )

    async def _check_chat_no_tool(self) -> ValidationResult:
        payload = await self.client.chat("你好", timeout=self.chat_validation_timeout)
        return self._format_expected_result(
            "chat_no_tool",
            payload,
            "used_tools",
            [],
            "普通 Chat 未触发工具。",
            "确认 Chat 主链路不应自动调用工具。",
        )

    async def _check_chat_text_stats(self) -> ValidationResult:
        payload = await self.client.chat(
            "请调用 text_stats 工具统计下面文本的字符数、行数和单词数：hello world",
            timeout=self.chat_validation_timeout,
        )
        return self._format_contains_result(
            "chat_text_stats",
            payload,
            "used_tools",
            "text_stats",
            "text_stats 工具触发成功。",
            "检查 Agent 工具选择规则。",
        )

    async def _check_chat_note_search(self) -> ValidationResult:
        payload = await self.client.chat(
            "请调用 note_search 工具，在本地笔记里搜索 demo 这个关键词。",
            timeout=self.chat_validation_timeout,
        )
        return self._format_contains_result(
            "chat_note_search",
            payload,
            "used_tools",
            "note_search",
            "note_search 工具触发成功。",
            "检查笔记工具注册与检索规则。",
        )

    async def _check_memory_roundtrip(self) -> ValidationResult:
        await self.client.chat("请记住：我偏好 FastAPI 示例", timeout=self.chat_validation_timeout)
        memories = await self.client.list_memories(query="FastAPI")
        return self._format_bool_result(
            "memory_roundtrip",
            bool(memories),
            memories,
            "记忆写入后可查询。",
            "检查记忆注入与检索链路。",
        )

    async def _check_run_trace(self) -> ValidationResult:
        payload = await self.client.chat("请调用 text_stats 工具统计 trace check", timeout=self.chat_validation_timeout)
        trace = await self.client.get_run(payload["run_id"])
        return self._format_bool_result(
            "run_trace",
            bool(trace.get("steps")),
            trace,
            "run_id 可查询 trace。",
            "检查 run 记录是否写入并返回 steps。",
        )

    async def _check_claude_config(self) -> ValidationResult:
        payload = await self.client.model_health(live=False)
        if payload.get("provider") != "claude":
            return ValidationResult(
                status=SKIPPED,
                summary="当前 provider 不是 claude，跳过。",
                detail=pretty_json(payload),
                next_step="切换到 claude 后再检查模型配置。",
            )
        configured = payload.get("model") == "claude-opus-4-8" and payload.get("status") in {
            "ok",
            "not_checked",
            "degraded",
        }
        return self._format_bool_result(
            "claude_config",
            configured,
            payload,
            "Claude provider 配置检查通过。",
            "检查模型名、凭证和状态。",
        )

    async def _check_docker_config(self) -> ValidationResult:
        return await self._run_command(["docker", "compose", "config", "--quiet"])

    async def _check_pytest(self) -> ValidationResult:
        return await self._run_command(["uv", "run", "pytest"])

    async def _check_ruff(self) -> ValidationResult:
        return await self._run_command(["uv", "run", "ruff", "check", "."])

    def _format_payload_result(
        self,
        label: str,
        passed: bool,
        payload: Any,
        summary: str,
        next_step: str,
    ) -> ValidationResult:
        return self._format_bool_result(label, passed, payload, summary, next_step)

    def _format_expected_result(
        self,
        label: str,
        payload: dict[str, Any],
        key: str,
        expected: Any,
        summary: str,
        next_step: str,
    ) -> ValidationResult:
        return self._format_bool_result(label, payload.get(key) == expected, payload, summary, next_step)

    def _format_contains_result(
        self,
        label: str,
        payload: dict[str, Any],
        key: str,
        expected: str,
        summary: str,
        next_step: str,
    ) -> ValidationResult:
        return self._format_bool_result(label, expected in payload.get(key, []), payload, summary, next_step)

    def _format_bool_result(
        self,
        label: str,
        passed: bool,
        payload: Any,
        summary: str,
        next_step: str,
    ) -> ValidationResult:
        status = PASSED if passed else FAILED
        detail = short_text(pretty_json(payload), 4000)
        return ValidationResult(status=status, summary=summary, detail=detail, next_step="" if passed else next_step)

    async def _run_command(self, command: list[str]) -> ValidationResult:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await process.communicate()
        text = stdout.decode(errors="replace")
        passed = process.returncode == 0
        summary = f"{' '.join(command)} exited {process.returncode}"
        detail = text or "(no output)"
        next_step = "" if passed else "根据命令输出修复失败项后重试。"
        return ValidationResult(
            status=PASSED if passed else FAILED,
            summary=summary,
            detail=detail,
            next_step=next_step,
        )
