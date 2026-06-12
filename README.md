# AI-Coding-Insights

分析你本机的 Claude Code 会话记录，生成四维 AI 协作画像（posture / breadth / depth / outcome）+ 摩擦建议的本地 HTML 报告。形态是 Claude Code plugin，由用户本人手动触发；会话原文与业务语义永不出本机。

## 使用

### 方式一：免安装运行（开发 / 调试推荐）

直接以本仓库为插件目录启动 Claude Code：

```bash
claude --plugin-dir /path/to/AI-Coding-Insights
```

然后在 session 中触发斜杠命令：

```
/ai-coding-insights        # 默认增量窗口（自上次检查以来）
/ai-coding-insights 30     # 可选：只看最近 30 天
```

改完代码重新启动 session 即生效，无需安装。

### 方式二：作为插件安装

```
/plugin marketplace add /path/to/AI-Coding-Insights
/plugin install ai-coding-insights
```

之后任意 session 中用 `/ai-coding-insights` 触发。

报告默认输出到当前工作目录 `aci-report-<日期>.html`。

## 开发

```bash
uv run pytest    # 全量测试（零运行时依赖，dev 仅 pytest）

# 规则层手动调试（正常由 skill 编排调用）
uv run python -m ai_coding_insights scan --plugin-root . --emit-batches /tmp/aci-batches
```

架构与约束详见 [CLAUDE.md](CLAUDE.md)。
