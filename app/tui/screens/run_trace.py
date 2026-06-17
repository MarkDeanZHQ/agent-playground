from __future__ import annotations

import httpx
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Label, ListItem, ListView, Static

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


class RunTraceScreen(Screen[None]):
    BINDINGS = [("r", "refresh", "刷新")]

    def __init__(self, client: AgentPlaygroundClient) -> None:
        super().__init__()
        self.client = client
        self.runs: list[dict] = []
        self.steps: list[dict] = []
        self.tool_calls: list[dict] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="run-trace"):
            yield Static(
                page_title("Run Trace｜执行轨迹", "复盘一次 Agent 执行中的 step、summary、tool call 与最终结果"),
                id="run-trace-title",
                classes="page-title",
            )
            yield Static(
                page_shortcuts("方向键选择", "Enter 查看详情", "r 刷新"),
                id="run-trace-shortcuts",
                classes="page-shortcuts",
            )
            yield Static(
                "准备就绪：刷新后选择 Run 或 Step 查看详情。",
                id="run-trace-status",
                classes="page-status",
            )
            with Horizontal(id="run-trace-panels"):
                with Vertical(id="run-runs-panel", classes="panel"):
                    yield Static("Runs｜最近执行记录", classes="panel-title")
                    yield ListView(id="runs-list")
                with Vertical(id="run-steps-panel", classes="panel"):
                    yield Static("Steps｜执行步骤与工具调用", classes="panel-title")
                    yield ListView(id="steps-list")
                with Vertical(id="run-detail-panel", classes="panel"):
                    yield Static("Detail｜选中项 JSON 详情", classes="panel-title")
                    yield CopyableText(language="json", id="detail")
            yield ScreenNavBar("run_trace")

    def on_mount(self) -> None:
        self.run_worker(self.refresh_runs(), exclusive=True)

    def action_refresh(self) -> None:
        self.run_worker(self.refresh_runs(), exclusive=True)

    async def refresh_runs(self) -> None:
        runs_list = self.query_one("#runs-list", ListView)
        steps_list = self.query_one("#steps-list", ListView)
        detail = self.query_one("#detail", CopyableText)
        status = self.query_one("#run-trace-status", Static)
        status.update("正在加载最近 Run...")
        await runs_list.clear()
        await steps_list.clear()
        detail.clear()
        try:
            self.runs = await self.client.list_runs()
        except httpx.HTTPError as exc:
            self.runs = []
            self.steps = []
            self.tool_calls = []
            status.update("加载 Run 失败。")
            detail.write(format_http_error(exc))
            return
        for run in self.runs:
            label = f"{run['id']} {run['status']} tools={run['tool_count']} steps={run['step_count']}"
            await runs_list.append(ListItem(Label(label)))
        if self.runs:
            runs_list.index = 0
            status.update("已刷新。选择左侧 Run 或中间 Step 查看详情。")
            await self.load_run(self.runs[0]["id"])
            return
        self.steps = []
        self.tool_calls = []
        status.update("暂无 Run。")
        detail.write(
            empty_state(
                "暂无执行记录。",
                "按 F2 到 Chat Lab 发送一条消息，然后回到 F3 查看 trace。",
                "请统计 hello world",
            )
        )

    async def load_run(self, run_id: str) -> None:
        detail = self.query_one("#detail", CopyableText)
        steps_list = self.query_one("#steps-list", ListView)
        status = self.query_one("#run-trace-status", Static)
        status.update(f"正在加载 Run：{run_id}")
        await steps_list.clear()
        detail.clear()
        try:
            trace = await self.client.get_run(run_id)
        except httpx.HTTPError as exc:
            self.steps = []
            self.tool_calls = []
            status.update("加载 Run 详情失败。")
            detail.write(format_http_error(exc))
            return
        self.steps = trace.get("steps", [])
        self.tool_calls = trace.get("tool_calls", [])
        for step in self.steps:
            label = f"{step['step_index']} {step['kind']} {short_text(step['content'], 40)}"
            if step["kind"] in {"model_response", "model_tool_use", "model_final"}:
                label = "Claude " + label
            if step["kind"].startswith("session_summary_"):
                label = "Summary " + label
            await steps_list.append(ListItem(Label(label)))
        for tool_call in self.tool_calls:
            status_text = "ERROR" if tool_call["is_error"] else "OK"
            await steps_list.append(ListItem(Label(f"tool {tool_call['name']} {status_text}")))
        detail.write(pretty_json(trace))
        status.update("Run 详情已加载。")

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id == "runs-list" and event.list_view.index is not None:
            await self.load_run(self.runs[event.list_view.index]["id"])
            return
        if event.list_view.id == "steps-list" and event.list_view.index is not None:
            index = event.list_view.index
            detail = self.query_one("#detail", CopyableText)
            status = self.query_one("#run-trace-status", Static)
            detail.clear()
            if index < len(self.steps):
                detail.write(pretty_json(self.steps[index]))
                status.update("已显示选中 Step。")
            else:
                detail.write(pretty_json(self.tool_calls[index - len(self.steps)]))
                status.update("已显示选中 Tool Call。")
