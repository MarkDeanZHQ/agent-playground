# 08 Testing and Docker

## 概念

Round 3 的交付目标不是“代码能看”，而是“别人能独立启动、测试、验证”。本项目使用 `uv`、pytest、ruff、Alembic 和 Docker Compose 形成本地交付闭环。

## 本地测试

安装依赖：

```bash
uv sync --dev
```

运行测试：

```bash
uv run pytest
```

运行 lint：

```bash
uv run ruff check .
```

## 数据库迁移

开发学习模式下，应用启动仍会执行 `Base.metadata.create_all()`，降低首次运行门槛。

需要显式迁移时：

```bash
uv run alembic upgrade head
```

如果本地 `agent_playground.db` 已经由 `create_all()` 建过表，当前初始迁移会使用 `IF NOT EXISTS` 风格兼容该场景，并写入 Alembic 版本表。

新增 schema 后：

```bash
uv run alembic revision --autogenerate -m "describe schema change"
uv run alembic upgrade head
```

## Docker Compose

Docker Compose 读取 `.env.docker` 作为容器专用环境变量文件。首次使用可从模板复制：

```bash
cp .env.docker.example .env.docker
```

默认 `.env.docker` 使用 `fake` provider，容器无需真实 LLM Key 即可演示。`docker-compose.yml` 会固定覆盖：

```yaml
environment:
  AGENT_PLAYGROUND_DATABASE_URL: sqlite+aiosqlite:////app/data/agent_playground.db
```

不要把数据库路径写进 `.env.docker`，这样 Docker 的聊天记录、记忆和 trace 会保存在 `agent_data` volume，并与本地 `./agent_playground.db` 隔离。

验证 Compose 配置：

```bash
docker compose config --quiet
```

启动服务：

```bash
docker compose up --build
```

另开终端验证：

```bash
curl http://127.0.0.1:8000/health
curl -X POST http://127.0.0.1:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"请统计 hello world"}'
```

## 最近一次 Docker 实启验证

- 日期：2026-06-17
- 环境：Windows 10 Pro，本机 Docker Desktop Linux engine 可用
- `docker compose config --quiet`：通过
- `docker compose up --build --detach`：镜像构建完成，`agent-playground-api-1` 成功启动并监听 `0.0.0.0:8000->8000/tcp`
- `/health`：HTTP 200，返回 `{"status":"ok"}`
- `/api/v1/chat`：HTTP 200，返回 `used_tools` 包含 `text_stats`
- 持久化：容器内确认 `/app/data/agent_playground.db` 存在，使用 `agent_data:/app/data`
- 挂载：容器内确认 `/app/sandbox` 可访问并包含 `notes`
- 重启验证：执行 `docker compose down && docker compose up --detach` 后，服务重新可访问；刚启动后立即请求可能遇到短暂未就绪，重试 `/health` 通过

说明：如果 Windows 终端直接发送中文 JSON 时遇到请求体解析错误，请确认终端编码为 UTF-8，或用 Python/HTTP 客户端发送 UTF-8 JSON；这不是容器服务启动失败。

## 持久化与挂载

`docker-compose.yml` 中：

- `agent_data:/app/data` 保存 SQLite 数据库；
- `./sandbox:/app/sandbox` 让 `note_search` 可以读取本地示例笔记；
- 默认 provider 是 fake，容器无需真实 LLM Key 即可演示。

如果要在容器中体验 Claude 模式，修改 `.env.docker`：

```env
AGENT_PLAYGROUND_MODEL_PROVIDER=claude
AGENT_PLAYGROUND_ANTHROPIC_API_KEY=sk-ant-...
AGENT_PLAYGROUND_CLAUDE_MODEL=claude-opus-4-8
AGENT_PLAYGROUND_CLAUDE_MAX_TOKENS=16000
AGENT_PLAYGROUND_CLAUDE_EFFORT=medium
AGENT_PLAYGROUND_CLAUDE_THINKING=adaptive
```

如果要体验 OpenAI / OpenAI-compatible 模式，修改 `.env.docker`：

```env
AGENT_PLAYGROUND_MODEL_PROVIDER=openai
AGENT_PLAYGROUND_OPENAI_API_KEY=sk-...
AGENT_PLAYGROUND_OPENAI_MODEL=gpt-4.1
# AGENT_PLAYGROUND_OPENAI_BASE_URL=https://your-openai-compatible-endpoint/v1
```

`.env.docker` 已加入 `.gitignore` 和 `.dockerignore`；不要把真实 API Key 写进镜像、日志或提交记录。

## 本地文件安全边界

- `.env`、`.env.docker`、`.env.*` 只存放本机配置和密钥，禁止提交；仓库只保留 `.env.example` / `.env.docker.example` 模板。
- `agent_playground.db`、`test_agent_playground.db`、`*.db-shm`、`*.db-wal` 是本地运行或测试生成的数据文件，可能包含聊天记录、长期记忆和 trace，已加入 `.gitignore`。
- `.venv/`、`.pytest_cache/`、`.ruff_cache/`、`.mypy_cache/`、`.serena/`、`.ace-tool/`、覆盖率目录和构建目录都属于本地工具产物，不进入版本库。
- `sandbox/notes/*.md` 是可提交的演示笔记；`sandbox/todos.json` 是 `todo_create` 的本地副作用产物，已忽略。
- Docker Compose 的 SQLite 数据保存在 `agent_data` volume；清理或迁移 volume 前先确认里面没有需要保留的演示记录。

## TUI 验证

TUI 的 Validation Lab 可运行：

- API health；
- Chat no tool；
- Chat with `text_stats`；
- Chat with `note_search`；
- Memory roundtrip；
- Run trace available；
- Claude provider 配置检查；
- Docker Compose 配置检查；
- pytest；
- ruff。

## 对应测试

- `tests/test_api.py`
- `tests/test_agent_runner.py`
- `tests/test_claude_adapter.py`
- `tests/test_memory.py`
- `tests/test_model_factory.py`
- `tests/test_tools.py`
- `tests/test_tui_client.py`
