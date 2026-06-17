# Agent Playground

一个面向后端开发者的可观察 AI Agent 学习实验场，用来实践、调试、验证 Agent Loop、工具调用、上下文工程和长期记忆。

## 这是什么

`agent-playground` 是一个本地优先的教学项目。它用 FastAPI、SQLite、SQLAlchemy Async、Textual TUI 和可测试的 Agent Loop，把一次 Agent 对话里的模型请求、工具调用、记忆检索、上下文组装、trace 记录和失败处理都摊开给你看。

它不是为了堆功能给人看热闹，而是为了回答一个朴素问题：后端开发者怎样把 Agent 从“能聊”做成“可观察、可测试、可解释”。

## 适合谁

- 想系统理解 Agent Loop 的后端开发者。
- 想通过 trace 学工具调用、记忆、上下文工程的人。
- 想用一个可控本地实验场快速验证 Agent 想法的人。

## 不适合谁

- 想直接拿去上线当生产 Agent 平台的人。
- 想要多租户、权限系统、向量检索、生产级沙箱的人。
- 想跳过原理，只看“怎么接个 API 出效果”的人。

## 先看这个

- 默认使用 `fake` provider，没有 API Key 也能跑通工具调用、记忆和 trace 闭环。
- 这是教学实验场，不是生产级沙箱。
- 工具集是受控教学工具，不提供 shell、浏览器抓取、任意文件读写等高权限能力。
- 长期记忆当前是轻量关键词检索，不是 embedding 检索或向量数据库。
- `live=true` 会真实请求模型供应商，可能产生 token 成本或触发限流。

## 快速开始

本项目使用 `uv` 管理依赖和虚拟环境，不直接依赖本机 Python 环境。

```bash
cd agent-playground
uv sync --dev
uv run uvicorn app.main:app --reload
```

服务启动后访问：

- API: <http://127.0.0.1:8000>
- OpenAPI: <http://127.0.0.1:8000/docs>

启动 Textual TUI：

```bash
uv run python -m app.tui.main
```

## 从这里开始学

先按 Lab 做，不要一上来钻参考文档。首页要是又把人按进八篇实现说明里，那就跟把新手丢进仓库找螺丝一样离谱。

1. [`docs/labs/01-tool-call.md`](docs/labs/01-tool-call.md)：工具调用闭环。
2. [`docs/labs/02-context-and-summary.md`](docs/labs/02-context-and-summary.md)：上下文与摘要，占位入口，先读 [`docs/01-agent-loop.md`](docs/01-agent-loop.md) 和 [`docs/04-trace-observability.md`](docs/04-trace-observability.md)。
3. [`docs/labs/03-memory.md`](docs/labs/03-memory.md)：长期记忆，占位入口，先读 [`docs/03-memory-system.md`](docs/03-memory-system.md) 和 [`docs/tutorials/02-memory-roundtrip.md`](docs/tutorials/02-memory-roundtrip.md)。
4. [`docs/labs/04-failures.md`](docs/labs/04-failures.md)：失败与调试。
5. [`docs/learning-path.md`](docs/learning-path.md)：完整学习路线。

## 你能做什么实验

- 观察一次完整的工具调用闭环。
- 对比手动工具调用和 Agent 自动工具调用。
- 观察 recent messages、session summary 和 current user message 如何进入上下文。
- 观察记忆写入、召回、冲突、替代、归档和软删除。
- 观察 trace 中的失败、错误分类、provider usage 和成本估算。
- 用 TUI 的 Validation Lab 验证核心学习闭环、环境配置和开发质量。

## 运行示例

触发一次工具调用：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"请统计 hello world"}'
```

写入一条偏好记忆：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"请记住：我偏好 FastAPI 示例"}'
```

响应里会返回 `run_id`、`used_tools` 等字段。拿到 `run_id` 后可以查看 trace：

```bash
curl http://127.0.0.1:8000/api/v1/runs/<run_id>
```

## 常见失败

- 没有 API Key，却把 `AGENT_PLAYGROUND_MODEL_PROVIDER` 切成了 `openai` 或 `claude`。
- `live=true` 会产生真实模型请求，不是普通静态健康检查。
- OpenAI-compatible 服务的 tool calling 支持度不一致。
- `.env`、`.env.docker`、`.env.*` 不能提交。
- Docker 数据库和本地 SQLite 数据库是分开的，不要拿错数据源排查。
- 长期记忆是关键词检索，查不到不代表 Agent 有玄学问题，先看 trace 里的 `memory_retrieved.terms`。

