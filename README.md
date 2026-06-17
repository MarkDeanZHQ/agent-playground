# Agent Playground

一个面向后端开发者的可观察 AI Agent 学习实验场。

## 当前实现范围

- FastAPI 服务与 OpenAPI 文档
- `/health`
- `/api/v1/sessions`
- `/api/v1/chat`
- `/api/v1/chat/stream`
- `/api/v1/models/health`
- `/api/v1/runs`
- `/api/v1/runs/{run_id}`
- `/api/v1/memories`
- `/api/v1/memories/{memory_id}`
- `/api/v1/memories/{memory_id}/archive`
- `/api/v1/memories/{memory_id}/delete`
- `/api/v1/memories/{memory_id}/restore`
- `/api/v1/tools`
- `/api/v1/tools/{name}/invoke`
- 可测试的 Agent Loop
- 受控安全工具：`text_stats`、`note_search`、`json_extract`、`todo_create`、`todo_list`
- SQLite + SQLAlchemy Async 持久化
- Alembic 初始迁移与开发态 `create_all`
- 轻量长期记忆策略：保守关键词检索、可解释评分、使用反馈、保守冲突处理、用户可控管理闭环与 `memory_versions` 版本审计记录
- 轻量会话摘要：`session_summaries` 滚动摘要、摘要 trace、chat/stream 持久化一致性
- 真实 Claude / OpenAI / OpenAI-compatible 模型接入
- pytest 测试
- Docker 本地启动配置
- Textual TUI：Dashboard、Chat Lab、Run Trace、Tools Lab、Memory Lab、Validation Lab

## 学习路线验收导览

| 学习路线要求 | 对应实现 |
|---|---|
| FastAPI 服务 | `app/main.py`, `/health`, `/api/v1/*` |
| 普通/流式对话 | `/api/v1/chat`, `/api/v1/chat/stream`, `app/services/chat.py` |
| Agent Loop 与最大循环保护 | `app/agent/runner.py`, `max_agent_loops` |
| 至少 2 轮工具调用 | `tests/test_agent_runner.py::test_agent_loop_supports_two_round_tool_calls` |
| 工具注册与调用 | `app/tools/registry.py`, `app/tools/builtin.py`, `tool_calls` |
| 短期会话上下文 | `ChatService._recent_messages()`, `ContextBuilder.build()` |
| 长会话摘要压缩 | `app/services/session_summary.py`, `session_summaries`, `ContextBuilder.build()` |
| 长期记忆抽取/检索/注入 | `app/memory/service.py`, `memories`, `memory_versions`；Agent 默认只检索 `active` 记忆，并在注入后更新 `use_count` / `last_used_at` |
| 记忆使用闭环 | `tests/test_api.py::test_memory_roundtrip_retrieves_injects_and_uses_saved_memory` |
| Trace 可观察性 | `agent_steps`, `tool_calls`, `/api/v1/runs/{run_id}` |
| 测试 | `tests/`, `uv run pytest` |
| Docker | `Dockerfile`, `docker-compose.yml`, `.env.docker.example` |

更完整的概念说明见 [`docs/01-agent-loop.md`](docs/01-agent-loop.md)、[`docs/02-tool-system.md`](docs/02-tool-system.md)、[`docs/03-memory-system.md`](docs/03-memory-system.md)、[`docs/04-trace-observability.md`](docs/04-trace-observability.md) 和 [`docs/08-testing-and-docker.md`](docs/08-testing-and-docker.md)。

## 已知限制和失败 case

- 默认 `FakeModelAdapter` 用于教学演示，不代表真实 LLM 推理能力；它只覆盖工具调用、记忆引用等少量可验收路径。
- 长期记忆检索当前是轻量关键词检索：保守 term 提取、DB 粗过滤、Python 精排；不支持 embedding、向量数据库、reranker 或复杂语义相关性排序。
- 记忆管理支持 `active` / `superseded` / `archived` / `deleted`；非 `active` 记忆不会注入 Agent 上下文，`superseded` 是系统历史状态，只读，软删除不会物理删除数据。
- 记忆冲突处理采用 `conflict_key` + 明确替换信号的保守 `superseded` 策略；普通相关记忆默认新增，不能识别所有复杂偏好冲突或细粒度事实冲突。
- 短期上下文保留当前 session 最近有限消息，超长对话会先写入滚动摘要，再继续保留最近窗口原文；摘要只覆盖当前 turn 之前的旧消息，不等于长期记忆。
- 工具集仅包含安全的教学工具，不提供 shell、浏览器抓取、任意文件读写等高权限工具。
- OpenAI-compatible 服务的 tool calling 支持度不一致；协议兼容模式只调整参数与流式解析，不再自动禁用 tools。
- `live=true` 模型健康检查会真实请求模型供应商，可能产生 token 成本或触发供应商限流。
- Docker 默认使用 `fake` provider；接入真实 Claude/OpenAI 时需要自行配置 API Key，并避免提交 `.env` / `.env.docker`。

