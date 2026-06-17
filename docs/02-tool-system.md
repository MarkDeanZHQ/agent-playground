# 02 Tool System

## 概念

工具系统把“模型想做的动作”和“本地代码真正执行的动作”隔离开：模型只返回结构化的 `tool_use` / `tool_call`，`ToolRegistry` 负责按名称找到安全、可测试的本地函数并返回 `tool_result`。

本项目保留一组受控内置工具，便于学习：

| 工具 | 作用 | 典型触发 |
|---|---|---|
| `text_stats` | 统计字符、行数、词数 | “请统计 hello world” |
| `note_search` | 搜索 `sandbox/notes/*.md` 示例笔记 | “搜索 demo 笔记” |
| `json_extract` | 按字段列表从文本中抽取简单 JSON | “从这段文本提取姓名和城市” |
| `todo_create` | 写入一个本地 sandbox todo | “创建一个学习任务” |
| `todo_list` | 读取本地 sandbox todo 列表 | “查看 todo 列表” |

四类教学定位：

- 纯函数工具：`text_stats`
- 受控检索工具：`note_search`
- 结构化抽取工具：`json_extract`
- 安全副作用工具：`todo_create` / `todo_list`

`ToolDefinition` 现在除了 `name`、`description`、`input_schema` 外，还包含：

- `examples`：给 Tools Lab 一键填充参数，不参与真实执行；
- `learning_notes`：说明教学目的，不参与模型调用决策。

## 安全边界

- 模型只产生结构化工具调用请求，是否执行、执行哪个函数由 `ToolRegistry` 决定。
- 当前内置工具不提供 shell、网络抓取、生产 API 调用、系统配置修改或任意路径读写。
- `note_search` 的读取范围限制在 `AGENT_PLAYGROUND_SANDBOX_DIR` 对应目录下的 `*.md` 文件，默认是 `sandbox/notes/*.md`。
- `todo_create` / `todo_list` 的副作用限制在 `sandbox/todos.json`，这是教学用本地状态，不是通用存储层。
- 工具异常会被转换成 `ToolCallResult(is_error=True)` 并进入 trace，避免工具错误直接变成不可观察的 500。

## 对应代码

- `app/tools/registry.py`：`ToolDefinition` 与 `ToolRegistry`。
- `app/tools/builtin.py`：内置工具实现和 `build_default_registry()`。
- `app/agent/runner.py`：收到 `ModelTurn(kind="tool_call")` 后执行工具。
- `app/models/adapters.py`：把 Fake / Claude / OpenAI 的工具调用统一成 `ToolCallRequest`。
- `app/schemas/api.py`：`ToolCallRequest`、`ToolCallResult`、`ToolDefinitionResponse`。

## 如何运行

查看工具定义：

```bash
curl http://127.0.0.1:8000/api/v1/tools
```

手动调用工具：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/tools/text_stats/invoke \
  -H "Content-Type: application/json" \
  -d '{"arguments":{"text":"hello world"}}'
```

通过 Chat 触发工具：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"请统计 hello world"}'
```

## 如何在 TUI 观察

1. `F4` 进入 Tools Lab。
2. 左侧选择 `text_stats` 或 `json_extract`。
3. 右侧查看 Summary、Parameters、Learning notes 和 Raw schema。
4. 按 `e` 切换 example，或手动输入 JSON 参数后调用工具。
5. 故意删掉 required 字段，观察 `SCHEMA_VALIDATION_ERROR`。
6. 按 `s` 把当前示例送到 `F2` Chat Lab，观察模型是否自动调用工具。
7. `F3` Run Trace 中观察 `tool_call`、`tool_result`、`model_tool_use`。

## Tools Lab 学习闭环

建议按这个顺序走一遍，不然只会看热闹看不懂门道：

1. `F4` Tools Lab 先看 `text_stats`，理解最小 schema 和纯函数工具；
2. 切到 `json_extract`，看结构化 JSON 返回长什么样；
3. 故意删掉 `fields`，观察 `SCHEMA_VALIDATION_ERROR`；
4. 切到 `todo_create`，调用一次，再用 `todo_list` 读回来；
5. 按 `s` 把当前 example 送到 Chat Lab；
6. 在 `F2` 发送消息后，按 `F3` 看 Run Trace；
7. 对照手动调用历史和自动 `tool_call` / `tool_result`，理解两条链路的边界。

关键认知：

- schema 不只是给前端看的，它同时约束手动调用和模型调用；
- `description` 写的是“什么时候该调”，不是简单功能名；
- `examples` 和 `learning_notes` 只服务教学与 TUI，不参与后端执行；
- 手动 `/tools/{name}/invoke` 不会伪装成 Agent Run；
- 工具失败不是系统崩溃，而是可观察结果。

## 对应测试

- `tests/test_tools.py`
- `tests/test_api.py::test_tools_endpoint_lists_default_tools`
- `tests/test_api.py::test_tool_invoke_endpoint_returns_success_and_observable_error`
- `tests/test_agent_runner.py::test_agent_loop_can_trigger_json_extract_tool`
- `tests/test_agent_runner.py::test_agent_loop_can_trigger_todo_side_effect_tools`
