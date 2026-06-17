# Lab 04: 失败与调试

## 你将学到什么

- 如何把失败先归类，再定位。
- 如何用 Run Trace 复盘工具失败、模型失败和记忆检索失败。
- 如何区分本地开发、Docker、真实 provider 三类环境问题。
- 为什么 Validation Lab 先看核心闭环，再看环境和开发质量。

## 先看结果

一次合格的失败排查应该能回答：

- 失败发生在哪一层：API、Agent Loop、工具、模型、记忆、数据库、TUI、Docker。
- 有没有 `run_id`。
- Run Trace 有没有 `run_failed`、`tool_result.is_error` 或 provider error。
- 这个失败是否和真实 API Key、`live=true`、Docker volume 或 OpenAI-compatible tool calling 有关。

## 背后的最小原理

这个项目的调试入口不是猜，也不是重启三遍求运气。先看 API 响应，再看 `run_id`，最后看 Run Trace 和 TUI Validation Lab。

Agent 项目的失败经常不是一处坏了，而是边界没分清：模型只负责决定，工具由后端执行，记忆由后端检索，Docker 用自己的 SQLite volume。边界一混，排查就开始瞎转圈。

## 代码入口

- `app/agent/runner.py`：写入 `run_failed`、`tool_call`、`tool_result`。
- `app/models/adapters.py`：把 provider SDK 异常转成可观察错误。
- `app/api/routes.py`：`/api/v1/runs` 和 `/api/v1/models/health`。
- `app/tui/screens/run_trace.py`：Run Trace 复盘入口。
- `app/tui/screens/validation_lab.py`：核心闭环、环境、开发质量检查。

## 动手实验

### 1. 工具参数失败

调用 `json_extract` 时故意漏掉 `fields`：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/tools/json_extract/invoke \
  -H "Content-Type: application/json" \
  -d '{"text":"name: Alice"}'
```

观察重点：

- API 是否返回结构化错误。
- TUI Tools Lab 是否能在调用前提示 schema 问题。
- 失败是否被限制在工具调用层，而不是把整个服务打崩。

### 2. 模型配置失败

如果没有真实 API Key，不要把 provider 切成 `openai` 或 `claude` 后再怪项目抽风。正确排查方式：

```bash
curl http://127.0.0.1:8000/api/v1/models/health
```

只有确实要做真实连通性检查时才运行：

```bash
curl 'http://127.0.0.1:8000/api/v1/models/health?live=true'
```

观察重点：

- 不带 `live=true` 时只是静态配置检查。
- 带 `live=true` 时会请求真实供应商，可能产生 token 成本或限流。
- provider 错误应被分类成认证、限流、超时、模型不存在或工具 schema 不兼容等类型。

### 3. 记忆检索失败

先写入记忆：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"请记住：我偏好 FastAPI 示例"}'
```

再问一个相关问题，然后查看 Run Trace 里的 `memory_retrieved`。

观察重点：

- `terms` 提取了哪些关键词。
- `matches` 是否为空。
- 命中的记忆属于 `session`、`project` 还是 `user` scope。
- 当前检索是关键词检索，不是 embedding RAG。

### 4. Docker 与本地数据库看混

Docker Compose 的 SQLite 数据在 `agent_data` volume，本地开发默认是 `./agent_playground.db`。如果你在本地写了记忆，却去 Docker 容器里找，找不到是正常的，不是数据库祖宗十八代突然叛变。

排查时先确认启动方式：

```bash
docker compose ps
curl http://127.0.0.1:8000/health
```

再确认你到底在看本地进程还是容器进程。

### 5. Validation Lab 收口

启动 TUI 后按 `F6`：

1. 先运行 Core Path。
2. 再看 Environment。
3. 最后看 Developer Quality。

不要一上来就把 `pytest` 或 Docker 当成核心学习闭环。核心闭环是 API、Chat、Tool、Memory、Trace 先活着。

## 你应该观察什么

- 有 `run_id` 的失败优先看 Run Trace。
- 没有 `run_id` 的失败优先看 API、配置和启动日志。
- 工具失败看 `tool_result.is_error`。
- provider 失败看 `/models/health` 和 trace 里的 provider error。
- 记忆失败看 `memory_retrieved.terms` 和 `matches`。
- Docker 失败先确认 `.env.docker`、volume 和端口。

## 失败案例

| 现象 | 优先检查 |
|---|---|
| Chat 没调用工具 | prompt 是否触发工具、provider 是否支持 tool calling、`used_tools` 和 trace |
| OpenAI-compatible 不会 tool call | `AGENT_PLAYGROUND_OPENAI_TOOL_CALLING`、协议兼容模式、服务商能力 |
| 记忆没召回 | `memory_retrieved.terms`、scope、状态是否 `active` |
| Dashboard 显示历史模型错误 | 看 `is_latest_run`，别把历史失败当当前失败 |
| Docker 找不到本地记忆 | Docker volume 和本地 SQLite 是两份数据 |

## 对应测试

- `tests/test_api.py::test_model_health_endpoint_reports_static_status`
- `tests/test_api.py::test_run_trace_endpoint_returns_steps`
- `tests/test_api.py::test_memory_roundtrip_retrieves_injects_and_uses_saved_memory`
- `tests/test_tui_screens.py`

## 延伸阅读

- [`../04-trace-observability.md`](../04-trace-observability.md)
- [`../05-tui-guide.md`](../05-tui-guide.md)
- [`../06-claude-adapter.md`](../06-claude-adapter.md)
- [`../08-testing-and-docker.md`](../08-testing-and-docker.md)
- [`../tutorials/03-validation-lab.md`](../tutorials/03-validation-lab.md)
