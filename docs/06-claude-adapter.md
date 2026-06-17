# 06 Claude Adapter

## 概念

`ClaudeModelAdapter` 是本项目的真实 Claude 接入层。它使用官方 `anthropic` Python SDK，把 Claude Messages API 的 `text` / `tool_use` 内容块转换成本项目统一的 `ModelTurn`。

本项目故意采用手动 Tool Use loop，而不是隐藏在框架里：这样学习者可以在 trace 中看到模型请求、工具调用、工具结果和最终回答。

## 配置

默认仍是 Fake 模式：

```env
AGENT_PLAYGROUND_MODEL_PROVIDER=fake
```

切换 Claude：

```env
AGENT_PLAYGROUND_MODEL_PROVIDER=claude
AGENT_PLAYGROUND_ANTHROPIC_API_KEY=sk-ant-...
AGENT_PLAYGROUND_CLAUDE_MODEL=claude-opus-4-8
AGENT_PLAYGROUND_CLAUDE_MAX_TOKENS=16000
AGENT_PLAYGROUND_CLAUDE_EFFORT=medium
AGENT_PLAYGROUND_CLAUDE_THINKING=adaptive
```

也可以不设置 `AGENT_PLAYGROUND_ANTHROPIC_API_KEY`，让 SDK 从 `ANTHROPIC_API_KEY`、`ANTHROPIC_AUTH_TOKEN` 或 `ant auth login` profile 读取凭证。

## 对应代码

- `app/core/config.py`：Claude 配置项。
- `app/models/factory.py`：按 provider 创建 adapter。
- `app/models/adapters.py`：`ClaudeModelAdapter`。
- `app/agent/runner.py`：执行工具并把 `tool_result` 交回 adapter。
- `app/api/routes.py`：`/api/v1/models/health` 配置/连通性检查。

## Tool Use 流程

```text
用户消息
  -> AgentRunner 构建 context
  -> ClaudeModelAdapter.next_turn(...)
  -> client.messages.create(..., tools=[...])
  -> Claude 返回 stop_reason=tool_use + tool_use blocks
  -> adapter 转成 ModelTurn(kind="tool_call")
  -> AgentRunner 执行本地工具
  -> adapter 下一轮把 assistant tool_use + user tool_result 发回 Claude
  -> Claude 返回 end_turn
  -> ModelTurn(kind="final")
```

流式接口使用 SDK 的 `messages.stream()`、`text_stream` 和 `get_final_message()`：文本增量给 `/chat/stream`，最终 message 再统一转为 `ModelTurn`。

## 关键约束

- 使用官方 `anthropic` Python SDK；
- 默认模型为 `claude-opus-4-8`；
- 不在代码里硬编码 API Key；
- Claude / Opus 4.8 使用 adaptive thinking；
- `stop_reason=refusal` 会转为可读最终消息；
- SDK 异常会转为 `ModelAdapterError`，并进入 trace。

## 如何运行

```bash
AGENT_PLAYGROUND_MODEL_PROVIDER=claude uv run uvicorn app.main:app --reload
curl 'http://127.0.0.1:8000/api/v1/models/health?live=true'
curl -X POST http://127.0.0.1:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"请统计 hello world"}'
```

缺少 API Key 时，Claude live health 会返回 `unavailable` 和经过清洗的错误类型，而不是泄露密钥或堆栈。

## 响应慢时的排查顺序

TUI Dashboard 默认不会触发 live health check；按 `l` 才会请求真实模型。这样可以避免每次进入 TUI 都产生外部 API 延迟或费用。

如果流式响应慢，优先看：

1. Dashboard 的 `Live Check Duration`：区分模型不可用、认证失败和网络慢；
2. Chat Lab 的 `Latency: first_token=... total=...`：区分首 token 等待和总生成耗时；
3. Run Trace 中的 `latency_metric` step：保留本次 run 的业务级延迟指标；
4. `AGENT_PLAYGROUND_CLAUDE_MAX_TOKENS`：教学演示可降到 `2048`，不改变默认值也不影响生产兼容。

```env
AGENT_PLAYGROUND_CLAUDE_MAX_TOKENS=2048
```

## 对应测试

- `tests/test_model_factory.py`
- `tests/test_claude_adapter.py`
- `tests/test_agent_runner.py`
