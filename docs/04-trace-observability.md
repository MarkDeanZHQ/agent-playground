# 04 Trace Observability

## 概念

可观察性是本项目的核心学习价值：每次 Chat 都会生成一个 `AgentRun`，并把关键状态写入 `agent_steps` 和 `tool_calls`。

学习者不需要猜 Agent 做了什么，可以直接复盘：

```text
run_started
memory_retrieval_started
memory_retrieved（包含 query、terms、score、matched_terms、reason、rank_signals）
session_summary_checked
session_summary_updated（触发滚动摘要时出现）
session_summary_used（摘要进入上下文时出现）
context_built
model_request
model_response
model_tool_use
工具执行：tool_call / tool_result
token_usage
model_final
run_finished
memory_policy_decision / memory_saved / memory_skipped
```

## 对应代码

- `app/agent/runner.py`：`TraceRecorder`、`AgentRunner.run()`、`AgentRunner.stream()`。
- `app/db/models.py`：`AgentRun`、`AgentStep`、`ToolCall`。
- `app/api/routes.py`：`/api/v1/runs` 与 `/api/v1/runs/{run_id}`。
- `app/tui/screens/run_trace.py`：TUI Run Trace 展示。

## 如何运行

触发一次工具调用：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"请统计 hello world"}'
```

拿到响应中的 `run_id` 后：

```bash
curl http://127.0.0.1:8000/api/v1/runs/<run_id>
```

## 如何在 TUI 观察

1. `F2` Chat Lab 发送消息。
2. `F3` Run Trace 刷新。
3. 左侧选择 run。
4. 观察步骤列表和完整 JSON。

`memory_retrieved` step 现在会记录检索解释：`terms` 是本轮保守提取的关键词，`matches` 中每条记忆包含 `memory_id`、`score`、`matched_terms`、`reason`、`conflict_key` 和 `rank_signals`。这能说明记忆为什么被注入上下文，也明确当前仍是轻量关键词检索，不是 embedding RAG。`session` 级记忆只会在同一个 session 内被召回，`project` / `user` 级记忆可跨 session 召回。

`memory_policy_decision` step 会记录自动抽取时的 `conflict_decision`。它用于区分 `no_conflict`、`pending_confirmation`、`supersedes` 和 `invalidated`；其中 `pending_confirmation` 的执行结果会通过 `outcome` / `conflict_outcome` 体现为 `coexists`。当出现替代时，`memory_saved` 会带上 `supersedes_memory_id`，`memory_superseded` 会记录被替代旧记忆。过期记忆会被标记为 `invalidated`，不再参与 `memory_retrieved`。

`session_summary_*` step 用于观察长对话上下文压缩。摘要默认由确定性规则生成，不调用真实模型，也不产生额外 token 成本。摘要只覆盖当前 turn 之前、且不在最近消息窗口内的旧消息；当前用户消息仍通过 `current_user_message` 和最近消息窗口进入上下文，不会提前进入摘要。

`context_built` step 会记录 `context_trace`。当前预算单位是字符，不是真实 token；payload 包含 `total_budget_chars`、`total_original_chars`、`total_final_chars`、`trimmed_blocks`、`dropped_blocks` 和每个 block 的 `source/decision/reason`。当前用户消息只通过 `current_user_message` 注入，recent window 会排除当前 user message，避免重复塞上下文。

Claude 模式下重点观察：

- `model_request`：请求摘要；
- `model_response`：`finish_reason`、工具调用数量；
- `model_tool_use`：Claude 返回的工具名称和参数；
- `tool_result`：本地工具执行结果；
- `token_usage`：真实模型 usage。

## 失败也要可观察

工具失败不会直接隐藏。`ToolRegistry.execute()` 会把异常转成 `ToolCallResult(is_error=True)`，Run Trace 中可以看到失败工具名称、参数与错误内容。

真实模型 SDK 异常会转为 `ModelAdapterError`，Agent Run 会落为 `failed`，并写入 `run_failed`。

## 对应测试

- `tests/test_agent_runner.py`
- `tests/test_api.py::test_run_trace_endpoint_returns_steps`
- `tests/test_api.py::test_stream_chat_emits_observable_events`
