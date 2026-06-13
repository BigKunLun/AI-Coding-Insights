#!/usr/bin/env bash
# AI-Coding-Insights · SessionEnd 后台自动评估钩子
#
# 由本插件 hooks/hooks.json 的 SessionEnd 事件注册，会话结束后在后台静默触发一次
# 增量评估（auto-scan 子命令）。
#
# best-effort 契约：找不到 uv、本机无 transcript、扫描异常——一律静默 exit 0，
# 绝不阻塞会话退出、绝不打扰用户。auto-scan 子命令自带防护：
#   - lock 文件：同一天只跑一次（~/.ai-coding-insights/.auto-scan.lock）
#   - 滚动日志：真实失败原因落 ~/.ai-coding-insights/auto-scan.log，事后可诊断
#   - 空扫描不推进游标：无新增会话时不写快照，下次窗口仍完整
# 因此本脚本无需回传任何状态，吞掉全部输出即可。
#
# CLAUDE_PLUGIN_ROOT 由 Claude Code 在 hook 执行时注入为插件安装目录的绝对路径；
# 缺省时（手动调试）回退到本脚本上级目录。
ROOT="${CLAUDE_PLUGIN_ROOT:-"$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"}"
REPORT_DIR="${HOME}/.ai-coding-insights/reports"
UV="$(command -v uv 2>/dev/null)"
{
  if [ -n "$UV" ]; then
    "$UV" run --project "$ROOT" python -m ai_coding_insights auto-scan \
      --out-dir "$REPORT_DIR" --plugin-root "$ROOT"
  fi
} >/dev/null 2>&1
exit 0
