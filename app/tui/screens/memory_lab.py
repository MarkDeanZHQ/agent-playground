from __future__ import annotations

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


class MemoryLabScreen(Screen[None]):
    BINDINGS = [
        ("r", "refresh", "刷新"),
        ("ctrl+n", "create_memory", "新增"),
        ("ctrl+s", "save_memory", "保存"),
        ("ctrl+x", "archive_memory", "归档"),
        ("ctrl+d", "delete_memory", "删除"),
        ("ctrl+u", "restore_memory", "恢复"),
    ]

    def __init__(self, client: AgentPlaygroundClient) -> None:
        super().__init__()
        self.client = client
        self.memories: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="memory-lab"):
            yield Static(
                page_title("Memory Lab｜记忆实验", "检索、管理长期记忆，观察状态、来源与版本变化"),
                id="memory-title",
                classes="page-title",
            )
            yield Static(
                page_shortcuts("Enter 搜索", "Ctrl+N 新增", "Ctrl+S 保存", "Ctrl+D 删除"),
                id="memory-shortcuts",
                classes="page-shortcuts",
            )
            yield Static(
                "准备就绪：输入关键词或 status:<状态> 后按 Enter。",
                id="memory-status",
                classes="page-status",
            )
            with Horizontal(id="memory-panels"):
                with Vertical(id="memory-list-panel", classes="panel"):
                    yield Static("Memories｜长期记忆列表", classes="panel-title")
                    yield ListView(id="memory-list")
                with Vertical(id="memory-detail-panel", classes="panel"):
                    yield Static("Detail｜状态、来源、版本与内容", classes="panel-title")
                    yield CopyableText(language="json", id="memory-detail")
            with Vertical(id="memory-actions"):
                yield Static(
                    "Action｜Ctrl+N 新增 · Ctrl+S 保存编辑 · Ctrl+X 归档 · Ctrl+D 删除 · Ctrl+U 恢复",
                    classes="panel-title",
                )
                yield TextArea(
                    soft_wrap=True,
                    show_line_numbers=False,
                    compact=True,
                    placeholder="输入新增或编辑后的记忆内容",
                    id="memory-editor",
                )
                with Horizontal(id="memory-buttons"):
                    yield Button("New", id="create-memory", variant="primary")
                    yield Button("Save", id="save-memory")
                    yield Button("Archive", id="archive-memory")
                    yield Button("Delete", id="delete-memory", variant="error")
                    yield Button("Restore", id="restore-memory")
            yield Input(
                placeholder="输入关键词检索 Memory，回车刷新；可用 status:active/superseded/archived/deleted 过滤",
                id="memory-query",
            )
            yield ScreenNavBar("memory_lab")

    async def on_mount(self) -> None:
        self.run_worker(self.refresh_memories(), exclusive=True)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "create-memory":
            await self.create_memory()
        elif event.button.id == "save-memory":
            await self.save_memory()
        elif event.button.id == "archive-memory":
            await self.archive_memory()
        elif event.button.id == "delete-memory":
            await self.delete_memory()
        elif event.button.id == "restore-memory":
            await self.restore_memory()

    def action_refresh(self) -> None:
        self.run_worker(self.refresh_memories(), exclusive=True)

    def action_create_memory(self) -> None:
        self.run_worker(self.create_memory(), exclusive=True)

    def action_save_memory(self) -> None:
        self.run_worker(self.save_memory(), exclusive=True)

    def action_archive_memory(self) -> None:
        self.run_worker(self.archive_memory(), exclusive=True)

    def action_delete_memory(self) -> None:
        self.run_worker(self.delete_memory(), exclusive=True)

    def action_restore_memory(self) -> None:
        self.run_worker(self.restore_memory(), exclusive=True)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "memory-query":
            await self.refresh_memories()

    async def refresh_memories(self) -> None:
        status_widget = self.query_one("#memory-status", Static)
        raw_query = self.query_one("#memory-query", Input).value.strip()
        query, status = self._parse_query(raw_query)
        status_widget.update("正在检索记忆...")
        memory_list = self.query_one("#memory-list", ListView)
        detail = self.query_one("#memory-detail", CopyableText)
        await memory_list.clear()
        detail.clear()
        try:
            self.memories = await self.client.list_memories(query=query or None, status=status)
        except httpx.HTTPError as exc:
            self.memories = []
            status_widget.update("检索记忆失败。")
            detail.write(format_http_error(exc))
            return
        for memory in self.memories:
            usage = f"used={memory.get('use_count', 0)}"
            conflict = memory.get("conflict_key") or "no-key"
            label = f"{memory['id']} {memory['status']} {usage} {conflict} {short_text(memory['content'], 40)}"
            await memory_list.append(ListItem(Label(label)))
        if self.memories:
            memory_list.index = 0
            self._show_memory(self.memories[0])
            status_widget.update(f"已找到 {len(self.memories)} 条记忆。")
        else:
            status_widget.update("暂无匹配记忆。")
            self.query_one("#memory-editor", TextArea).load_text("")
            detail.write(
                empty_state(
                    "暂无记忆。" if not raw_query else "未找到匹配记忆。",
                    "可在下方 Editor 输入内容后按 Ctrl+N 手动新增，或按 F2 通过对话写入。",
                    "示例：我偏好 FastAPI 示例",
                )
            )

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id == "memory-list" and event.list_view.index is not None:
            self._show_memory(self.memories[event.list_view.index])

    async def create_memory(self) -> None:
        content = self._editor_content()
        if not content:
            self._set_status("新增失败：Editor 内容不能为空。")
            return
        try:
            memory = await self.client.create_memory(content=content)
        except httpx.HTTPError as exc:
            self._show_error("新增记忆失败。", exc)
            return
        await self.refresh_memories()
        self._set_status(f"已新增记忆：{memory['id']}")

    async def save_memory(self) -> None:
        memory = self._selected_memory()
        if memory is None:
            self._set_status("保存失败：请先选择一条记忆。")
            return
        if memory["status"] == "superseded":
            self._set_status("历史替代记忆只读，不能编辑。")
            return
        content = self._editor_content()
        if not content:
            self._set_status("保存失败：Editor 内容不能为空。")
            return
        try:
            updated = await self.client.update_memory(memory["id"], content=content)
        except httpx.HTTPError as exc:
            self._show_error("保存记忆失败。", exc)
            return
        await self.refresh_memories()
        self._set_status(f"已保存记忆：{updated['id']}")

    async def archive_memory(self) -> None:
        await self._run_memory_action("archive", "归档")

    async def delete_memory(self) -> None:
        await self._run_memory_action("soft_delete", "软删除")

    async def restore_memory(self) -> None:
        await self._run_memory_action("restore", "恢复")

    async def _run_memory_action(self, action: str, label: str) -> None:
        memory = self._selected_memory()
        if memory is None:
            self._set_status(f"{label}失败：请先选择一条记忆。")
            return
        if memory["status"] == "superseded":
            self._set_status("历史替代记忆只读，不能归档、删除或恢复。")
            return
        method = getattr(self.client, f"{action}_memory")
        try:
            updated = await method(memory["id"])
        except httpx.HTTPError as exc:
            self._show_error(f"{label}记忆失败。", exc)
            return
        await self.refresh_memories()
        self._set_status(f"已{label}记忆：{updated['id']} -> {updated['status']}")

    def _show_memory(self, memory: dict[str, Any]) -> None:
        detail = self.query_one("#memory-detail", CopyableText)
        detail.clear()
        detail.write("排序：命中质量优先，其次 importance、使用次数、更新时间")
        detail.write(pretty_json(memory))
        self.query_one("#memory-editor", TextArea).load_text(memory["content"])
        self._set_status("已显示选中记忆详情。")

    def _selected_memory(self) -> dict[str, Any] | None:
        memory_list = self.query_one("#memory-list", ListView)
        if memory_list.index is None:
            return None
        if memory_list.index >= len(self.memories):
            return None
        return self.memories[memory_list.index]

    def _editor_content(self) -> str:
        return self.query_one("#memory-editor", TextArea).text.strip()

    def _show_error(self, message: str, exc: httpx.HTTPError) -> None:
        detail = self.query_one("#memory-detail", CopyableText)
        detail.clear()
        detail.write(format_http_error(exc))
        self._set_status(message)

    def _set_status(self, message: str) -> None:
        self.query_one("#memory-status", Static).update(message)

    def _parse_query(self, raw_query: str) -> tuple[str | None, str | None]:
        status = None
        parts = []
        for part in raw_query.split():
            if part.startswith("status:"):
                status = part.removeprefix("status:") or None
            else:
                parts.append(part)
        return " ".join(parts) or None, status
