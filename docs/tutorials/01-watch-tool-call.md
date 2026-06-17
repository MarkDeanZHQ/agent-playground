# 工具调用实验

目标：理解 Tool 的 schema、手动调用和 Agent 自动调用之间的关系。

## 1. 查看工具列表

在 TUI 按 `F4` 进入 Tools Lab。

观察：

- `name`
- `description`
- `input_schema.properties`
- `input_schema.required`
- `examples`
- `learning_notes`

## 2. 手动调用 text_stats

输入：

```json
{"text":"hello world"}
```

期望结果：

```text
characters=11, lines=1, words=2
```

## 3. 观察失败

选择 `note_search`，输入：

```json
{"query":""}
```

期望结果：

```json
{"is_error": true}
```

重点：工具失败不会让服务崩溃，而是作为可观察结果返回。

## 3.1 观察调用前校验

选择 `json_extract`，故意输入：

```json
{"text":"name: Alice"}
```

期望结果：

- Tools Lab 在真正发请求前提示 `SCHEMA_VALIDATION_ERROR`
- 能看到缺少 `fields` 的提示
- 能看到当前工具的 example 参数

## 4. 在 Chat Lab 触发工具

按 `F2`，输入：

```text
请统计 hello world
```

观察 Live Trace：

- `model_turn`
- `tool_call`
- `tool_result`
- `run_finished`

## 5. 对比

- 手动调用 `/tools/{name}/invoke` 和 Agent 自动触发工具，底层都返回 `ToolCallResult` 结构；
- 但手动调用不会伪装成 Agent Run，不会污染 Run Trace 语义；
- 用 `s` 把 Tools Lab 里的示例送到 Chat，再用 `F3` 看最近 Trace，最容易看懂这两条链路的区别。

手动调用工具和 Agent 自动调用工具，最终都返回同一种 `ToolCallResult` 结构。
