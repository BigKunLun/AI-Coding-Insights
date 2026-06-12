# 待办事项

> 2026-06-13 首次基线报告审查产出。编号 = 优先级排序，非严格顺序。

---

## 已修复（本次迭代）

- [x] **auto-scan 静默失败** — `_cmd_auto_scan` 向 `decide_window` 传入字符串而非 `date` 对象，`TypeError` 被 `except Exception` 吞掉，auto-scan 永久失效。修复：`date.fromisoformat()` + `now.date()`。（cli.py）
- [x] **时长/深度被微会话拉低** — 中位数 5min、均值 4.6 轮/会话，被大量 1-2 轮微交互拖垮。修复：改用 P90（时长 206min、轮次 12）。（signals.py + report.py）
- [x] **热力图无区分度** — bins `0/1/2-3/4+` 对重度用户全深蓝。修复：`0/1-5/6-12/13-20/21+`，绿阶配色。（report.py）
- [x] **热力图延伸到未来日期** — 周填充未 clamp，显示灰块。修复：`min(end, today)`。（report.py）
- [x] **工具/技能/MCP 图表样式** — 名称列窄被截断，数字列窄显示不全，默认折叠。修复：列宽 `220px 1fr 70px`，字号 13px，默认 `open`。（report.py）
- [x] **报告条形图重复代码** — 三段 ~50 行重复。修复：提取 `_bar_section()`。（report.py）

---

## 待修复

### 1. 落地率分母口径不自洽

**现状**：`landed_ratio = git_landed_count / (git_landed_count + dropped_count)`
- 分子 `git_landed_count`(352) 来自 git log 口径（扫 867 个窗口内提交）
- 分母中 `dropped_count`(36) 来自 transcript 口径（只看到 89 个提交）
- 两个采样框差 10 倍，拼出的 90.7% 没有明确语义

**推荐方案 A**：拆成两个自洽指标，保留旧 `landed_ratio` 向后兼容

| 新指标 | 计算 | 本例值 | 含义 |
|--------|------|--------|------|
| `session_attribution_ratio` | `git_landed / (git_landed + git_outside)` | 40.6% | 窗口内 git 提交，多少是 AI 会话产生 |
| `transcript_landed_ratio` | `landed / commit` | 59.6% | AI 产生的提交，多少存活到 HEAD |

**涉及文件**：`models.py`（加 property）、`cli.py`（`_metrics_dict` 序列化）、`report.py`（切换展示）、`snapshot.py`（`_CORE_KEYS` 同步）

### 2. 高光/证据条数太少

**现状**：高光 3 条、证据 5 条，从 279 条 extractor 素材中仅选 2.9%

**根因**：SKILL.md 写死约束——evidence L3/L4 各 1-2 条，highlights 2-3 条。不是 LLM 偷懒。

**方案**：SKILL.md 放宽 → evidence L3 2-4 条 + L4 2-4 条，highlights 4-6 条。代码层不用动。

### 3. L4 主导占比系统性偏低

**现状**：L4 = 9%（extractor 原始 10.5%，被 AskUserQuestion option_pick 并入 L2 进一步压缩到 9%）

**根因**：SKILL.md 的三重保守偏向
1. 「拿不准就低不就高（L4 拿不准记 L3）」
2. 「防『伪主导』：未验证就放行、放任膨胀、容忍未请求的连带改动，措辞再强也不算 L4」
3. L4 隐式要求「推翻方案并给更优替代」的完整闭环

**证据**：本次抽查发现 28 处明显低判案例——extractor 自己写的 notable_turn 行为描述已明确包含 L4 标志动作（指出系统性缺陷、质疑方案自相矛盾、纠正数值区间假设、推翻方案简化架构），但仍大部分计入 L3。372 次 override 中只有约 48% 被判为 L4。

**方案**：
- 短期：SKILL.md 中给「指出系统性缺陷 / 技术具体性纠错」更高 L4 权重，降低对「必须给出完整替代方案」的要求
- 长期：攒 2-3 个月度 v2 真实分布后按本机人群分位重定四档阈值（当前代码注释已预留此意图）

---

## 已判定非问题

- ~~水平卡片"等"字截断~~ — LLM 自然语言枚举表达，两处"等"后均有语义完整的总括词（"等全链路""等开发方法论与自定义技能"）

---

## 待讨论 / 待深入

- **深度卡片第一点无副标题** — LLM 输出将大段文字塞进 `pt-title`，未按"——"拆分。需确认 SKILL.md 对深度专家的分点格式约束是否明确
- **evidence/obs 交叉验证** — 可抽查几个 L4 判为 L3 的边界案例，人工复议后调整 SKILL.md 的分档指引
- **落地率方案选择** — 方案 A（双指标）vs 方案 B（估算真实丢弃）vs 方案 C（仅重命名），需决策