## 使用 uv 创建依赖环境

本项目按要求使用 `uv` 管理依赖和虚拟环境，不直接使用本机 Python 环境安装依赖。

```bash
cd agent-playground
uv sync --dev
uv run pytest
uv run uvicorn app.main:app --reload
uv run python -m app.tui.main
```

服务启动后访问：

- API: <http://127.0.0.1:8000>
- OpenAPI: <http://127.0.0.1:8000/docs>

TUI 快捷键与导览：

- `F1` Dashboard/总览：查看 API、模型、工具、记忆与最近 Run 状态
- `F2` Chat/对话：发送消息，观察 Agent Loop、工具调用、记忆注入和流式输出
- `F3` Trace/轨迹：复盘 Run Trace；在 Chat Lab 内表示查看最近一次 Run Trace
- `F4` Tools/工具：查看工具 schema，并手动调用安全教学工具
- `F5` Memory/记忆：检索、手动新增、编辑、归档、软删除、恢复长期记忆，观察状态、来源与版本变化
- `F6` Validation/验收：运行学习验收台，先看核心闭环，再看环境和开发质量
- `r` 刷新当前页面或运行当前验证项
- `Ctrl+N` / `Ctrl+S` / `Ctrl+X` / `Ctrl+D` / `Ctrl+U` 在 Memory Lab 新增、保存、归档、软删除、恢复记忆
- `Ctrl+Enter` / `i` 在 Tools Lab 调用选中工具
- `Esc` / `Ctrl+C` 在 Chat Lab 取消当前请求
- `a` 在 Validation Lab 运行全部验证项
- `q` 退出

TUI 页面顶部会显示页面标题、学习目标、固定的本页核心快捷键提示和动态状态；窗口最底部提供 `F1`~`F6` 页面标签栏和 Textual Footer，当前页面会高亮，也可以点击标签切换。空态与错误态会给出下一步建议。Dashboard、Run Trace、Tools、Memory、Validation 的只读详情/输出区支持选中文本并复制。Chat Lab 的 Live Trace 现在会显示 `session_summary_*` 事件。Tools Lab 现在会分层展示 Summary / Parameters / Learning notes / Raw schema，支持 example 一键填充、调用前最小 schema 校验、错误分类、最近 10 次手动调用历史，以及把当前示例送到 Chat Lab 对照自动工具调用。完整说明见 [`docs/05-tui-guide.md`](docs/05-tui-guide.md)。

## 学习文档

Round 3 文档按“概念 → 对应代码 → 如何运行 → 如何在 TUI 观察 → 对应测试”组织：

1. [`docs/01-agent-loop.md`](docs/01-agent-loop.md)：Agent Loop。
2. [`docs/02-tool-system.md`](docs/02-tool-system.md)：工具系统。
3. [`docs/03-memory-system.md`](docs/03-memory-system.md)：记忆系统与版本。
4. [`docs/04-trace-observability.md`](docs/04-trace-observability.md)：Trace 可观察性。
5. [`docs/05-tui-guide.md`](docs/05-tui-guide.md)：TUI 学习控制台。
6. [`docs/06-claude-adapter.md`](docs/06-claude-adapter.md)：Claude Adapter 与真实 Tool Use。
7. [`docs/07-add-custom-tool.md`](docs/07-add-custom-tool.md)：新增自定义工具。
8. [`docs/08-testing-and-docker.md`](docs/08-testing-and-docker.md)：测试、迁移与 Docker。

## TUI 学习路径

详细说明见 [`docs/05-tui-guide.md`](docs/05-tui-guide.md)。推荐按下面顺序实验：

1. [`docs/tutorials/01-watch-tool-call.md`](docs/tutorials/01-watch-tool-call.md)：查看工具 schema，手动调用工具，再在 Chat 中触发工具。
2. [`docs/tutorials/02-memory-roundtrip.md`](docs/tutorials/02-memory-roundtrip.md)：写入记忆、检索记忆、观察记忆注入和策略决策。
3. [`docs/tutorials/03-validation-lab.md`](docs/tutorials/03-validation-lab.md)：在 TUI 中运行学习验收台，先跑核心闭环，再看环境与开发质量。

## 示例请求

```bash
curl -X POST http://127.0.0.1:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"请统计 hello world"}'
```

## 设计说明

默认使用 `FakeModelAdapter`，便于学习者在没有真实 LLM API Key 的情况下理解 Agent Loop、工具调用、轨迹记录和记忆系统。

