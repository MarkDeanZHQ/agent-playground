# 05 TUI Guide

## 概念

Textual TUI 是学习控制台，不是生产后台。它把 API、Agent Loop、工具、记忆和验证流程放在同一个终端界面里，帮助学习者边运行边观察。

## 启动

先启动 API：

```bash
uv run uvicorn app.main:app --reload
```

再启动 TUI：

```bash
uv run python -m app.tui.main
```

## 首次打开 TUI 怎么操作

1. `F1` 查看 Dashboard，确认 API、模型、工具、记忆和最近 Run 状态；
2. `F2` 进入 Chat Lab，发送“请统计 hello world”；
3. 在 Chat Lab 按 `t` 查看最近一次 Run Trace；
4. `F4` 进入 Tools Lab，查看 schema 并手动调用工具；
5. `F5` 进入 Memory Lab，检索长期记忆；
6. `F6` 进入 Validation Lab，先运行核心学习闭环，再看附加自检。

每个页面顶部都会显示：页面标题、一句话学习目标、固定的本页核心快捷键提示和动态状态。窗口最底部会显示 `F1`~`F6` 页面标签栏和 Textual `Footer`，当前页面高亮；可用快捷键切换，也可点击标签切换。

## 页面与快捷键

| 快捷键 | 页面 | 学习目标 |
|---|---|---|
| `F1` | Dashboard/总览 | 查看 API、provider、模型健康、工具/记忆/run 概览 |
| `F2` | Chat/对话 | 发送消息，触发 Agent Loop |
| `F3` | Trace/轨迹 | 复盘每一步 trace |
| `F4` | Tools/工具 | 查看和手动调用工具 |
| `F5` | Memory/记忆 | 检索、管理记忆和查看 versions |
| `F6` | Validation/验收 | 运行学习验收台，区分核心闭环、环境配置、开发质量 |
| `r` | 刷新/运行当前项 | 当前页面上下文动作 |
| `l` | Dashboard live check | 真实模型连通性检查 |
| `Enter` | Chat / Memory / Lists | 提交输入或选择项 |
| `Ctrl+N` | Memory Lab 新增记忆 | 读取底部 Editor 内容并调用 `POST /api/v1/memories` |
| `Ctrl+S` | Memory Lab 保存编辑 | 保存当前选中记忆；`superseded` 只读 |
| `Ctrl+X` / `Ctrl+D` / `Ctrl+U` | Memory Lab 归档/软删除/恢复 | 改变当前选中记忆状态并写入版本审计 |
| `Ctrl+Enter` / `i` | Tools Lab 调用工具 | 校验当前 JSON 后调用选中工具 |
| `Esc` / `Ctrl+C` | Chat Lab 取消当前请求 | 取消正在运行的 TUI worker，后端/provider 是否中断取决于底层连接 |
| `c` | 运行核心闭环 | 仅 Validation Lab，建议首次进入先执行 |
| `a` | 运行全部检查 | 仅 Validation Lab |
| `q` | 退出 | 关闭 TUI |

## 界面导览

- **Dashboard｜总览**：显示 API、模型、工具、记忆与最近 Run 状态。页面顶部提示 `r` 刷新和 `l` 真实模型检查；`l` 会请求真实模型供应商，可能产生 token 成本或触发限流。
- **Chat Lab｜对话实验**：左侧 Conversation 展示用户与 Agent 的可读对话；右侧 Live Trace 展示模型请求、工具调用、记忆、摘要和延迟事件。页面顶部提示 `Enter` 发送、`Esc/Ctrl+C` 取消、`t` 查看最近 Trace。`session_summary_used` 会在会话摘要参与上下文时显示。Loading / Done 写入页面状态栏，不刷屏污染对话日志。
- **Run Trace｜执行轨迹**：Runs、Steps、Detail 三栏用于复盘一次 Agent 执行中的 step、summary、tool call 与最终结果。页面顶部提示方向键选择、`Enter` 查看详情、`r` 刷新。没有 runs 时会提示去 Chat Lab 产生一条记录。
- **Tools Lab｜工具实验**：Tools、Learning Panel、Invoke、Result、History 五块区域用于查看工具摘要、参数表、学习点、原始 schema、最近手动调用历史，并编辑 JSON 参数调用工具。页面顶部提示方向键选择、`Ctrl+Enter/i` 调用、`e` 切换示例、`s` 送到 Chat、`t` 最近 Trace、`r` 刷新。优先使用工具定义里的 `examples` 填充参数，没有 examples 时才回退 schema 自动样例。
- **Memory Lab｜记忆实验**：Memories、Detail 与底部 Editor 用于检索长期记忆、查看状态、来源、`use_count`、`last_used_at`、`conflict_key` 和版本变化，并支持手动新增、编辑、归档、软删除和恢复。页面顶部提示 `Enter` 搜索、`Ctrl+N` 新增、`Ctrl+S` 保存、`Ctrl+D` 删除。列表会显示使用次数与冲突分组，Detail 会提示当前排序规则：命中质量优先，其次 importance、使用次数、更新时间。没有记忆时会提示通过 Editor 或 Chat Lab 写入示例记忆。
- **Validation Lab｜学习验收**：Checks 与 Output 用于运行学习闭环检查台。页面顶部提示 `c` 核心闭环、`r` 运行选中项、`a` 运行全部。分为 `Core Path`、`Environment`、`Developer Quality` 三组，建议先按 `c` 跑核心闭环，再按需看环境和质量自检。状态含义为 `? 未运行`、`… 运行中`、`✓ 通过`、`✗ 失败`、`○ 跳过`。

## 空态与错误态

