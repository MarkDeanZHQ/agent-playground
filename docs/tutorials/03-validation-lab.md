# Validation Lab 实验

目标：把学习路线最后一段收成闭环，先确认主链路，再区分环境问题和开发质量问题。

## 1. 打开页面

按 `F6` 进入 Validation Lab｜学习验收。

页面会把检查项分成三组：

- `Core Path｜核心学习闭环`
- `Environment｜环境配置`
- `Developer Quality｜开发质量`

## 2. 先运行核心闭环

按 `c` 或点击 `Run core path`。

建议顺序：

1. `api_health`
2. `chat_no_tool`
3. `chat_text_stats`
4. `chat_note_search`
4. `memory_roundtrip`
5. `run_trace`

这一步的目的不是“全绿才算完”，而是先判断 API、Agent、记忆、Trace 这条学习主链路是不是活的。

## 3. 运行单项检查

选择一个检查项，按 `r` 或点击 `Run selected`。

适合用来追某个失败点，不用每次都重跑全部。

## 4. 运行全部检查

按 `a` 运行全部检查。

其中：

- Core Path：通过 HTTP 调用后端，验证主链路。
- Environment：检查 `claude` 配置和 Docker Compose 配置。
- Developer Quality：执行 `uv run pytest` 和 `uv run ruff check .`。

`pytest` 和 `ruff` 只是开发质量附加检查，不是核心学习路径的第一优先级。

## 5. 失败时怎么看

Validation Lab 不隐藏失败：

- 会先给一句结论；
- 再给原始详情；
- 最后给下一步建议。

常见判读方式：

- Core Path 失败：优先看 API、Agent、记忆、Trace 主链路。
- Environment 失败：优先看 provider、凭证或 Docker 本地环境。
- Developer Quality 失败：优先看测试输出和 lint 结果。

这符合本项目“学习优先”的目标：Validation Lab 是学习验收台，不是假装成 CI 的大杂烩。
