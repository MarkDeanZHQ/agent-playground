# 07 Add Custom Tool

## 概念

新增工具的最小路径是：写一个异步函数，定义 schema，注册到 `ToolRegistry`，然后通过 API / TUI / Chat 验证它能被调用和追踪。

工具应满足三点：

1. 输入必须是 JSON object；
2. 返回值应是字符串，便于模型继续推理；
3. 失败时抛出明确异常，`ToolRegistry` 会记录为 `is_error=true`。

另外建议同步提供：

4. `examples`：至少一个能在 Tools Lab 直接填充的样例；
5. `learning_notes`：说明这个工具教什么，不要把解释塞进 handler；
6. 如果工具有副作用，必须限制在 sandbox 内，不要做任意路径文件操作。

## 步骤 1：实现工具函数

在 `app/tools/builtin.py` 中添加函数，例如：

```python
async def uppercase(arguments: dict[str, Any]) -> str:
    text = str(arguments.get("text", ""))
    if not text:
        raise ValueError("text is required")
    return text.upper()
```

## 步骤 2：注册工具

在 `build_default_registry()` 中注册：

```python
registry.register(
    ToolDefinition(
        name="uppercase",
        description="Call this when the user asks to convert text to uppercase.",
        input_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to convert"}
            },
            "required": ["text"],
        },
        handler=uppercase,
        examples=[{"title": "转成大写", "arguments": {"text": "hello"}}],
        learning_notes=[
            "这是确定性文本转换工具。",
            "description 应该写清楚触发时机，而不只是功能摘要。",
        ],
    )
)
```

工具描述要写“什么时候调用”，不只写“这个工具做什么”。真实 Claude 会根据 `description` 判断是否调用工具。

## 步骤 3：手动验证

```bash
curl http://127.0.0.1:8000/api/v1/tools
curl -X POST http://127.0.0.1:8000/api/v1/tools/uppercase/invoke \
  -H "Content-Type: application/json" \
  -d '{"arguments":{"text":"hello"}}'
```

## 步骤 4：在 TUI 观察

1. `F4` Tools Lab 刷新工具列表。
2. 选择 `uppercase`。
3. 输入 `{"text":"hello"}` 调用。
4. 切到 `F2` Chat Lab，尝试让模型触发工具。
5. `F3` Run Trace 观察 `tool_call` / `tool_result`。

## 步骤 5：补测试

建议至少补两个测试：

- 工具成功：断言返回内容；
- 工具失败：断言 `is_error=true` 或异常被正确记录。

如果你想让新工具真正融入 Tools Lab 学习闭环，至少再补两件事：

- 给 `/api/v1/tools` 返回的定义加上 `examples` 和 `learning_notes`；
- 补一条 TUI helper 测试，确认 example 能填充、错误提示能落到正确分类。

可参考：

- `tests/test_tools.py`
- `tests/test_api.py::test_invoke_tool_returns_result`
- `tests/test_agent_runner.py::test_agent_loop_can_trigger_json_extract_tool`
- `tests/test_tui_screens.py`

## 注意事项

- 不要让工具直接执行危险 shell 命令；
- 不要在工具返回中泄露密钥；
- 文件访问应限制在明确目录中；
- Schema 中尽量写清楚参数 description；
- 如果工具有副作用，先限制 sandbox 边界，再考虑是否需要确认机制；本项目不提供任意文件写入。
- `description` 要写“什么时候调用”，不是只写“它能干什么”；
- `examples` 要能直接拿来跑，别写一堆花活参数把 Tools Lab 自己搞死。
