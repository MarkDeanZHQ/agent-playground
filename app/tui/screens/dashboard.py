from __future__ import annotations

import time

import httpx
from textual.app import ComposeResult
from textual.containers import Container
from textual.screen import Screen
from textual.widgets import Static

from app.core.config import get_settings
from app.tui.client import AgentPlaygroundClient
from app.tui.widgets import CopyableText, ScreenNavBar, format_http_error, page_shortcuts, page_title


class DashboardScreen(Screen[None]):
    BINDINGS = [("r", "refresh", "刷新"), ("l", "live_model_check", "真实模型检查")]

    def __init__(self, client: AgentPlaygroundClient) -> None:
        super().__init__()
        self.client = client

    def compose(self) -> ComposeResult:
        with Container(id="dashboard"):
            yield Static(
                page_title("Dashboard｜总览", "查看 API、模型、工具、记忆与最近 Run 状态"),
                id="dashboard-title",
                classes="page-title",
            )
            yield Static(
                page_shortcuts("r 刷新", "l 真实模型检查", "F2 开始对话"),
                id="dashboard-shortcuts",
                classes="page-shortcuts",
            )
            yield Static(
                "准备就绪：l 会请求真实模型供应商，可能产生 token 成本或触发限流。",
                id="dashboard-status",
                classes="page-status",
            )
            yield CopyableText(id="dashboard-log")
            yield ScreenNavBar("dashboard")

    def on_mount(self) -> None:
        self.run_worker(self.refresh_dashboard(), exclusive=True)

    def action_refresh(self) -> None:
        self.run_worker(self.refresh_dashboard(), exclusive=True)

    def action_live_model_check(self) -> None:
        self.run_worker(self.refresh_dashboard(live_model_check=True), exclusive=True)

    async def refresh_dashboard(self, live_model_check: bool = False) -> None:
        log = self.query_one("#dashboard-log", CopyableText)
        status = self.query_one("#dashboard-status", Static)
        log.clear()
        status.update("正在请求真实模型健康检查..." if live_model_check else "正在连接 API...")
        settings = get_settings()
        model_status = "unknown"
        model_message = ""
        model_check_mode = "static"
        live_duration_seconds: float | None = None
        try:
            health = await self.client.health()
            started_at = time.perf_counter()
            model_health = await self.client.model_health(live=live_model_check)
            if live_model_check:
                live_duration_seconds = time.perf_counter() - started_at
            runs = await self.client.list_runs(limit=1)
            memories = await self.client.list_memories()
            tools = await self.client.list_tools()
            api_status = health.get("status", "unknown")
            model_status = f"{model_health.get('provider')}:{model_health.get('status')}"
            model_message = str(model_health.get("message") or "")
            model_check_mode = "live" if model_health.get("live") else "static"
            run_count = len(runs)
            memory_count = len(memories)
            tool_count = len(tools)
            status.update("已刷新")
        except httpx.HTTPError as exc:
            api_status = "failed"
            model_message = format_http_error(exc)
            run_count = 0
            memory_count = 0
            tool_count = 0
            status.update("API 请求失败，详情见下方日志。")

        log.write(f"API URL: {self.client.base_url}")
        log.write(f"API Status: {api_status}")
        log.write(f"Provider: {settings.model_provider}")
        if settings.model_provider == "claude":
            credential_status = "configured" if settings.anthropic_api_key else "SDK env/profile"
            log.write(f"Claude Model: {settings.claude_model}")
            log.write(f"Claude Credentials: {credential_status}")
            log.write(f"Max Tokens: {settings.claude_max_tokens}")
        if settings.model_provider == "openai":
            credential_status = "configured" if settings.openai_api_key else "SDK env/profile"
            log.write(f"OpenAI Model: {settings.openai_model}")
            log.write(f"OpenAI Credentials: {credential_status}")
            log.write(f"Max Tokens: {settings.openai_max_tokens}")
            log.write(f"OpenAI Protocol Mode: {settings.effective_openai_protocol_mode}")
            log.write(f"OpenAI Tool Calling Enabled: {settings.openai_tool_calling}")
        log.write(f"LLM Timeout: {settings.llm_timeout_seconds:g}s")
        log.write(f"Max Retries: {settings.llm_max_retries}")
        log.write(f"Model Health: {model_status} ({model_check_mode})")
        if live_duration_seconds is not None:
            log.write(f"Live Check Duration: {live_duration_seconds:.2f}s")
        if model_message:
            log.write(f"Model Health Detail:\n{model_message}")
        if model_health.get("tool_calling_status"):
            log.write(
                "Tool Calling Health: "
                f"{model_health.get('tool_calling_status')} "
                f"(enabled={model_health.get('tool_calling_enabled')})"
            )
        if model_health.get("tool_calling_message"):
            log.write(f"Tool Calling Detail:\n{model_health.get('tool_calling_message')}")
        log.write(f"Runs: {run_count}")
        log.write(f"Memories: {memory_count}")
        log.write(f"Tools: {tool_count}")
