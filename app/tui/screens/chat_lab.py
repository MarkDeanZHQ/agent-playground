from __future__ import annotations

import asyncio
import json
import time

import httpx
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Input, RichLog, Static

from app.tui.client import AgentPlaygroundClient, SseEvent
from app.tui.widgets import ScreenNavBar, format_http_error, page_shortcuts, page_title, pretty_json


class ChatLabScreen(Screen[None]):
    BINDINGS = [
        ("t", "show_last_run", "最近 Trace"),
        ("escape", "cancel_run", "取消请求"),
        ("ctrl+c", "cancel_run", "取消请求"),
    ]

    def __init__(self, client: AgentPlaygroundClient) -> None:
        super().__init__()
        self.client = client
        self.session_id: str | None = None
        self.last_run_id: str | None = None
        self.current_worker: object | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="chat-lab"):
            yield Static(
                page_title("Chat Lab｜对话实验", "发送消息，观察 Agent Loop、工具调用、记忆注入、会话摘要和流式输出"),
                id="chat-title",
                classes="page-title",
            )
            yield Static(
                page_shortcuts("Enter 发送", "Esc/Ctrl+C 取消", "t 查看最近 Trace"),
                id="chat-shortcuts",
                classes="page-shortcuts",
            )
            yield Static(
                "准备就绪：输入消息后按 Enter。",
                id="chat-status",
                classes="page-status",
            )
            with Horizontal(id="chat-panels"):
                with Vertical(id="conversation-panel", classes="panel"):
                    yield Static("Conversation｜用户与 Agent 的可读对话", classes="panel-title")
                    yield RichLog(id="conversation", wrap=True, highlight=True)
                with Vertical(id="live-trace-panel", classes="panel"):
                    yield Static("Live Trace｜模型请求、工具调用、记忆、摘要和延迟事件", classes="panel-title")
                    yield RichLog(id="live-trace", wrap=True, highlight=True)
            yield Input(placeholder="输入消息后按 Enter，例如：请统计 hello world", id="chat-input")
            yield ScreenNavBar("chat_lab")

    def on_mount(self) -> None:
        self.query_one("#chat-input", Input).focus()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        message = event.value.strip()
        if not message:
            return
        event.input.value = ""
        self.query_one("#conversation", RichLog).write(f"User: {message}")
        self.current_worker = self.run_worker(self.send_message(message), exclusive=True)

    async def send_message(self, message: str) -> None:
        conversation = self.query_one("#conversation", RichLog)
        trace = self.query_one("#live-trace", RichLog)
        status = self.query_one("#chat-status", Static)
        state: dict[str, object] = {
            "final_text": "",
            "used_tools": [],
            "used_memories": [],
            "error_text": "",
            "started_at": time.perf_counter(),
            "first_token_at": None,
            "completed_at": None,
        }
        status.update("正在连接 API 并发送消息...")
        try:
            async for event in self.client.stream_chat(message, self.session_id):
                trace.write(f"[{event.event}] {pretty_json(event.data)}")
                self._capture_event_state(event, state)
                self._write_observable_event(conversation, event, state)
        except asyncio.CancelledError:
            state["error_text"] = "Run cancelled by user."
            status.update("已取消当前请求。")
            trace.write("[stream_cancelled] Run cancelled by user.")
        except httpx.HTTPError as exc:
            message = format_http_error(exc)
            state["error_text"] = message
            status.update("API 请求失败，详情见 Live Trace。")
            trace.write(f"[stream_error] {message}")
        except json.JSONDecodeError as exc:
            message = f"流式响应解析失败：{exc.__class__.__name__}: {exc}"
            state["error_text"] = message
            status.update("流式响应解析失败，详情见 Live Trace。")
            trace.write(f"[stream_error] {message}")
        finally:
            state["completed_at"] = time.perf_counter()
            self.current_worker = None
        self._write_message_summary(conversation, state)
        if not state.get("error_text"):
            status.update("已完成。按 t 查看最近一次 Run Trace，或继续输入消息。")

    def _capture_event_state(self, event: SseEvent, state: dict[str, object]) -> None:
        if self.session_id is None and event.data.get("session_id"):
            self.session_id = str(event.data["session_id"])
        if event.data.get("run_id"):
            self.last_run_id = str(event.data["run_id"])
        if event.event == "message_delta":
            if state["first_token_at"] is None:
                state["first_token_at"] = time.perf_counter()
            state["final_text"] = str(state["final_text"]) + str(event.data.get("text", ""))
        if event.event == "memory_used":
            state["used_memories"] = [str(item) for item in event.data.get("memory_ids", [])]
        if event.event == "tool_result":
            used_tools = state["used_tools"]
            assert isinstance(used_tools, list)
            used_tools.append(str(event.data.get("name")))
        if event.event in {"model_error", "stream_error"}:
            state["error_text"] = str(event.data.get("message") or event.data.get("detail") or "流式响应中断")

    def _write_observable_event(
        self,
        conversation: RichLog,
        event: SseEvent,
        state: dict[str, object],
    ) -> None:
        status = self.query_one("#chat-status", Static)
        if event.event == "model_request":
            status.update("正在等待模型首个 token...")
        if event.event == "message_delta" and state.get("first_token_at") is not None:
            status.update("正在接收模型流式输出...")
        if event.event == "tool_call":
            tool_name = event.data.get("name") or event.data.get("tool_name") or "unknown"
            status.update(f"正在调用工具：{tool_name}")
        if event.event == "memory_used":
            conversation.write(f"Memory used: {pretty_json(event.data.get('memories', []))}")
        if event.event == "session_summary_used":
            covered = event.data.get("covered_message_count")
            chars = event.data.get("summary_chars")
            conversation.write(f"Session summary used: covered={covered} summary_chars={chars}")
        if event.event == "context_built":
            trace = event.data.get("context_trace", {})
            conversation.write(f"Context blocks: {pretty_json(trace)}")
        if event.event == "tool_result":
            conversation.write(f"Tool result: {event.data.get('name')} error={event.data.get('is_error')}")
        if event.event == "latency_metric":
            first = event.data.get("time_to_first_token_ms")
            total = event.data.get("total_run_duration_ms")
            conversation.write(f"Latency: first_token={self._format_ms(first)} total={self._format_ms(total)}")
        if event.event == "run_finished":
            status.update("正在写入 Trace 和记忆...")
        if event.event in {"model_error", "stream_error"}:
            status.update("模型或流式响应失败，详情见对话与 Trace。")
            conversation.write(f"Error: {state['error_text']}")

    def _write_message_summary(self, conversation: RichLog, state: dict[str, object]) -> None:
        used_tools = state["used_tools"]
        used_memories = state["used_memories"]
        if isinstance(used_tools, list) and used_tools:
            conversation.write(f"Used tools: {', '.join(used_tools)}")
        if isinstance(used_memories, list) and used_memories:
            conversation.write(f"Used memories: {', '.join(used_memories)}（按 F5 到 Memory Lab 查看详情）")
        first_token_at = state.get("first_token_at")
        started_at = state.get("started_at")
        completed_at = state.get("completed_at")
        if isinstance(started_at, float) and isinstance(completed_at, float):
            first = first_token_at - started_at if isinstance(first_token_at, float) else None
            total = completed_at - started_at
            first_text = f"{first:.2f}s" if first is not None else "n/a"
            conversation.write(f"Latency: first_token={first_text} total={total:.2f}s")
        error_text = str(state.get("error_text", ""))
        final_text = str(state["final_text"])
        if error_text:
            if final_text:
                conversation.write(f"Agent(partial): {final_text}")
            conversation.write(f"Agent failed: {error_text}")
            return
        conversation.write(f"Agent: {final_text}")

    def action_cancel_run(self) -> None:
        if self.current_worker is None:
            return
        cancel = getattr(self.current_worker, "cancel", None)
        if callable(cancel):
            cancel()
            self.query_one("#chat-status", Static).update("正在取消当前请求...")

    def action_show_last_run(self) -> None:
        self.app.switch_screen("run_trace")
        if not self.last_run_id:
            return
        screen = self.app.screen
        if hasattr(screen, "load_run"):
            screen.run_worker(screen.load_run(self.last_run_id), exclusive=True)

    def _format_ms(self, value: object) -> str:
        if isinstance(value, int | float):
            return f"{value / 1000:.2f}s"
        return "n/a"
