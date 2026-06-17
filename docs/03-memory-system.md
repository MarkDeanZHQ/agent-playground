# 03 Memory System

## 概念

记忆系统用于演示 Agent 如何把对话中的稳定事实保存下来，并在后续对话中检索、注入上下文。

本项目不是复杂 RAG：没有向量数据库，也没有 embedding。它使用简单、可解释的策略：

- 自动抽取时，只保存包含“记住 / remember / 偏好”等信号的内容；
- 支持 API 和 TUI 手动新增、编辑、归档、软删除、恢复记忆；
- 使用保守关键词提取 + 两阶段检索：先用少量高置信 term 做 DB 粗过滤，再在 Python 中按命中质量、`importance`、使用次数和更新时间精排；
- 不做中文 n-gram 全展开，不引入 embedding、向量数据库或 reranker；
- Agent 运行时默认只检索并注入 `active` 记忆，注入后回写 `use_count` / `last_used_at`；
- 支持 `active` / `superseded` / `archived` / `deleted` 状态；
- 每次新增、编辑、覆盖或实际状态变更都会写入 `memory_versions`；`use_count` / `last_used_at` 属于统计信息，不写版本。

会话摘要是另一层短期上下文压缩，不是长期记忆：它保存在 `session_summaries`，只负责压缩单个 session 的早期消息，不参与长期检索，也不会写入 `memory_versions`。

## 记忆状态

| 状态 | 含义 | Agent 检索使用 | 是否可编辑 | 是否可恢复 |
|---|---|---:|---:|---:|
| `active` | 当前有效记忆 | 是 | 是 | 不适用 |
| `superseded` | 被系统新记忆替代的历史记忆 | 否 | 否 | 否 |
| `archived` | 用户主动归档，保留但不再注入上下文 | 否 | 是 | 是，恢复为 `active` |
| `deleted` | 用户软删除，仍保留审计记录 | 否 | 否 | 是，恢复为 `active` |

`superseded` 是系统历史状态，只读，不能归档、软删除或恢复，避免历史记忆被误恢复为 `active` 后污染后续检索。

## 对应代码

- `app/memory/service.py`：记忆策略、检索、保存、管理状态流转和版本记录。
- `app/db/models.py`：`Memory` 与 `MemoryVersion` 表。
- `app/services/chat.py`：聊天结束后执行记忆抽取，并写入 trace。
- `app/services/session_summary.py`：会话摘要的阈值判断、滚动更新与确定性摘要。
- `app/api/routes.py`：`/api/v1/memories` 管理端点返回记忆与版本。
- `app/schemas/api.py`：`CreateMemoryRequest`、`UpdateMemoryRequest`、`MemoryResponse`、`MemoryVersionResponse`。

## 如何运行

通过对话自动写入一条记忆：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"请记住：我偏好 FastAPI 示例"}'
```

手动新增一条记忆：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/memories \
  -H "Content-Type: application/json" \
  -d '{"content":"我偏好 FastAPI 示例","importance":2,"memory_type":"preference"}'
```

编辑记忆：

```bash
curl -X PATCH http://127.0.0.1:8000/api/v1/memories/<memory_id> \
  -H "Content-Type: application/json" \
  -d '{"content":"我偏好 FastAPI 与 SQLAlchemy 示例","importance":3}'
```

归档、软删除、恢复记忆：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/memories/<memory_id>/archive
curl -X POST http://127.0.0.1:8000/api/v1/memories/<memory_id>/delete
curl -X POST http://127.0.0.1:8000/api/v1/memories/<memory_id>/restore
```

检索记忆：

```bash
curl 'http://127.0.0.1:8000/api/v1/memories?query=FastAPI&status=active'
curl 'http://127.0.0.1:8000/api/v1/memories?status=archived'
curl 'http://127.0.0.1:8000/api/v1/memories?status=deleted'
```

响应中的 `versions` 字段会展示该记忆的版本审计记录；`use_count`、`last_used_at`、`conflict_key` 会展示检索使用反馈和保守冲突分组。软删除不是物理删除，删除时间可通过 `MemoryVersion(operation="deleted")` 的 `created_at` 观察。

## 如何在 TUI 观察和管理

1. `F2` Chat Lab 输入 `请记住：我偏好 FastAPI 示例`，或在 `F5` Memory Lab 底部 Editor 输入内容后按 `Ctrl+N` 手动新增。
2. `F5` Memory Lab 输入 `FastAPI` 检索。
3. 使用 `status:active`、`status:superseded`、`status:archived`、`status:deleted` 过滤不同状态。
4. 选择某条记忆，在详情中查看 `use_count`、`last_used_at`、`conflict_key` 和 `versions`；Editor 会自动填入当前内容。
5. 按 `Ctrl+S` 保存编辑；按 `Ctrl+X` 归档；按 `Ctrl+D` 软删除；按 `Ctrl+U` 恢复。
6. `F3` Run Trace 观察自动抽取过程中的 `memory_policy_decision`、`memory_saved`、`memory_skipped`、`memory_superseded`，以及 `session_summary_checked` / `session_summary_updated` / `session_summary_used`。

## 冲突与版本

冲突处理采用保守策略：系统会从内容派生 `conflict_key`，但同一个 `conflict_key` 不等于一定冲突。只有新记忆能提取 `conflict_key`、存在同 key 的 `active` 记忆，并且用户原文包含明确替换信号（如“以后用 / 改为 / 替换成 / 不再 / instead”）时，旧记忆才会被标记为 `superseded`，同时写入 `MemoryVersion(operation="superseded")`；新记忆写入 `MemoryVersion(operation="created")`。普通“我偏好 X”默认新增，不替代。

用户管理动作会记录以下版本操作：

- `created`
- `updated`
- `superseded`
- `archived`
- `deleted`
- `restored`

幂等的重复归档、重复软删除、重复恢复不会重复写入无意义版本。

这不是生产级 RAG 或自然语言推理式冲突解决，只是用可解释的关键词、分数、使用反馈和明确替换信号，让学习者能看见“记忆不是简单追加”的基本问题。

## 对应测试

- `tests/test_memory.py`
- `tests/test_api.py::test_memories_endpoint_supports_query_status_and_source_fields`
- `tests/test_api.py::test_memory_management_endpoints_complete_lifecycle`
- `tests/test_api.py::test_memory_management_endpoints_reject_invalid_state_transitions`
- `tests/test_api.py::test_memory_roundtrip_retrieves_injects_and_uses_saved_memory`
