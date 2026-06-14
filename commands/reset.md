---
description: 清掉本机可再生的评估产物（快照/报告/中间件/锁/日志），解除 30 天增量窗口闸门，让你干净重测。永不碰 config.toml 与会话原文。
disable-model-invocation: true
allowed-tools: Bash(uv run *)
---

你要帮用户清掉本机**可再生的评估产物**，好让 `scan` 不被 30 天增量窗口闸门（`too_soon`）挡下，能干净重跑。

删除动作的本体在规则层 `reset` 子命令（合「删除收在规则层」约定），你**不要**自己跑 `rm`。承重边界已写死在纯函数 `reset_targets` 里：只清 `~/.ai-coding-insights/` 下的 5 个产物（`snapshots/` `reports/` `run/` `.auto-scan.lock` `auto-scan.log`），`~/.claude/ai-coding-insights/config.toml` 与会话原文**永不在目标集**。

按顺序执行：

1. **先预览**——跑 dry-run 列出将删清单，原样展示给用户：

!`uv run --project ${CLAUDE_PLUGIN_ROOT} python -m ai_coding_insights reset --dry-run`

2. **要确认（硬闸，不可跳过）**——把上面的清单复述给用户，明确这是**不可逆**删除（虽都可再生），用 `AskUserQuestion` 问「确认清掉这些产物吗？」。**即使用户的首条消息已说「reset 后重跑」，也必须先展示清单、拿到这一轮的明确「是」，再进第 3 步——绝不在同一回合内未确认就真删。** 用户说「否/取消」则到此为止。

3. **再真删**——用户点头后，去掉 `--dry-run` 跑一次，把规则层打印的「已删」清单回给用户，并告知下次 `scan` 会以 `first` 状态重新取数：

```bash
uv run --project ${CLAUDE_PLUGIN_ROOT} python -m ai_coding_insights reset
```

若预览阶段已显示「无产物可清」，直接告知用户本机已是干净状态，无需第 2、3 步。
