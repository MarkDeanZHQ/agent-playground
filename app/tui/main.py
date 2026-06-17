from __future__ import annotations

from textual.app import App, ComposeResult
from textual.widgets import Footer, Header

from app.tui.client import AgentPlaygroundClient
from app.tui.screens.chat_lab import ChatLabScreen
from app.tui.screens.dashboard import DashboardScreen
from app.tui.screens.memory_lab import MemoryLabScreen
from app.tui.screens.run_trace import RunTraceScreen
from app.tui.screens.tools_lab import ToolsLabScreen
from app.tui.screens.validation_lab import ValidationLabScreen


class AgentPlaygroundTui(App[None]):
    TITLE = "Agent Playground"
    SUB_TITLE = "可观察的 Agent 学习控制台"

    CSS = """
    #dashboard, #chat-lab, #run-trace, #tools-lab, #memory-lab, #validation-lab { height: 100%; }
    #chat-panels, #run-trace-panels, #tools-panels, #memory-panels, #validation-panels { height: 1fr; }
    .page-title { content-align: center middle; text-style: bold; height: 2; }
    .page-shortcuts { height: 1; color: $accent; text-style: dim; }
    .page-status { height: 1; color: $accent; }
    #screen-nav {
        dock: bottom;
        height: 3;
        border-top: solid $accent;
        background: $surface;
        padding: 0 1;
    }
    #screen-nav Button {
        width: 1fr;
        min-width: 13;
        height: 1;
        margin: 1 0;
        border: none;
    }
    #screen-nav Button.active {
        text-style: bold reverse;
        background: $accent;
        color: $text;
    }
    .panel { height: 1fr; }
    .panel-title { height: 1; text-style: bold; color: $accent; }
    #conversation-panel, #live-trace-panel, #tool-detail-panel,
    #memory-detail-panel, #validation-output-panel { width: 1fr; }
    #run-runs-panel, #run-steps-panel { width: 1fr; }
    #run-detail-panel { width: 2fr; }
    #tools-list-panel { width: 30; min-width: 24; }
    #tools-actions { height: 20; border: solid $accent; padding: 0 1; }
    #tool-form { height: 7; }
    #tool-args { width: 1fr; height: 7; }
    #invoke-tool { width: 24; height: 3; min-width: 20; }
    #chat-input, #memory-query { height: 3; }
    #memory-actions { height: 12; border: solid $accent; padding: 0 1; }
    #memory-editor { height: 5; }
    #memory-buttons { height: 3; }
    #memory-buttons Button { width: 1fr; min-width: 10; }
    #conversation, #live-trace, #tool-detail, #detail, #memory-detail, #validation-output {
        height: 1fr;
        border: solid $accent;
        padding: 0 1;
    }
    CopyableText {
        height: 1fr;
        border: solid $accent;
        padding: 0 1;
    }
    #runs-list, #steps-list, #memory-list, #validation-list, #tools-list {
        height: 1fr;
        border: solid $accent;
    }
    #dashboard-log { height: 1fr; border: solid $accent; padding: 1; }
    #tool-result { height: 1fr; border: none; padding: 0 1; }
    """
    BINDINGS = [
        ("f1", "show_dashboard", "Dashboard/总览"),
        ("f2", "show_chat", "Chat/对话"),
        ("f3", "show_trace", "Trace/轨迹"),
        ("f4", "show_tools", "Tools/工具"),
        ("f5", "show_memory", "Memory/记忆"),
        ("f6", "show_validation", "Validation/验收"),
        ("q", "quit", "退出"),
    ]

    def __init__(self, base_url: str = "http://127.0.0.1:8000") -> None:
        super().__init__()
        self.client = AgentPlaygroundClient(base_url)

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()

    def on_mount(self) -> None:
        self.install_screen(DashboardScreen(self.client), "dashboard")
        self.install_screen(ChatLabScreen(self.client), "chat_lab")
        self.install_screen(RunTraceScreen(self.client), "run_trace")
        self.install_screen(ToolsLabScreen(self.client), "tools_lab")
        self.install_screen(MemoryLabScreen(self.client), "memory_lab")
        self.install_screen(ValidationLabScreen(self.client), "validation_lab")
        self.push_screen("dashboard")

    def action_show_dashboard(self) -> None:
        self.switch_screen("dashboard")

    def action_show_chat(self) -> None:
        self.switch_screen("chat_lab")

    def action_show_trace(self) -> None:
        self.switch_screen("run_trace")

    def action_show_tools(self) -> None:
        self.switch_screen("tools_lab")

    def action_show_memory(self) -> None:
        self.switch_screen("memory_lab")

    def action_show_validation(self) -> None:
        self.switch_screen("validation_lab")


def main() -> None:
    AgentPlaygroundTui().run()


if __name__ == "__main__":
    main()
