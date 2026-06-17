# Agent Playground 学习路线

这条路线按任务组织，不按实现文件组织。先把主链路跑通，再回头看实现细节。别一上来就啃所有参考文档，那种学法容易把脑子学成一团毛线。

## 开始前

先启动 API 和 TUI：

```bash
cd agent-playground
uv sync --dev
uv run uvicorn app.main:app --reload
uv run python -m app.tui.main
```

确认能打开：

- OpenAPI: <http://127.0.0.1:8000/docs>
- TUI: 本地终端里的 Textual 控制台

## 路线总览

| 阶段 | 目标 | 入口 |
|---|---|---|
| 1 | 跑通工具调用闭环 | [`labs/01-tool-call.md`](labs/01-tool-call.md) |
| 2 | 看懂上下文与摘要 | [`01-agent-loop.md`](01-agent-loop.md), [`04-trace-observability.md`](04-trace-observability.md) |
| 3 | 跑通长期记忆 | [`03-memory-system.md`](03-memory-system.md), [`tutorials/02-memory-roundtrip.md`](tutorials/02-memory-roundtrip.md) |
| 4 | 学会复盘失败 | [`labs/04-failures.md`](labs/04-failures.md) |
| 5 | 深入实现和交付 | [`02-tool-system.md`](02-tool-system.md), [`05-tui-guide.md`](05-tui-guide.md), [`08-testing-and-docker.md`](08-testing-and-docker.md) |

## 第一段：工具调用闭环

先做 [`labs/01-tool-call.md`](labs/01-tool-call.md)。

你要看懂四件事：

- 工具 schema 怎样描述参数。
- 手动调用工具和 Agent 自动调用工具有什么区别。
- `tool_call` / `tool_result` 怎样出现在 Run Trace 里。
- 工具失败为什么不会直接把服务搞崩。

对应参考：

- [`02-tool-system.md`](02-tool-system.md)
- [`tutorials/01-watch-tool-call.md`](tutorials/01-watch-tool-call.md)

## 第二段：上下文与摘要

用 Chat Lab 连续发送多轮消息，再到 Run Trace 看：

- `memory_retrieved`
- `session_summary_checked`
- `session_summary_updated`
- `session_summary_used`
- `context_built`

重点不是背字段，而是搞清楚三件事：

- 当前用户消息通过 `current_user_message` 进入上下文。
- 最近消息窗口负责短期上下文。
- 长会话摘要只覆盖旧消息，不等于长期记忆。

对应参考：

- [`01-agent-loop.md`](01-agent-loop.md)
- [`04-trace-observability.md`](04-trace-observability.md)

## 第三段：长期记忆

先写入一条偏好：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"请记住：我偏好 FastAPI 示例"}'
```

再发一个能使用这条偏好的问题，观察：

- 记忆是否被抽取。
- 记忆是否被召回。
- `use_count` / `last_used_at` 是否更新。
- 冲突、替代、归档、软删除分别怎样进入 trace。

对应参考：

- [`03-memory-system.md`](03-memory-system.md)
- [`tutorials/02-memory-roundtrip.md`](tutorials/02-memory-roundtrip.md)

## 第四段：失败与调试

做 [`labs/04-failures.md`](labs/04-failures.md)。

你要练的不是“永远不失败”，而是失败后能定位：

- 是工具参数错了。
- 是 provider/API Key 配置错了。
- 是 OpenAI-compatible tool calling 不支持。
- 是 Docker 和本地数据库看混了。
- 是记忆检索关键词没命中。

对应参考：

- [`04-trace-observability.md`](04-trace-observability.md)
- [`05-tui-guide.md`](05-tui-guide.md)
- [`08-testing-and-docker.md`](08-testing-and-docker.md)

## 第五段：实现与交付

主链路跑通后，再按专题补实现细节：

1. [`01-agent-loop.md`](01-agent-loop.md)：请求如何进入 Agent Loop。
2. [`02-tool-system.md`](02-tool-system.md)：工具 schema、注册、执行和错误包装。
3. [`03-memory-system.md`](03-memory-system.md)：记忆模型、检索、状态和版本。
4. [`04-trace-observability.md`](04-trace-observability.md)：Run、Step、ToolCall 的可观察结构。
5. [`05-tui-guide.md`](05-tui-guide.md)：TUI 学习控制台。
6. [`06-claude-adapter.md`](06-claude-adapter.md)：Claude Tool Use 接入。
7. [`07-add-custom-tool.md`](07-add-custom-tool.md)：新增自定义工具。
8. [`08-testing-and-docker.md`](08-testing-and-docker.md)：pytest、ruff、Alembic、Docker Compose。

## 验收

学完这条路线后，你应该能独立完成：

- 启动 API 和 TUI。
- 用 `fake` provider 跑通一次工具调用。
- 从 `run_id` 找到完整 trace。
- 解释 recent messages、session summary、long-term memory 的区别。
- 写入并召回一条长期记忆。
- 判断常见失败属于工具、模型、环境、数据库还是测试质量问题。
