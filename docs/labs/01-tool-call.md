# Lab 01: 工具调用闭环

## 你将学到什么

- 工具 schema 如何约束输入。
- 手动工具调用和 Agent 自动工具调用的区别。
- `tool_call` / `tool_result` 如何进入 Run Trace。
- 工具失败如何被包装成可观察结果。

## 先看结果

发送：

```text
请统计 hello world
```

你应该看到：

- 响应里 `used_tools` 包含 `text_stats`。
- Run Trace 里出现 `tool_call` 和 `tool_result`。
- 最终回答包含字符、单词或行数统计结果。

## 背后的最小原理

模型不会直接执行工具。它只返回一个结构化工具调用请求，后端的 `AgentRunner` 再通过 `ToolRegistry` 执行受控工具，并把 `ToolCallResult` 交回模型或写入 trace。

这条边界很重要：工具能力属于后端，不属于模型。把这点搞混，后面安全边界就全乱了。

## 代码入口

- `app/agent/runner.py`：驱动模型请求、工具执行和 trace 记录。
- `app/tools/registry.py`：注册工具并统一执行。
- `app/tools/builtin.py`：内置教学工具。
- `app/schemas/api.py`：`ToolCallRequest` 和 `ToolCallResult`。
- `app/tui/screens/tools_lab.py`：TUI Tools Lab。

## 动手实验

启动服务：

```bash
cd agent-playground
uv run uvicorn app.main:app --reload
```

查看工具列表：

```bash
curl http://127.0.0.1:8000/api/v1/tools
```

手动调用 `text_stats`：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/tools/text_stats/invoke \
  -H "Content-Type: application/json" \
  -d '{"text":"hello world"}'
```

再让 Agent 自动触发工具：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"请统计 hello world"}'
```

拿响应里的 `run_id` 查看 trace：

```bash
curl http://127.0.0.1:8000/api/v1/runs/<run_id>
```

也可以启动 TUI：

```bash
uv run python -m app.tui.main
```

然后按这个顺序观察：

1. `F4` Tools Lab 手动调用工具。
2. `F2` Chat Lab 发送“请统计 hello world”。
3. `F3` Run Trace 查看最近一次 Run。

## 你应该观察什么

- `/tools/{name}/invoke` 是手动工具调用，不会生成 Agent Run。
- `/chat` 是 Agent 自动调用，会生成 `run_id` 和 trace。
- `tool_call` 记录模型想调用什么。
- `tool_result` 记录本地工具实际返回什么。
- 工具参数和返回结果都应能在 trace 里复盘。

## 失败案例

故意调用一个缺少必填参数的工具：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/tools/json_extract/invoke \
  -H "Content-Type: application/json" \
  -d '{"text":"name: Alice"}'
```

在 TUI Tools Lab 里，缺少 `fields` 时会先出现 schema 校验提示；通过 API 调用时，后端也会返回可观察错误。这个设计是为了让学习者看见失败，而不是让服务用 500 糊脸，真那样就该把这段代码拖出去重写。

## 对应测试

- `tests/test_agent_runner.py::test_agent_loop_supports_two_round_tool_calls`
- `tests/test_api.py::test_tools_endpoint_lists_builtin_tools`
- `tests/test_api.py::test_tool_invoke_endpoint_returns_result`
- `tests/test_tui_screens.py`

## 延伸阅读

- [`../02-tool-system.md`](../02-tool-system.md)
- [`../04-trace-observability.md`](../04-trace-observability.md)
- [`../tutorials/01-watch-tool-call.md`](../tutorials/01-watch-tool-call.md)
