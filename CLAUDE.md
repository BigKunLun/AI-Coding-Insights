# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目定位

评估一个人对 AI 编码工具的使用情况：分析其本机的 Claude Code 会话记录，做深度分析，给出画像与改进建议。形态是 Claude Code plugin，由用户本人手动触发，产出本机报告。**机器只给分析与证据，结论与奖惩判决在人。**

## 常用命令

```bash
uv run pytest                                  # 全量测试
uv run pytest tests/test_window.py             # 单文件
uv run pytest tests/test_window.py::test_xxx   # 单用例

# 规则层手动调试（正常由 skill 编排调用）
uv run python -m ai_coding_insights scan --plugin-root . --emit-batches /tmp/aci-batches
```

零运行时依赖（纯 stdlib），dev 仅 pytest。

## 架构原则

双层分工，职责不混：

- **规则层**（本仓库 Python）：确定性工作——会话发现与归属判定、解析、硬指标计算、渲染。凡是能用规则算的，不交给 LLM。
- **LLM 层**（`skills/` 下的 SKILL.md，编排用户自己的 cc）：只做语义判定，产出结构化数据；像素（HTML）一律由脚本渲染。

两层之间靠**文件契约**衔接（manifest、中间 JSON、CLI 参数、profile schema）。**改任何接缝必须两侧同步**：动了 CLI 输出或 schema，就要同步改 SKILL.md，反之亦然。

## 不变约束（定位级，违反即 bug）

- **隐私**：会话原文与业务语义永不出本机。所有进入画像/证据/建议的自由文本只描述**行为模式与量级**，绝不含客户/功能/产品/架构等业务内容。
- **人在环**：机器不下最终判决、不自动定奖惩；产出是软信号初筛 + 可验证证据入口。
- **硬成果可验证**：与奖励挂钩的指标必须基于可独立验证的硬证据（如 git 历史），不依赖 LLM 判断。
- **归属宁漏勿误**：公司项目判定不确定的一律不纳入，私人会话从机制上进不来。

数值阈值与具体策略（窗口、分批、判定规则等）以代码为准，不在本文档复述。

## 工程规范

- 先写测试再实现；决策逻辑写成无 IO 纯函数，便于直接测试。
- commit message 与文档命名用中文。