真实模型接入具备以下保护：

- `AGENT_PLAYGROUND_MODEL_PROVIDER` 严格限制为 `fake`、`claude`、`openai`。
- 测试环境强制使用 `fake` provider，避免本地 `.env` 误触发真实 API 调用。
- SDK 异常会转为可观察的 `model_error` trace，Agent Run 状态会落为 `failed`。
- 模型 `usage` 会记录为 `token_usage` trace step。
- `/api/v1/chat/stream` 会在真实 provider 下透传文本 delta；Fake provider 仍按一次性 delta 模拟。
- OpenAI 工具参数 JSON 解析失败会变成可观察的 `_parse_error` 参数，不会直接抛 500。

## 安全边界

这个项目是本地学习实验场，不是生产级沙箱，安全边界必须写清楚，免得把本地脏东西提交上去：

- 模型只能通过 `ToolRegistry` 调用后端注册过的工具，不能直接执行 shell、访问浏览器、读取任意路径或修改系统配置。
- `note_search` 只读取 `AGENT_PLAYGROUND_SANDBOX_DIR` 指向目录下的 `*.md` 笔记，默认是 `sandbox/notes/*.md`。
- `todo_create` / `todo_list` 只读写 `sandbox/todos.json`，用于演示受控副作用，不作为通用文件写入接口。
- `/api/v1/models/health` 默认只做静态配置检查；只有显式追加 `?live=true` 才会请求真实模型供应商，可能产生 token 成本或触发限流。
- 本地 `.env`、`.env.docker`、`.env.*`、SQLite 数据库、缓存目录、覆盖率产物和 `sandbox/todos.json` 都应留在本机，已在 `.gitignore` 中忽略；`.env.example` 和 `.env.docker.example` 只保留无密钥模板。
- Docker 数据库放在 `agent_data` volume，本地开发数据库默认是 `./agent_playground.db`，两者不要混用，避免把聊天记录、长期记忆或真实 API Key 混进提交。

## 模型健康检查

默认只检查配置，不发起真实 LLM 请求：

```bash
curl http://127.0.0.1:8000/api/v1/models/health
```

如需执行真实连通性检查：

```bash
curl 'http://127.0.0.1:8000/api/v1/models/health?live=true'
```

注意：`live=true` 会实际调用配置的模型供应商，可能产生 token 成本。

## 接入真实 OpenAI API Key

项目支持 OpenAI 官方 API 以及 OpenAI-compatible 服务。

在本地 `.env` 中配置：

```env
AGENT_PLAYGROUND_MODEL_PROVIDER=openai
AGENT_PLAYGROUND_OPENAI_API_KEY=sk-...
AGENT_PLAYGROUND_OPENAI_MODEL=gpt-4.1
AGENT_PLAYGROUND_OPENAI_MAX_TOKENS=16000
AGENT_PLAYGROUND_LLM_TIMEOUT_SECONDS=60
AGENT_PLAYGROUND_LLM_MAX_RETRIES=2
```

如需接入 OpenAI-compatible 服务，可额外设置：

```env
AGENT_PLAYGROUND_OPENAI_BASE_URL=https://your-openai-compatible-endpoint/v1
```

部分 OpenAI-compatible 服务只支持旧参数 `max_tokens`，或者不完整支持 tool calling。项目默认 `AGENT_PLAYGROUND_OPENAI_PROTOCOL_MODE=auto`，检测到自定义 `AGENT_PLAYGROUND_OPENAI_BASE_URL` 时会自动启用协议兼容模式：使用 `max_tokens`，并改用更宽松的流式 chunk 解析；是否发送 tools 仅由 `AGENT_PLAYGROUND_OPENAI_TOOL_CALLING` 控制。

如需手动控制，可配置：

```env
AGENT_PLAYGROUND_OPENAI_PROTOCOL_MODE=on   # on | off | auto
AGENT_PLAYGROUND_OPENAI_TOKEN_PARAMETER=max_tokens
AGENT_PLAYGROUND_OPENAI_TOOL_CALLING=false
```

如果你还在使用旧字段 `AGENT_PLAYGROUND_OPENAI_COMPATIBILITY_MODE`，当前版本仍兼容读取，但建议迁移到 `AGENT_PLAYGROUND_OPENAI_PROTOCOL_MODE`，避免把“协议兼容策略”和“工具能力开关”混在一起。

某些 OpenAI-compatible 网关会拦截缺少浏览器 User-Agent 的请求，可按需覆盖：

```env
AGENT_PLAYGROUND_OPENAI_USER_AGENT="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
```

