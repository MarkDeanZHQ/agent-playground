# TUI Guide

> 新版完整 TUI 学习说明见 [`05-tui-guide.md`](05-tui-guide.md)。本文件保留为旧链接入口。

## 快速启动

```bash
cd agent-playground
uv run uvicorn app.main:app --reload
uv run python -m app.tui.main
```

## 快捷键

| 快捷键 | 页面 |
|---|---|
| `F1` | Dashboard |
| `F2` | Chat Lab |
| `F3` | Run Trace |
| `F4` | Tools Lab |
| `F5` | Memory Lab |
| `F6` | Validation Lab |
| `r` | 刷新或运行当前选中项 |
| `a` | Validation Lab 运行全部检查 |
| `q` | 退出 |

## 推荐路径

1. 阅读 [`01-agent-loop.md`](01-agent-loop.md)。
2. 在 `F4` Tools Lab 手动调用工具。
3. 在 `F2` Chat Lab 触发工具。
4. 在 `F3` Run Trace 查看 trace。
5. 在 `F5` Memory Lab 查看 memory versions。
6. 在 `F6` Validation Lab 运行所有检查。
