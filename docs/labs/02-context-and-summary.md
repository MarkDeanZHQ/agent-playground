# Lab 02: 上下文与摘要

> 本 Lab 是学习入口占位版，当前先复用现有参考文档完成闭环，后续再扩展成完整动手实验。

## 你将学到什么

- recent messages、current user message 和 session summary 的职责边界。
- 为什么长会话摘要不等于长期记忆。
- 如何在 Run Trace 里观察 `context_built` 和 `session_summary_*`。

## 先看结果

连续发送多轮消息后，在 Run Trace 中观察：

- `session_summary_checked`
- `session_summary_updated`
- `session_summary_used`
- `context_built`

## 背后的最小原理

上下文工程不是把所有历史消息一股脑塞给模型。当前用户消息、最近消息窗口、会话摘要和长期记忆分别来自不同来源，并在 `context_built` 里留下预算、裁剪和来源解释。

## 代码入口

- `app/services/chat.py`
- `app/services/context_builder.py`
- `app/services/session_summary.py`
- `app/agent/runner.py`

## 动手实验

1. 启动 API 和 TUI。
2. 在 `F2` Chat Lab 连续发送多轮消息。
3. 在 `F3` Run Trace 查看最近 Run。
4. 重点展开 `context_built` 和 `session_summary_*` step。

## 你应该观察什么

- 当前用户消息不会重复进入 recent window。
- session summary 只覆盖旧消息。
- `context_trace` 会解释哪些块被保留、裁剪或丢弃。

## 失败案例

- 把 session summary 当长期记忆用。
- 只看最终回答，不看 `context_built`。
- 看到上下文被裁剪就以为数据丢了，其实要先看预算和裁剪原因。

## 对应测试

- `tests/test_api.py`
- `tests/test_agent_runner.py`

## 延伸阅读

- [`../01-agent-loop.md`](../01-agent-loop.md)
- [`../04-trace-observability.md`](../04-trace-observability.md)
- [`../05-tui-guide.md`](../05-tui-guide.md)
