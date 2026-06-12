---
description: Analyze your own Claude Code sessions (all of them by default, or scoped to your team's repos via config) with a three-stage agent-team (extract → four dimension experts + a coach → synthesize) and produce a local four-dimension AI-coding dashboard (posture/breadth/depth/outcome) plus friction-and-advice, over an incremental window since your last check.
disable-model-invocation: true
argument-hint: "[days]"
allowed-tools: Bash(uv run *), Bash(date *), Agent, Read, Write
---

你要用**三阶段 agent-team** 为当前用户生成一份**四维 AI 协作画像 + 摩擦建议**（仅本机），取数为**自上次检查以来的增量窗口**。

## ⚠️ 脱敏铁律（贯穿全程，每个 subagent 都必须遵守）

你写入画像 / 证据 / 摩擦建议 / 小结的**所有自由文本**（`headline`、`points`、`metrics.label`、`frictions.observation/suggestion`、`evidence.behavior`、`highlights.behavior`）只描述**行为模式与量级**，**绝不**引用业务内容（客户名 / 功能名 / 产品方向 / 架构细节 / 含业务词的文件名）。原文与业务语义**永不出本机**。
- ✓「推翻 AI 一处实现方案并给出更优约束」「累计 N 次提交、落地率 X%」
- ✗「重构虚拟手环数据服务」「支付通道限流实现」

## 1. 扫描 + 窗口决策 + 分批（预处理注入）

记下运行起始时刻（下面输出记为 `<RUN_STARTED>`，第 4 步渲染时透传，报告页脚据此标注本次运行耗时）：

!`date -u +%Y-%m-%dT%H:%M:%SZ`

规则层扫描——窗口决策、纳入范围内会话分批、硬指标都已算好，并顺带清掉上一轮的中间产物残留（旧 obs/profile，批次划分一变专家会静默读到张冠李戴的数据）。`aggregate` 是硬指标，`window` 是取数窗口；纳入范围默认全部本机会话，团队配置了 include 模式则只含团队项目：

!`uv run --project ${CLAUDE_PLUGIN_ROOT} python -m ai_coding_insights scan --plugin-root ${CLAUDE_PLUGIN_ROOT} --emit-batches /tmp/aci-batches`

后续步骤里的 `<PLUGIN_ROOT>` 一律取上面清单的 `plugin_root` 字段（运行时 `${CLAUDE_PLUGIN_ROOT}` 是空的，**不要**再用它）。

**按此顺序判清单 `status`（逐条匹配，命中即停）：**
1. `status == "too_soon"`：把 `message` 原样告诉用户后**停止**——不派 subagent、不渲染。
2. 否则 `batch_count == 0`：告知「该窗口内没有可纳入的会话」并停止。
3. `first` / `ok`：继续。记住 `window`（第 4 步透传给渲染）；若 `window.truncated` 为 `true`，第 5 步小结必须提醒用户。

## 2. 阶段一 · 提取（每批派一个 extractor）

对清单 `batches` 里**每个** batch 文件，用 **Agent 工具**派一个 extractor（可并行）。prompt（替换 `<BATCH_FILE>` / `<NN>`，`<NN>` 取文件名两位序号）：

```
你是 extractor。脱敏铁律：behavior 只描述行为模式，绝不含业务内容（客户/功能/产品/架构）。
用 Read 读 <BATCH_FILE>（JSON 数组，每元素一个会话：session_id/cwd/file_path/signals/turns，turns 是该会话的完整真人输入，每条有 uuid/chars/text/anchors）。
通读全部 turns 做两件事，用 Write 写 /tmp/aci-batches/obs-<NN>.json，结构严格如下：
{"sessions":[
  {"session_id":"...","file_path":"...","signals":<该会话的 signals 原样带上>,
   "posture_counts":{"L1":<n>,"L2":<n>,"L3":<n>,"L4":<n>},
   "notable_turns":[
     {"pointer":"<该会话 file_path>#<该 turn 的 uuid>","anchors":<该 turn 的 anchors>,
      "kind":"posture 或 friction",
      "behavior":"<这条真人输入体现的行为模式，一句话（30 字内），行为级脱敏>"}
   ]}
]}
事一 · 逐条分档（posture_counts）：对该会话【每一条】turn 判定一档并计数，四档总和必须等于该会话 turns 条数（规则层逐会话校验，不符会整批重派）：
- L1 跟随：纯放行/确认/客套，没有在 AI 已给信息之外增加任何信息（「好的」「继续」「就这么办吧」「go ahead」，无论长短）。
- L2 选择：从 AI 给出的选项里挑一个，不加新约束（「用方案 A」「第二个吧」）。
- L3 引导：主动给目标/约束/格式/范围，贴报错或日志追问，递进式追问。只给目标不给约束的普通指令（「帮我修 X」）算 L3 下沿。
- L4 主导：技术具体性纠错、推翻方案并给更优约束、给 AI 没想到的边界、要求自验或给验收判据、先要方案评审再放行、编排多 subagent 或自建扩展。
判档看语义不看长度：「改成异步」4 个字是 L4；「好的，就按你说的办吧」9 个字是 L1。防「伪主导」：未验证就放行、放任膨胀、容忍未请求的连带改动，措辞再强也不算 L4。拿不准就低不就高（L4 拿不准记 L3，L3 拿不准记 L1）。
事二 · notable_turns 选材（每条带 kind）：
- kind="posture"：有 L3/L4 判定价值的 turn——命中 anchors、明显主导/纠错（判据同上）。
- kind="friction"：摩擦时刻——同一问题反复修而不收敛、报错贴了又贴、推倒重来、长拉锯无收口、人机互相误解空转。
看不出行为价值的 turn 直接不选，不要勉强归类。
pointer 的 uuid 必须原样取自该 turn 的 uuid 字段，**绝不能拿 session_id 充当**——渲染端会逐条核验指针真伪，伪指针会在报告中公开标注。
⚠️ batch 里的【每个会话】都必须出现在 sessions 里——没有可记录 turn 的也要列入并令 "notable_turns":[]（空数组占位）；posture_counts 每个会话必填，无输入会话填全零。
只回复 Write 成功的确认，不要复述内容。
```

派完后用 **Bash 工具**运行覆盖校验（`<PLUGIN_ROOT>` 换成清单 `plugin_root`，glob 必须带引号）：

```
uv run --project <PLUGIN_ROOT> python -m ai_coding_insights verify-obs --batches /tmp/aci-batches --obs-glob '/tmp/aci-batches/obs-*.json'
```

- `ok`：进入下一步。
- `missing` 或 `posture_invalid` 非空：对所列 batch 文件（`posture_invalid` 条目带 `file` 字段指向所属 batch）**重派** extractor（同 prompt，Write 覆盖重写对应 obs）并重跑校验，最多 2 轮；仍有问题则告知用户并停止。
- `orphans` 或 `unreadable` 非空：有写坏的 obs——从本步开头重派**全部** extractor（同 prompt，Write 覆盖重写对应 obs 文件）并重跑校验（仅一次机会，再失败则告知用户并停止）。

校验通过前**不得**进入阶段二——专家读到错配的 obs 不会报错，只会安静产出错误结论。

## 3. 阶段二 · 五专家并行（四维度 + 一教练）

用 **Agent 工具**并行派 **5 个专家**。每个先用 **Read** 读**全部** `/tmp/aci-batches/obs-*.json`，结合清单 `aggregate` 产出结论。`aggregate`/`window` 数据**已由第 1 步落盘**为 `/tmp/aci-batches/_aggregate.json` 与 `_window.json`，专家需要时直接 Read——你**不要**再写这两个文件。**脱敏铁律必守。产出结构化字段、不写散文长句。**

**专家共同纪律（写进每个专家的 prompt）**：
- 产出只通过返回值回传，**不要写任何文件**。
- 每个数字必须直接取自 `aggregate` 字段或 obs 原文，禁止自创推算——尤其不得从均值推断分布（「平均 0.5 次/会话」推不出「过半会话遇到过」，错误可能集中在少数会话）。
- 只陈述本窗口数据，不外推普适规律（「经典 S 曲线」「任何新项目前 N 个会话 ROI 接近零」之类一律不写）；不给行为编动机或定性（「轻量级使用」「有成本意识」是数据里没有的因果）。
- 引用 `model_counts` 等枚举字段必须**全量**陈述或明说省略口径，不得只挑部分（漏报第二名即失真）。
- `evidence`/`highlights` 的 pointer 只能从 obs 的 notable_turns **原样拷贝**；会话级观察的 pointer 只写 `file_path`（不带 `#uuid`），绝不能拿会话 id 冒充 turn uuid——渲染端会逐条核验指针真伪，未命中会在报告中公开标注。

- **证据专家**：L1-L4 四档分布已由规则层从 obs 的 posture_counts 聚合组装，你**不要**估任何占比、不要返回任何姿势数字。你只做证据精选：通读 kind="posture" 的 notable_turns，挑最有判定价值的 L3/L4 证据与最佳实践。判据：L3 引导（主动给目标/约束/格式、贴报错、追问递进）、L4 主导（技术具体性纠错、推翻方案、给 AI 没想到的约束、要求自验或给验收判据、先要方案再放行、编排多 subagent 或自建扩展）。防「伪主导」：未验证就放行、放任代码膨胀、容忍未请求的连带改动——措辞再强也不算 L4。无可选素材时返回空数组，不要编造。返回 `{"evidence":[{"pointer","behavior"}...L3/L4 各 1-2 条], "highlights":[{"pointer","behavior"}...2-3 条，挑技术具体性最强的最佳 L4 实践]}`。
- **水平专家**：基于 `aggregate` 的 `tool_breadth` / `tool_session_counts` / `subagent_sessions` / `workflow_sessions` / `mcp_sessions` / `model_counts`，返回 `{"breadth":{"headline":"一句话定调","points":["2-4 条要点，行为+量级"],"metrics":[{"label":"工具广度","value":<n>},{"label":"SubAgent会话","value":<n>}],"tools":["工具能力短语..."]}}`。
- **深度专家**：看多轮打磨、纠错的技术具体性、失败→恢复链（notable_turns 行为 + `aggregate.anchor_counts`）。每条 point 要挖到「意味着什么 / 该怎么调」，不止「做了什么」。metrics 的数字直接取硬指标：override 取 `aggregate.anchor_counts.override`，轮次/会话取 `aggregate.avg_turns`，不要自己从 notable_turns 数。返回 `{"depth":{"headline":"..","points":["..带 SO-WHAT.."],"metrics":[{"label":"override","value":<n>},{"label":"轮次/会话","value":".."}]}, "evidence":[{"pointer","behavior"}...]}`。
- **成果专家**：照实用 `aggregate` 的 `git_landed_count`（主锚：git 历史硬证据，窗口内本人提交且落入会话时间窗）/`git_outside_count`（窗口内本人提交但在会话窗外，仅参考）/`commit_count`（会话内可观测提交）/`dropped_count`（观测到但已不在分支历史）/`edit_count`/`landed_ratio`，只讲量级与落地节奏。⚠️ `commit_count` 为 0 而 `git_landed_count` > 0 时，说明本机会话记录不含提交回执（CC 版本差异），是**测不到**不是**没提交**，绝不能写成「无提交收口」「落地为零」。返回 `{"outcome":{"headline":"..","points":[".."],"metrics":[{"label":"落地提交","value":<git_landed_count>},{"label":"观测丢弃","value":<dropped_count>}],"landed":<git_landed_count>,"total":<git_landed_count + dropped_count>}}`。
- **教练 / 诊断专家**：读全部 notable_turns（重点 kind="friction"）+ `aggregate`（含 `friction_stats`：error/override 的命中会话数与单会话 top3 计数、轮次最长 top3，专为「集中于少数会话」类判断提供确定性分布，不必再从均值猜）。识别**协作摩擦**，每条 = 行为级观察 + 指针 + 可执行建议：观察必须写出依据的数字（取自 `aggregate`/`friction_stats`/各会话 `signals`，禁止自创推算）；`pointers` 给 1-3 个，从 kind="friction" 的 notable_turns **原样拷贝** pointer，会话级观察可用该会话 file_path（不带 #uuid），无指针也无数字支撑的观察直接不要写；建议必须具体到「什么场景下、做什么动作、怎么验证有效」，禁止「建议小步提交」「建议明确需求」这类对谁都成立的话。下面只是常见方向**示例**，优先写本窗口数据里最突出、最个性化的模式，宁缺勿滥：反复返工（`edit_count` 相对 `git_landed_count` 明显偏高）、error 集中（`friction_stats.error_top_counts` 对照 `error_session_count`/`session_count`）、override 集中于少数会话（`friction_stats.override_top_counts`）、单会话轮次远超 `avg_turns` 且无 commit 收口（此判据仅当 `aggregate.commit_count` > 0 即会话内提交可观测时可用；`commit_count` 为 0 而 `git_landed_count` > 0 时本机会话不含提交回执，凡依赖会话级 commit 的判据一律不用）。工具覆盖盲区不归你管（规则层已单独计算渲染）。返回 `{"frictions":[{"observation":"..","pointers":["/abs/path.jsonl#uuid"],"suggestion":".."}...1-5 条]}`。

## 4. 阶段三 · 合成 + 渲染

汇总五专家产出，用 **Write 工具**写 `/tmp/aci-batches/profile.json`（结构严格如下；L1-L4 四档分布由渲染命令直接从 obs 聚合组装，画像里**不含任何姿势字段**）：

```json
{"breadth":{"headline":"…","points":["…"],"metrics":[{"label":"…","value":28}],"tools":["…"]},
 "depth":{"headline":"…","points":["…"],"metrics":[{"label":"…","value":149}]},
 "outcome":{"headline":"…","points":["…"],"metrics":[{"label":"…","value":49}],"landed":39,"total":49},
 "frictions":[{"observation":"…","pointers":["/abs/path.jsonl#uuid"],"suggestion":"…"}],
 "evidence":[{"pointer":"/abs/path.jsonl#uuid","behavior":"行为级描述"}],
 "highlights":[{"pointer":"/abs/path.jsonl#uuid","behavior":"行为级最佳实践描述"}]}
```
（`evidence` 汇集证据/深度专家精选证据——**必须非空**，空列表渲染校验直接失败；`highlights` 取证据专家的最佳 L4 实践 2-3 条，各维 `headline/points` 取对应专家产出，全部行为级。`outcome.landed/total` 固定取 `git_landed_count` 与 `git_landed_count + dropped_count`——git 主锚口径，不要再填 `commit_count`。）

**发布前自检**：重读你写的**所有** `headline`、`points`、`metrics.label`、`frictions.observation/suggestion`、`evidence.behavior`、`highlights.behavior`（与脱敏铁律同一份字段清单），逐条确认无业务方向词（客户/功能/产品/架构/含业务词的文件名）。有则改写成纯行为级再继续。

然后用 **Bash 工具**运行下面这条**单条**命令（`<PLUGIN_ROOT>` 换成清单 `plugin_root`，`<N>` 换成清单 `aggregate.session_count`，并为清单 `included_projects` 里**每个**项目追加一个 `--project <路径>`）：

```
uv run --project <PLUGIN_ROOT> python -m ai_coding_insights render-profile --plugin-root <PLUGIN_ROOT> --profile /tmp/aci-batches/profile.json --metrics /tmp/aci-batches/_aggregate.json --window /tmp/aci-batches/_window.json --obs-glob '/tmp/aci-batches/obs-*.json' --session-count <N> --project <项目1> --project <项目2> --run-started <RUN_STARTED> --run-agents <AGENT_N>
```

运行元信息两个参数（进报告页脚「本报告由 … 生成 · 运行约 … 分钟 · 编排 … 个 agent」，各自可整体省略，**不确定就省略，不要编造**）：`<RUN_STARTED>` 填第 1 步记下的起始时刻；`<AGENT_N>` 填本次实际派出的 subagent 总数（extractor 含重派 + 5 个专家）。页脚模型名由规则层从会话记录自动识别，**不要传任何模型参数**；不传 `--out`，报告自动落当前工作目录 `aci-report-<日期>.html`，成功时 stdout 最后一行即实际路径。

若 stderr 出现「证据指针未命中」警告：报告已照常生成并在对应证据行标注 ⚠，无需重跑；在小结里如实告知用户哪几条证据指针未能回看。

若 stderr 报「画像校验失败：…」，按提示用 Write 重写 `/tmp/aci-batches/profile.json` 后重跑，最多 3 次。

## 5. 小结

成功后把渲染命令 stdout 输出的报告路径（当前目录 `aci-report-<日期>.html`）告诉用户，口头小结：**取数窗口（起止 + 天数）+ 取数范围（`window.mode` 为 `all` 时明示「个人模式：全部本机会话」，`include` 时明示「团队模式」）** + 四维画像（姿势分布 + 水平/深度/成果 + landed/total；姿势分布的四档数字以渲染命令 stdout 的「姿势分布: …」行为准（规则层组装），不要用自己估的数）+ **摩擦建议 1-2 个要点** + 「较上次进步」（若有同比）+ **本次编排规模**（如「N 个 extractor + 5 个专家」；subagent 的 token 用量编排端拿不到，不要报数、不要编造）。**小结同守脱敏铁律**。

若 `window.truncated` 为 `true`，追加提醒：名义窗口自 `since_date` 起，但本机 transcript 实际只保留到 `data_start`（Claude Code 默认 `cleanupPeriodDays=30` 天清理）；建议在 `~/.claude/settings.json` 把 `cleanupPeriodDays` 设为 ≥60，下次测评窗口才完整。
