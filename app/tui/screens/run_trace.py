from __future__ import annotations

import json

import httpx
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Checkbox, Input, Label, ListItem, ListView, Static

from app.tui.client import AgentPlaygroundClient
from app.tui.widgets import (
    CopyableText,
    ScreenNavBar,
    empty_state,
    format_error_info,
    format_http_error,
    format_token_usage,
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
        self.filtered_runs: list[dict] = []
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
            with Horizontal(id="run-trace-filters"):
                yield Input(placeholder="搜索 run id / session id / final answer", id="run-search")
                yield Checkbox("只看失败 run", id="failed-only")
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
            status_filter = "failed" if self.query_one("#failed-only", Checkbox).value else None
            self.runs = await self.client.list_runs(limit=50, status=status_filter)
        except httpx.HTTPError as exc:
            self.runs = []
            self.filtered_runs = []
            self.steps = []
            self.tool_calls = []
            status.update("加载 Run 失败。")
            detail.write(format_http_error(exc))
            return
        self.filtered_runs = self._filter_runs(self.runs)
        for run in self.filtered_runs:
            label = f"{run['id']} {run['status']} tools={run['tool_count']} steps={run['step_count']}"
            if run.get("duration_ms") is not None:
                label += f" duration={run['duration_ms']}ms"
            label += f" created={run['created_at']}"
            await runs_list.append(ListItem(Label(label)))
        if self.filtered_runs:
            runs_list.index = 0
            status.update("已刷新。选择左侧 Run 或中间 Step 查看详情。")
            await self.load_run(self.filtered_runs[0]["id"])
            return
        self.steps = []
        self.tool_calls = []
        status.update("没有匹配的 Run。")
        detail.write(
            empty_state(
                "暂无匹配执行记录。",
                "按 F2 到 Chat Lab 发送一条消息，或调整搜索与失败筛选。",
                "请统计 hello world",
            )
        )

    def _filter_runs(self, runs: list[dict]) -> list[dict]:
        query = self.query_one("#run-search", Input).value.strip().lower()
        if not query:
            return runs
        return [
            run
            for run in runs
            if query in str(run.get("id", "")).lower()
            or query in str(run.get("session_id", "")).lower()
            or query in str(run.get("final_answer", "")).lower()
        ]

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
            payload = self._parse_step_payload(step)
            if step["kind"] == "token_usage":
                label = f"{step['step_index']} Usage {format_token_usage(payload)}"
            if step["kind"] == "model_error":
                label = f"{step['step_index']} Error {format_error_info((payload or {}).get('error_info'))}"
            if step["kind"] in {"model_response", "model_tool_use", "model_final"}:
                label = "Claude " + label
            if step["kind"].startswith("session_summary_"):
                label = "Summary " + label
            if step["kind"] == "memory_policy_decision":
                label = "Memory Decision " + self._summary_line(self._memory_decision_summary(payload))
            if step["kind"] == "memory_saved":
                label = "Memory Saved " + self._summary_line(self._memory_saved_summary(payload))
            if step["kind"] == "memory_superseded":
                label = "Memory Superseded " + self._summary_line(self._memory_superseded_summary(payload))
            if step["kind"] == "memory_skipped":
                label = "Memory Skipped " + self._summary_line(self._memory_skipped_summary(payload))
            if step["kind"] == "memory_retrieved":
                label = "Memory Retrieval " + self._summary_line(self._memory_retrieval_summary(payload))
            if step["kind"] == "context_built":
                label = "Context Budget " + self._summary_line(self._context_trace_summary(payload))
            await steps_list.append(ListItem(Label(label)))
        for tool_call in self.tool_calls:
            status_text = "ERROR" if tool_call["is_error"] else "OK"
            await steps_list.append(ListItem(Label(f"tool {tool_call['name']} {status_text}")))
        detail.write(pretty_json(trace))
        status.update("Run 详情已加载。")

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id == "runs-list" and event.list_view.index is not None:
            await self.load_run(self.filtered_runs[event.list_view.index]["id"])
            return
        if event.list_view.id == "steps-list" and event.list_view.index is not None:
            index = event.list_view.index
            detail = self.query_one("#detail", CopyableText)
            status = self.query_one("#run-trace-status", Static)
            detail.clear()
            if index < len(self.steps):
                step = self.steps[index]
                payload = self._parse_step_payload(step)
                if step["kind"] == "token_usage" and payload is not None:
                    detail.write(
                        pretty_json(
                            {
                                "usage_summary": payload.get("usage_summary"),
                                "estimated_cost": payload.get("estimated_cost"),
                                "cost_notice": payload.get("cost_notice"),
                                "raw_usage": payload.get("usage"),
                                "finish_reason": payload.get("finish_reason"),
                            }
                        )
                    )
                elif step["kind"] == "model_error" and payload is not None:
                    detail.write(
                        pretty_json(
                            {
                                "message": payload.get("message"),
                                "error_info": payload.get("error_info"),
                            }
                        )
                    )
                elif step["kind"] == "memory_policy_decision":
                    detail.write(
                        self._structured_detail(
                            "Memory Decision",
                            self._memory_decision_summary(payload),
                            payload,
                        )
                    )
                elif step["kind"] == "memory_saved":
                    detail.write(
                        self._structured_detail(
                            "Memory Saved",
                            self._memory_saved_summary(payload),
                            payload,
                        )
                    )
                elif step["kind"] == "memory_superseded":
                    detail.write(
                        self._structured_detail(
                            "Memory Superseded",
                            self._memory_superseded_summary(payload),
                            payload,
                        )
                    )
                elif step["kind"] == "memory_skipped":
                    detail.write(
                        self._structured_detail(
                            "Memory Skipped",
                            self._memory_skipped_summary(payload),
                            payload,
                        )
                    )
                elif step["kind"] == "memory_retrieved":
                    detail.write(
                        self._structured_detail(
                            "Memory Retrieval",
                            self._memory_retrieval_summary(payload),
                            payload,
                        )
                    )
                elif step["kind"] == "context_built":
                    detail.write(
                        self._structured_detail(
                            "Context Budget",
                            self._context_trace_summary(payload),
                            payload,
                        )
                    )
                else:
                    detail.write(pretty_json(step))
                status.update("已显示选中 Step。")
            else:
                detail.write(pretty_json(self.tool_calls[index - len(self.steps)]))
                status.update("已显示选中 Tool Call。")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "run-search":
            self.run_worker(self.refresh_runs(), exclusive=True)

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id == "failed-only":
            self.run_worker(self.refresh_runs(), exclusive=True)

    def _parse_step_payload(self, step: dict) -> dict | None:
        content = step.get("content")
        if not isinstance(content, str):
            return None
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def _summary_line(self, lines: list[str]) -> str:
        return lines[0] if lines else ""

    def _structured_detail(self, title: str, lines: list[str], payload: dict | None) -> str:
        parts = [title]
        parts.extend(lines)
        if payload:
            parts.append("")
            parts.append(pretty_json(payload))
        return "\n".join(parts)

    def _memory_decision_summary(self, payload: dict | None) -> list[str]:
        if not payload:
            return []
        conflict = payload.get("conflict_decision") if isinstance(payload.get("conflict_decision"), dict) else {}
        lines = [
            f"should_store={payload.get('should_store')}",
            f"resolution={conflict.get('resolution')}",
            f"outcome={conflict.get('outcome')}",
            f"conflict_key={conflict.get('conflict_key')}",
            f"candidate_ids={conflict.get('candidate_ids', [])}",
            f"superseded_ids={conflict.get('superseded_ids', [])}",
            f"saved_memory_id={payload.get('saved_memory_id')}",
            f"supersedes_memory_id={payload.get('supersedes_memory_id')}",
            f"reason={conflict.get('reason')}",
        ]
        return [line for line in lines if not line.endswith("=None") and not line.endswith("=[]")]

    def _memory_saved_summary(self, payload: dict | None) -> list[str]:
        if not payload:
            return []
        lines = [
            f"memory_id={payload.get('memory_id')}",
            f"conflict_resolution={payload.get('conflict_resolution')}",
            f"conflict_outcome={payload.get('conflict_outcome')}",
            f"supersedes_memory_id={payload.get('supersedes_memory_id')}",
            f"reason={payload.get('reason')}",
        ]
        return [line for line in lines if not line.endswith("=None")]

    def _memory_superseded_summary(self, payload: dict | None) -> list[str]:
        if not payload:
            return []
        lines = [
            f"memory_id={payload.get('memory_id')}",
            f"superseded_memory_id={payload.get('superseded_memory_id')}",
            f"resolution={payload.get('resolution')}",
            f"outcome={payload.get('outcome')}",
            f"reason={payload.get('reason')}",
        ]
        return [line for line in lines if not line.endswith("=None")]

    def _memory_skipped_summary(self, payload: dict | None) -> list[str]:
        if not payload:
            return []
        lines = [f"reason={payload.get('reason')}"]
        return [line for line in lines if not line.endswith("=None")]

    def _memory_retrieval_summary(self, payload: dict | None) -> list[str]:
        if not payload:
            return []
        matches = payload.get("matches") if isinstance(payload.get("matches"), list) else []
        lines = [
            f"query={payload.get('query')}",
            f"terms={payload.get('terms')}",
            f"count={len(matches)}",
        ]
        for match in matches[:3]:
            if not isinstance(match, dict):
                continue
            lines.append(
                f"- {match.get('memory_id')} score={match.get('score')} "
                f"scope={match.get('scope')} conflict_key={match.get('conflict_key')}"
            )
        return [line for line in lines if not line.endswith("=None")]

    def _context_trace_summary(self, payload: dict | None) -> list[str]:
        if not payload:
            return []
        context_trace = payload.get("context_trace") if isinstance(payload.get("context_trace"), dict) else {}
        blocks = context_trace.get("blocks") if isinstance(context_trace.get("blocks"), list) else []
        lines = [
            f"budget_unit={context_trace.get('budget_unit')}",
            f"total_budget_chars={context_trace.get('total_budget_chars')}",
            f"total_original_chars={context_trace.get('total_original_chars')}",
            f"total_final_chars={context_trace.get('total_final_chars')}",
            f"trimmed_blocks={context_trace.get('trimmed_blocks')}",
            f"dropped_blocks={context_trace.get('dropped_blocks')}",
        ]
        for block in blocks[:4]:
            if not isinstance(block, dict):
                continue
            lines.append(
                f"- {block.get('name')} source={block.get('source')} "
                f"final={block.get('final_chars')} decision={block.get('decision')}"
            )
        return [line for line in lines if not line.endswith("=None")]