- API 未启动时，页面会提示启动命令：`uv run uvicorn app.main:app --reload`。
- 请求超时、HTTP 状态错误和连接失败会显示不同说明，并保留原始异常。
- Tools Lab JSON 参数错误会显示 `JSON_PARSE_ERROR`、当前工具示例参数和原始解析错误。
- Tools Lab 调用前会做最小 schema 校验，缺少 required、类型明显不对、`minLength` / `minItems` 不满足时会显示 `SCHEMA_VALIDATION_ERROR`。
- Tools Lab 会区分 `JSON_PARSE_ERROR`、`SCHEMA_VALIDATION_ERROR`、`TOOL_EXECUTION_ERROR`、`HTTP_ERROR`，方便学习者分辨是前端参数问题、工具业务错误还是后端请求失败。
- Run Trace、Memory Lab、Tools Lab 的空态会给出下一步操作，而不是只显示 `No ...`。
- Memory Lab 可用 `status:active`、`status:superseded`、`status:archived`、`status:deleted` 过滤状态；`superseded` 是系统历史状态，只读，软删除不是物理删除。非 `active` 记忆不会注入 Agent 上下文，也不会被 `use_count` 统计。
- 长会话超出最近窗口后会先生成 `session_summary`，再把最近消息和长期记忆一起注入上下文。当前 turn 的 user message 不会提前进入摘要。

## Provider 状态

Dashboard 会显示当前 `AGENT_PLAYGROUND_MODEL_PROVIDER`。默认是 `fake`；如果切到 `claude`，会额外显示 Claude 模型和凭证状态。

Dashboard 默认只做静态模型健康检查，避免进入页面就触发真实模型请求。需要验证真实连通性时按 `l`，TUI 会请求 `/api/v1/models/health?live=true`，并显示 `live` 状态、错误摘要和 `Live Check Duration`。

Dashboard 也会展示影响响应速度和工具能力判断的关键配置：`Max Tokens`、`LLM Timeout`、`Max Retries`、OpenAI protocol mode、`OPENAI_TOOL_CALLING`。教学演示如果感觉响应慢或工具验收失败，优先检查：

1. 当前 provider 是否真的可用（按 `l` live check）；
2. `Tool Calling Health` 是否为 `ok`；
3. `AGENT_PLAYGROUND_*_MAX_TOKENS` 是否过大；
4. `AGENT_PLAYGROUND_LLM_TIMEOUT_SECONDS` 与 `AGENT_PLAYGROUND_LLM_MAX_RETRIES` 是否符合本地网络状况。

教学场景可临时降低输出上限：

```env
AGENT_PLAYGROUND_OPENAI_MAX_TOKENS=2048
AGENT_PLAYGROUND_CLAUDE_MAX_TOKENS=2048
```

建议学习顺序：

1. 先用 `fake` 模式理解闭环；
2. 再配置 `claude` 模式观察真实 Tool Use；
3. 最后用 Validation Lab 和 Run Trace 验证行为。

## Validation Lab 检查项

Validation Lab 不是 CI 替代品，而是学习路线最后一站的可解释检查台。检查项分三层：

- `Core Path｜核心学习闭环`
- `Environment｜环境配置`
- `Developer Quality｜开发质量`

当前分组如下：

- Core Path：`api_health`、`chat_no_tool`、`chat_text_stats`、`chat_note_search`、`chat_json_extract`、`chat_todo_roundtrip`、`memory_roundtrip`、`run_trace`
- Environment：`claude_config`、`docker_config`
- Developer Quality：`pytest`、`ruff`

推荐顺序：

1. 先按 `c` 跑 Core Path，确认 API、Agent、记忆和 Trace 主链路。
2. 再看 Environment，判断本地演示条件是否齐全。
3. 最后按 `a` 或单项运行 Developer Quality，查看 `pytest` / `ruff` 这类附加自检。

新增的两个 Core Path 检查用来验证工具学习闭环：

- `chat_json_extract`：确认结构化抽取工具能被 Chat 自动触发；
- `chat_todo_roundtrip`：确认安全副作用工具能先写入再读取，并在 Chat / Trace 中可观察。

Claude 检查默认不发起真实请求，只验证 provider/model/status；当前 provider 不是 `claude` 时会显示“跳过”。真实连通性可以通过 API 的 `/models/health?live=true` 手动触发。

## 对应代码

- `app/tui/main.py`：Textual App 入口与页面绑定。
- `app/tui/client.py`：TUI 只调用 API，不直接访问数据库。
- `app/tui/screens/dashboard.py`：Provider 与概览。
- `app/tui/screens/chat_lab.py`：聊天实验。
- `app/tui/screens/run_trace.py`：执行轨迹。
- `app/tui/screens/tools_lab.py`：工具实验。
- `app/tui/screens/memory_lab.py`：记忆实验。
- `app/tui/screens/validation_lab.py`：验收实验。

## Tools Lab 推荐练法

如果你只是进去点两下按钮，那学不到东西。建议按这个顺序操作：

1. 先看 `text_stats`，理解纯函数工具为什么适合做确定性计算；
2. 切 `json_extract`，理解“模型理解意图，工具输出稳定协议”；
3. 故意删掉 `fields`，看 TUI 如何在调用前拦住 schema 错误；
4. 调 `todo_create` 再调 `todo_list`，理解受控副作用；
5. 按 `s` 把 example 送到 Chat Lab，再去看模型是否真的会自动调工具；
6. 按 `t` 或 `F3` 看最近 Trace，对照手动调用和自动调用的区别。

这套顺下来，工具学习才算闭环，不是只会在面板里发几个 POST。

## 对应测试

- `tests/test_tui_client.py`
- `tests/test_api.py`
- `tests/test_tui_screens.py`
