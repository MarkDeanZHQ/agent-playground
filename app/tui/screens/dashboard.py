from __future__ import annotations

import time

import httpx
from textual.app import ComposeResult
from textual.containers import Container
from textual.screen import Screen
from textual.widgets import Static

from app.core.config import get_settings
from app.tui.client import AgentPlaygroundClient
from app.tui.widgets import (
    CopyableText,
    ScreenNavBar,
    format_error_info,
    format_http_error,
    format_token_usage,
    page_shortcuts,
    page_title,
)


def format_dashboard_latest_run(latest_run: dict[str, object] | None) -> list[str]:
    if not isinstance(latest_run, dict):
        return ["Latest Run: n/a"]
    lines = [
        f"Run ID: {latest_run.get('id')}",
        f"Status: {latest_run.get('status')}",
        f"Created: {latest_run.get('created_at')}",
    ]
    if latest_run.get("finished_at"):
        lines.append(f"Finished: {latest_run.get('finished_at')}")
    if latest_run.get("duration_ms") is not None:
        lines.append(f"Duration: {latest_run.get('duration_ms')}ms")
    return lines


def format_dashboard_model_error(detail: dict[str, object] | None) -> list[str]:
    if not isinstance(detail, dict):
        return []
    label = "Latest Run Failed With Model Error" if detail.get("is_latest_run") else "Latest Historical Model Error"
    type_code = "/".join(
        str(value)
        for value in [detail.get("error_type"), detail.get("error_code")]
        if value
    )
    header_parts = [str(detail.get("created_at") or "time=n/a"), str(detail.get("run_id") or "run=n/a")]
    if type_code:
        header_parts.append(type_code)
    lines = [f"{label}:", " ".join(header_parts)]
    if not detail.get("is_latest_run"):
        lines.append("历史错误，不代表当前模型健康状态。")
    lines.append(str(detail.get("message") or ""))
    return lines


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
        model_health: dict[str, object] = {}
        live_duration_seconds: float | None = None
        run_stats: dict[str, object] = {}
        try:
            health = await self.client.health()
            started_at = time.perf_counter()
            model_health = await self.client.model_health(live=live_model_check)
            if live_model_check:
                live_duration_seconds = time.perf_counter() - started_at
            runs = await self.client.list_runs(limit=1)
            run_stats = await self.client.dashboard_run_stats()
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

        log.write("Current API")
        log.write(f"API URL: {self.client.base_url}")
        log.write(f"API Status: {api_status}")
        log.write("")
        log.write("Current Model")
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
        log.write("")
        log.write("Latest Run")
        for line in format_dashboard_latest_run(run_stats.get("latest_run") if run_stats else None):
            log.write(line)
        log.write("")
        log.write("Recent History")
        log.write(f"Runs: {run_count}")
        if run_stats:
            log.write(f"Recent Run Sample Size: {run_stats.get('sample_size')}")
            log.write(f"Recent Failed Runs: {run_stats.get('failed_runs')}")
            log.write(f"Recent Average Duration: {run_stats.get('average_duration_ms')}ms")
            if run_stats.get("latest_usage_summary") or run_stats.get("latest_estimated_cost"):
                log.write(f"Latest Usage: {format_token_usage(run_stats)}")
            if run_stats.get("latest_error_info"):
                log.write(f"Latest Provider Error: {format_error_info(run_stats.get('latest_error_info'))}")
            if run_stats.get("latest_cost_notice"):
                log.write(f"Cost Notice: {run_stats.get('latest_cost_notice')}")
            for line in format_dashboard_model_error(run_stats.get("latest_model_error_detail")):
                log.write(line)
        log.write(f"Memories: {memory_count}")
        log.write(f"Tools: {tool_count}")