OpenAI 接入使用官方 `openai` Python SDK 的 `AsyncOpenAI` 和 Chat Completions tool calling。官方 OpenAI endpoint 默认使用 SDK 高级 streaming helper；协议兼容模式只影响参数选择和流式解析分支，不再自动禁用 tool calling。是否发送 tools 由 `AGENT_PLAYGROUND_OPENAI_TOOL_CALLING` 单独控制，真实支持情况可通过 `/api/v1/models/health?live=true` 的 `tool_calling_status` 字段观察。

## 接入真实 Claude API Key

项目已提供真实 LLM 接入口，使用官方 `anthropic` Python SDK，不会把 API Key 写入代码。

在本地 `.env` 中配置：

```env
AGENT_PLAYGROUND_MODEL_PROVIDER=claude
AGENT_PLAYGROUND_ANTHROPIC_API_KEY=sk-ant-...
AGENT_PLAYGROUND_CLAUDE_MODEL=claude-opus-4-8
AGENT_PLAYGROUND_CLAUDE_MAX_TOKENS=16000
AGENT_PLAYGROUND_CLAUDE_EFFORT=medium
AGENT_PLAYGROUND_CLAUDE_THINKING=adaptive
AGENT_PLAYGROUND_LLM_TIMEOUT_SECONDS=60
AGENT_PLAYGROUND_LLM_MAX_RETRIES=2
```

也可以不写 `AGENT_PLAYGROUND_ANTHROPIC_API_KEY`，改用系统环境变量 `ANTHROPIC_API_KEY` 或 `ant auth login` 生成的 Anthropic profile，让 SDK 自动读取凭证。

`claude-opus-4-8` / `claude-opus-4-7` 与 `claude-fable-5` 只使用 adaptive thinking；即使配置为 `disabled`，适配层也会为这些模型强制使用 `{"type":"adaptive"}`，避免发送已不兼容的参数。

重新启动服务后：

```bash
uv run uvicorn app.main:app --reload
```

当前真实模型接入仍保持学习项目的简单闭环：模型负责决定“直接回答或调用工具”，工具执行仍由本地 `ToolRegistry` 完成。Claude Tool Use 会把 assistant 的 `tool_use` 内容块原样保存到下一轮上下文，再把本地工具结果作为 `tool_result` 用户消息送回模型，便于在 Run Trace 中观察完整闭环。

## 数据库迁移

开发学习模式仍会在应用启动时执行 `Base.metadata.create_all()`，降低首次运行门槛。需要显式演进 schema 时使用 Alembic：

```bash
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "describe schema change"
```

如果本地 `agent_playground.db` 已经由开发态 `create_all()` 建过表，初始迁移使用 `IF NOT EXISTS` 风格兼容该场景，并会写入 Alembic 版本表。更多说明见 [`docs/08-testing-and-docker.md`](docs/08-testing-and-docker.md)。

本轮已提供初始迁移：

```text
alembic/versions/0001_initial_schema.py
```

## Docker Compose

Docker Compose 会读取 `.env.docker` 作为容器专用环境变量文件。仓库提供 `.env.docker.example` 模板；首次使用可复制一份：

```bash
cp .env.docker.example .env.docker
```

默认 `.env.docker` 使用 `fake` provider，容器无需真实 LLM Key 即可启动：

```bash
docker compose up --build
curl http://127.0.0.1:8000/health
curl -X POST http://127.0.0.1:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"请统计 hello world"}'
```

Compose 会把 SQLite 数据放到 `agent_data` volume，并把 `./sandbox` 挂载到容器内 `/app/sandbox`。`AGENT_PLAYGROUND_DATABASE_URL` 固定在 `docker-compose.yml` 中覆盖为 `/app/data/agent_playground.db`，不要写进 `.env.docker`，这样 Docker 的聊天记录和长期记忆会与本地 `./agent_playground.db` 隔离。

如需在 Docker 中使用真实 Claude，将 `.env.docker` 改为：

```env
AGENT_PLAYGROUND_MODEL_PROVIDER=claude
AGENT_PLAYGROUND_ANTHROPIC_API_KEY=sk-ant-...
AGENT_PLAYGROUND_CLAUDE_MODEL=claude-opus-4-8
```

`.env.docker` 已加入 `.gitignore` 和 `.dockerignore`，不要提交真实 API Key。更新环境变量后重启容器：

```bash
docker compose up --build
```

最近一次 Docker 实启验证：2026-06-17，在 Windows 10 Pro + Docker Desktop Linux engine 环境下，`docker compose up --build --detach` 成功启动，`/health` 返回 `{"status":"ok"}`，`/api/v1/chat` 返回 `used_tools` 包含 `text_stats`；重启 Compose 后服务仍可访问。完整记录见 [`docs/08-testing-and-docker.md`](docs/08-testing-and-docker.md)。
