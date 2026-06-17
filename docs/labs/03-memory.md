# Lab 03: 长期记忆

> 本 Lab 是学习入口占位版，当前先复用现有记忆文档和教程完成闭环，后续再扩展成完整动手实验。

## 你将学到什么

- 记忆如何写入、召回和注入上下文。
- `active`、`superseded`、`archived`、`deleted` 等状态有什么区别。
- 如何观察记忆冲突、替代和使用反馈。

## 先看结果

发送：

```text
请记住：我偏好 FastAPI 示例
```

再发送一个相关问题。你应该能在 Run Trace 中看到：

- `memory_policy_decision`
- `memory_saved` 或 `memory_skipped`
- `memory_retrieved`
- `context_built`

## 背后的最小原理

长期记忆不是把全部聊天记录永久塞进 prompt。项目会抽取候选记忆，按 scope、category、confidence、状态和关键词召回，再把可见的 `active` 记忆注入上下文。

## 代码入口

- `app/memory/service.py`
- `app/db/models.py`
- `app/services/context_builder.py`
- `app/tui/screens/memory_lab.py`

## 动手实验

1. 在 Chat Lab 写入一条偏好。
2. 到 Memory Lab 检索并查看这条记忆。
3. 再发一个相关问题触发召回。
4. 到 Run Trace 查看 `memory_retrieved` 和 `context_built`。

## 你应该观察什么

- 召回解释里的 `terms`、`score`、`matched_terms` 和 `rank_signals`。
- 记忆 scope 是否符合预期。
- 非 `active` 记忆不会注入 Agent 上下文。
- `use_count` 和 `last_used_at` 是否更新。

## 失败案例

- 关键词没命中导致记忆没召回。
- session 级记忆换 session 后不可见。
- 归档或软删除后还期待它进入上下文。
- 把当前轻量关键词检索误认为 embedding 检索。

## 对应测试

- `tests/test_api.py::test_memory_roundtrip_retrieves_injects_and_uses_saved_memory`
- `tests/test_tui_screens.py`

## 延伸阅读

- [`../03-memory-system.md`](../03-memory-system.md)
- [`../tutorials/02-memory-roundtrip.md`](../tutorials/02-memory-roundtrip.md)
- [`../04-trace-observability.md`](../04-trace-observability.md)