## 部署方式

本地开发优先用 `uv`：

```bash
uv run uvicorn app.main:app --reload
```

Docker Compose 用于本地交付验证：

```bash
cp .env.docker.example .env.docker
docker compose up --build
```

默认 `.env.docker` 使用 `fake` provider，容器无需真实 LLM Key 即可启动。Docker 的 SQLite 数据保存在 `agent_data` volume，本地开发数据库默认是 `./agent_playground.db`，两者不要混用。

更多测试、迁移和 Docker 说明见 [`docs/08-testing-and-docker.md`](docs/08-testing-and-docker.md)。

## 更多配置

- 模型健康检查：`/api/v1/models/health` 默认只做静态配置检查；`/api/v1/models/health?live=true` 才会请求真实供应商。
- Claude 接入：见 [`docs/06-claude-adapter.md`](docs/06-claude-adapter.md)。
- OpenAI / OpenAI-compatible 接入：配置项在 `.env.example` 中，协议兼容、tool calling 和 User-Agent 注意事项见 [`docs/05-tui-guide.md`](docs/05-tui-guide.md) 与 [`docs/08-testing-and-docker.md`](docs/08-testing-and-docker.md)。
- Alembic 与 `create_all` 双模式：见 [`docs/08-testing-and-docker.md`](docs/08-testing-and-docker.md)。
- TUI 快捷键、页面说明、Dashboard 和 Run Trace 展示：见 [`docs/05-tui-guide.md`](docs/05-tui-guide.md)。
- 新增自定义工具：见 [`docs/07-add-custom-tool.md`](docs/07-add-custom-tool.md)。

## 文档索引

学习主线：

1. [`docs/learning-path.md`](docs/learning-path.md)：完整学习路线。
2. [`docs/labs/01-tool-call.md`](docs/labs/01-tool-call.md)：Lab 1 工具调用闭环。
3. [`docs/labs/04-failures.md`](docs/labs/04-failures.md)：Lab 4 失败与调试。
4. [`docs/tutorials/01-watch-tool-call.md`](docs/tutorials/01-watch-tool-call.md)：旧版工具调用教程，保留兼容入口。
5. [`docs/tutorials/02-memory-roundtrip.md`](docs/tutorials/02-memory-roundtrip.md)：记忆写入与召回教程。
6. [`docs/tutorials/03-validation-lab.md`](docs/tutorials/03-validation-lab.md)：Validation Lab 教程。

参考文档：

1. [`docs/01-agent-loop.md`](docs/01-agent-loop.md)：Agent Loop。
2. [`docs/02-tool-system.md`](docs/02-tool-system.md)：工具系统。
3. [`docs/03-memory-system.md`](docs/03-memory-system.md)：记忆系统与版本。
4. [`docs/04-trace-observability.md`](docs/04-trace-observability.md)：Trace 可观察性。
5. [`docs/05-tui-guide.md`](docs/05-tui-guide.md)：TUI 学习控制台。
6. [`docs/06-claude-adapter.md`](docs/06-claude-adapter.md)：Claude Adapter 与真实 Tool Use。
7. [`docs/07-add-custom-tool.md`](docs/07-add-custom-tool.md)：新增自定义工具。
8. [`docs/08-testing-and-docker.md`](docs/08-testing-and-docker.md)：测试、迁移与 Docker。

## 已知限制

- 默认 `FakeModelAdapter` 只覆盖教学路径，不代表真实 LLM 推理能力。
- 长期记忆检索当前是轻量关键词检索，不支持 embedding、向量数据库、reranker 或复杂语义排序。
- 记忆冲突处理采用保守策略，不能识别所有复杂偏好冲突或细粒度事实冲突。
- 短期上下文、session summary 和长期记忆是三套不同机制，不要混成一个“记忆”概念。
- 工具只能通过后端注册的 `ToolRegistry` 执行，不能绕过安全边界调用高权限能力。
- Dashboard 的 `Model Health` 只表示当前健康检查结果，历史 Run 错误需要去 Run Trace 和 Dashboard 历史区域复盘。
- 本项目保留旧文档入口，不会一次性删除 `docs/01..08.md` 或 `docs/tutorials/*`。
