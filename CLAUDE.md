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

规则层共 5 个子命令，正常由 skill 编排调用，单独调试时也可直接跑（点到存在即可，参数以代码为准）：

- `scan` —— 扫描 / 窗口决策 / 分批 / 硬指标；`--emit-batches` 是编排主路径，四种输出形态互斥（`--emit-batches` / `--profile-input` / `--json` / 默认渲染 HTML）。
- `init` —— 交互配置向导，从本机会话来源勾选团队归属。
- `verify-obs` —— 校验 LLM 观测（obs）对批次的覆盖与 posture 计数完整性。
- `render-profile` —— 渲染最终画像 HTML 报告。
- `auto-scan` —— `SessionEnd` hook 后台自动评估（接线在 `hooks/hooks.json`；自带 lock 防重入 + 滚动日志，失败对用户静默）。

## 架构原则

双层分工，职责不混：

- **规则层**（本仓库 Python）：确定性工作——会话发现与归属判定、解析、硬指标计算、渲染。凡是能用规则算的，不交给 LLM。
- **LLM 层**（`skills/` 下的 SKILL.md，编排用户自己的 cc）：只做语义判定，产出结构化数据；像素（HTML）一律由脚本渲染。

两层之间靠**文件契约**衔接，**改任何接缝必须两侧同步**：动了 CLI 输出或 schema，就要同步改 SKILL.md，反之亦然。当前接缝有名有姓（文件名即契约，改名或改字段须同步 SKILL.md）：

- **manifest**：`scan --emit-batches` 的 stdout JSON。
- **中间 JSON**（落 `--emit-batches` 目录）：`batch-NN.json`（LLM 分批输入）、`obs-*.json`（extractor 产出）、`_window.json`、`_aggregate.json`（已剥掉含项目名的 `project_breakdown`，含 `parse_health` / `customization_signals` 等字段）、`profile.json`（合成画像）。
- **CLI 参数**：`render-profile` 的 `--metrics` / `--window` / `--obs-glob` / `--run-*`。
- **profile schema**：`profile_schema.py`。

## 不变约束（定位级，违反即 bug）

- **隐私**：会话原文与业务语义永不出本机。所有进入画像/证据/建议的自由文本只描述**行为模式与量级**，绝不含客户/功能/产品/架构等业务内容。承重机制（改动即可能捅破隐私网）：归属边界单点在 `Config.discovery_rules`；`_aggregate.json` 与跨次快照主动剥离含项目名的 `project_breakdown`；批次出规则层前过 `redact` 密钥网。
- **人在环**：机器不下最终判决、不自动定奖惩；产出是软信号初筛 + 可验证证据入口。
- **硬成果可验证**：与奖励挂钩的指标必须基于可独立验证的硬证据，不依赖 LLM 判断。落地率为 git 主锚口径——按 git author 历史 + 本机 `user.email` 归属到会话时间窗，独立于 transcript 与 LLM，规则以 `git_outcome.py` 为准。
- **归属宁漏勿误**：公司项目判定不确定的一律不纳入，私人会话从机制上进不来。

数值阈值与具体策略（窗口、分批、判定规则等）以代码为准，不在本文档复述。

## 工程规范

- 先写测试再实现；决策逻辑写成无 IO 纯函数，便于直接测试。
- commit message 与文档命名用中文。
