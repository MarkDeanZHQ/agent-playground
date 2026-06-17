# 01 Agent Loop

## 概念

Agent Loop 是本项目的核心：接收用户消息，检索记忆，构建上下文，请模型决定直接回答或调用工具，执行工具后再把 `tool_result` 交回模型，直到得到最终答案或达到最大循环次数。

## 对应代码

- `app/services/chat.py`：API 请求进入 `ChatService`，负责创建消息、检索记忆、启动 runner。
- `app/agent/runner.py`：`AgentRunner.run()` 与 `AgentRunner.stream()` 实现 loop。
- `app/models/adapters.py`：`FakeModelAdapter` / `ClaudeModelAdapter` / `OpenAIModelAdapter` 统一返回 `ModelTurn`。
- `app/schemas/api.py`：`ModelTurn`、`ToolCallRequest`、`ToolCallResult` 定义内部协议。

## 如何运行

```bash
uv run uvicorn app.main:app --reload
curl -X POST http://127.0.0.1:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"请统计 hello world"}'
```

## 如何在 TUI 观察

1. `uv run python -m app.tui.main`
2. `F2` 进入 Chat Lab，发送“请统计 hello world”。
3. `F3` 进入 Run Trace，查看 `run_started`、`memory_retrieved`、`session_summary_checked`、`session_summary_updated`、`session_summary_used`、`model_request`、`model_response`、`tool_call`、`tool_result`、`model_final`、`run_finished`。

## 对应测试

- `tests/test_agent_runner.py`
- `tests/test_api.py::test_run_trace_endpoint_returns_steps`
- `tests/test_api.py::test_stream_chat_emits_observable_events`
