from __future__ import annotations

import json
from typing import Any

import httpx
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, TextArea

NAV_ITEMS = [
    ("f1", "dashboard", "Dashboard"),
    ("f2", "chat_lab", "Chat"),
    ("f3", "run_trace", "Trace"),
    ("f4", "tools_lab", "Tools"),
    ("f5", "memory_lab", "Memory"),
    ("f6", "validation_lab", "Validation"),
]


def short_text(value: str | None, limit: int = 80) -> str:
    if not value:
        return ""
    return value if len(value) <= limit else value[: limit - 1] + "…"


def format_token_usage(payload: dict[str, Any] | None) -> str:
    if not payload:
        return "n/a"
    usage_summary = payload.get("usage_summary")
    estimated_cost = payload.get("estimated_cost")
    parts: list[str] = []
    if isinstance(usage_summary, dict):
        parts.append(
            "usage "
            + " ".join(
                [
                    f"input={usage_summary.get('input_tokens', 0)}",
                    f"output={usage_summary.get('output_tokens', 0)}",
                    f"total={usage_summary.get('total_tokens', 0)}",
                ]
            )
        )
    if isinstance(estimated_cost, dict) and estimated_cost.get("total_cost") is not None:
        parts.append(f"cost~${float(estimated_cost['total_cost']):.6f}")
    return " | ".join(parts) if parts else "n/a"


def format_error_info(payload: dict[str, Any] | None) -> str:
    if not payload:
        return "n/a"
    code = payload.get("code", "unknown")
    retryable = payload.get("retryable")
    suggestion = payload.get("suggestion")
    parts = [f"{code}", f"retryable={str(bool(retryable)).lower()}"]
    if suggestion:
        parts.append(str(suggestion))
    return " | ".join(parts)


def pretty_json(value: Any) -> str:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return value
    return json.dumps(value, ensure_ascii=False, indent=2)


def page_title(title: str, description: str | None = None) -> str:
    return f"{title}\n{description}" if description else title


def page_shortcuts(*shortcuts: str) -> str:
    return "本页快捷键：" + " · ".join(shortcuts)


class CopyableText(TextArea):
    def __init__(
        self,
        text: str = "",
        *,
        language: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(
            text,
            language=language,
            soft_wrap=True,
            read_only=True,
            show_cursor=False,
            show_line_numbers=False,
            highlight_cursor_line=False,
            id=id,
            classes=classes,
        )

    def clear(self) -> None:
        self.load_text("")

    def write(self, text: object) -> None:
        next_text = str(text)
        if self.text:
            next_text = f"{self.text}\n{next_text}"
        self.load_text(next_text)
        self.scroll_end(animate=False)


class ScreenNavBar(Horizontal):
    def __init__(self, active_screen: str) -> None:
        super().__init__(id="screen-nav")
        self.active_screen = active_screen

    def compose(self) -> ComposeResult:
        for key, screen_name, label in NAV_ITEMS:
            classes = "screen-nav-item"
            if screen_name == self.active_screen:
                classes += " active"
            yield Button(
                f"{key.upper()} {label}",
                id=f"nav-{screen_name}",
                classes=classes,
                compact=True,
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if not event.button.id or not event.button.id.startswith("nav-"):
            return
        event.stop()
        self.app.switch_screen(event.button.id.removeprefix("nav-"))


def empty_state(message: str, next_step: str, example: str | None = None) -> str:
    parts = [message, f"下一步：{next_step}"]
    if example:
        parts.append(f"示例：{example}")
    return "\n".join(parts)


def format_http_error(exc: httpx.HTTPError) -> str:
    if isinstance(exc, httpx.ConnectError):
        return (
            "无法连接 API：请先启动后端服务\n\n"
            "推荐命令：\n"
            "cd agent-playground\n"
            "uv run uvicorn app.main:app --reload\n\n"
            f"原始错误：{exc.__class__.__name__}: {exc}"
        )
    if isinstance(exc, httpx.ReadTimeout):
        return f"请求超时：API 已连接，但响应时间过长。\n原始错误：{exc.__class__.__name__}: {exc}"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"API 返回错误状态：{exc.response.status_code}\n原始错误：{exc.__class__.__name__}: {exc}"
    return f"请求失败：{exc.__class__.__name__}: {exc}"
