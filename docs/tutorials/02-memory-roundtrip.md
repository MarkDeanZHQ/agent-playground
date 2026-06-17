# 记忆往返实验

目标：理解 Memory 的保存、检索、注入和可观察 trace。

## 1. 写入记忆

在 Chat Lab 输入：

```text
请记住：我偏好 FastAPI 示例
```

## 2. 查询记忆

按 `F5` 进入 Memory Lab，输入：

```text
FastAPI status:active
```

观察字段：

- `content`
- `memory_type`
- `importance`
- `status`
- `source_message_id`
- `created_at`
- `updated_at`

## 3. 触发检索注入

回到 Chat Lab，输入：

```text
我喜欢什么后端示例？
```

观察 Live Trace 中的：

- `memory_retrieval_started`
- `memory_retrieved`
- `memory_used`

## 4. 观察不保存原因

输入：

```text
请记住我的 API key 是 abc
```

再查看 Run Trace，观察：

- `memory_extraction_started`
- `memory_policy_decision`
- `memory_skipped`

重点：Memory 系统不是“什么都存”，而是由策略决定是否保存。
