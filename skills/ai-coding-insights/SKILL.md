---
description: Analyze your own Claude Code sessions (all of them by default, or scoped to your team's repos via config) with a three-stage agent-team (extract → four dimension experts + a coach → synthesize) and produce a local four-dimension AI-coding dashboard (posture/breadth/depth/outcome) plus friction-and-advice, over an incremental window since your last check.
disable-model-invocation: true
argument-hint: "[days]"
allowed-tools: Bash(uv run *), Bash(date *), Agent, Read, Write
---

你要用**三阶段 agent-team** 为当前用户生成一份**四维 AI 协作画像 + 摩擦建议**（仅本机），取数为**自上次检查以来的增量窗口**。

下面「铁律与速查」是**单一真相源**：脱敏字段集 / 业务词黑名单 / 契约占位符 / 金标准判据 / 专家共同纪律——后续各步**引用**它们，派 subagent 时按指示**整段嵌入**对应块（隔离的 subagent 看不到本文件，必须把引用块复制进它的 prompt）。

## 铁律与速查（贯穿全程）

### 脱敏铁律
写入画像 / 证据 / 建议 / 小结的**所有自由文本**只描述**行为模式与量级**，**绝不**引用业务内容。原文与业务语义**永不出本机**。
- **【脱敏字段集】**（下文多处引用此名）：`headline`、`points`、`metrics.label`、`frictions.observation/suggestion`、`evidence.behavior`、`highlights.behavior`。
- **【业务词黑名单】**：客户名 / 功能名 / 产品方向 / 架构细节 / 含业务词的文件名。
- ✓「推翻 AI 一处实现方案并给出更优约束」「累计 N 次提交、落地率 X%」　✗「重构虚拟手环数据服务」「支付通道限流实现」

### 契约速查表（改接缝只动这里）
运行时占位符一律取**第 1 步 scan 清单**（stdout JSON）的字段，**不要**用 `${CLAUDE_PLUGIN_ROOT}`（运行时为空）：
- `<PLUGIN_ROOT>` = 清单 `plugin_root`
- `<BATCHES_DIR>` = 清单 `batches_dir`（绝对路径）。Write obs/profile、glob obs 全用它；**绝不**写 `~` / `${HOME}` 开头——Write 工具不展开 `~`，会在工作目录造出字面 `~` 目录。
- `<RUN_STARTED>` = 第 1 步记的起始时刻；`<AGENT_N>` = 实际派出的 subagent 总数（extractor 含重派 + 5 专家）。

### 金标准判据（L1-L4 分档 + 防伪主导；extractor 与证据专家派发时整段嵌入）
- **L1 跟随**：纯放行/确认/客套，没有在 AI 已给信息之外增加任何信息（「好的」「继续」「go ahead」，无论长短）。
- **L2 选择**：从 AI 给的选项里挑一个，不加新约束（「用方案 A」「第二个吧」）。
- **L3 引导**：主动给目标/约束/格式/范围，贴报错或日志追问，递进式追问。只给目标不给约束的普通指令（「帮我修 X」）算 L3 下沿。
- **L4 主导**：技术具体性纠错、推翻方案并给更优约束、给 AI 没想到的边界、要求自验或给验收判据、先要方案评审再放行、编排多 subagent 或自建扩展。
- 判档**看语义不看长度**（「改成异步」是 L4；「好的就这么办」是 L1）。**防伪主导**：未验证就放行、放任膨胀、容忍未请求的连带改动——措辞再强也不算 L4。**拿不准就低不就高**（L4 拿不准记 L3，L3 拿不准记 L1）。
- **健康带原则**：L4 主导健康区间约 5-20%，过高（>20%）多为无脑推翻或 AI 不给力，过低则引导力不足；L3 引导是掌握者的主力档。姿态健康与成熟度档位**解耦**——档位看绝对用量硬指标，姿态看分布是否落在健康带。

### 专家共同纪律（派每个专家时整段嵌入其 prompt）
1. 产出只通过返回值回传，**不写任何文件**。
2. 每个数字直接取自 `aggregate` 字段或 obs 原文，**禁自创推算**；不从均值推分布（「平均 0.5 次/会话」推不出「过半会话遇到过」，错误可能集中在少数会话）。
3. **口径铁律**：任何比率/占比/派生量，分子分母必须**同源同口径**，禁跨口径相除（`edit_count`÷`git_landed_count`、观测分子÷git 分母都禁）；同名指标多口径必须对齐范围——`landed_ratio` 顶层是全窗口 git 文件重叠落地率（落地 ÷ 窗口同仓本人提交总数）、`trend.second_half.landed_ratio` 是下半场观测落地率，不可把前者冠以「下半场」；`commit_count`=0 而 `git_landed_count`>0 时，涉及落地的叙述禁写「无落地/落地为零」（**测不到≠没做到**）。
4. 只陈述本窗口数据，**不外推**普适规律（「经典 S 曲线」之类不写）；**不编动机/定性**（「轻量级使用」「资深驾驭者」「系统性摩擦」「源于 AI 理解偏差」都是数据没有的因果/性质判断，要写就降级为硬数陈述或移入建议）。
5. 引用 `model_counts` 等枚举字段必须**全量**陈述或明说省略口径（漏报第二名即失真）。
6. **指针规则**：`evidence`/`highlights` 的 pointer 只能从 obs 的 `notable_turns` **原样拷贝**；会话级观察只写 `file_path`（不带 `#uuid`），绝不拿会话 id 冒充 turn uuid——渲染端逐条核验，未命中公开标注 ⚠。
7. `aggregate` 里的 **0 是实测真值不是缺数**（如 `max_parallel_agents`=1/轮次=0 即从未单轮并发），不得当作缺失数据回避陈述。
8. 守脱敏铁律（见【脱敏字段集】/【业务词黑名单】），产出结构化字段，不写散文长句。

## 1. 扫描 + 窗口决策 + 分批

记下运行起始时刻（即 `<RUN_STARTED>`）：

!`date -u +%Y-%m-%dT%H:%M:%SZ`

规则层扫描——窗口决策、分批、硬指标都已算好，并清掉上一轮中间产物残留（批次划分一变，专家会静默读到张冠李戴的数据）。`aggregate` 是硬指标，`window` 是取数窗口；范围默认全部本机会话，团队 include 模式则只含团队项目：

!`uv run --project ${CLAUDE_PLUGIN_ROOT} python -m ai_coding_insights scan --plugin-root ${CLAUDE_PLUGIN_ROOT} --emit-batches "${HOME}/.ai-coding-insights/run"`

**按此顺序判清单 `status`（命中即停）：**
1. `too_soon`：把 `message` 原样告诉用户后**停止**——不派 subagent、不渲染。
2. 否则 `batch_count == 0`：告知「该窗口内没有可纳入的会话」并停止。
3. `first` / `ok`：继续，记住 `window`（第 4 步透传给渲染）；若 `window.truncated` 为 `true`，第 5 步小结必须提醒。

## 2. 阶段一 · 提取（每批派一个 extractor）

对清单 `batches` 里**每个** batch 文件用 **Agent 工具**派一个 extractor（可并行）。下面 prompt 派发时：替换 `<BATCH_FILE>`/`<NN>`（文件名两位序号）/`<BATCHES_DIR>`，并把上文**【金标准判据】整段嵌入**「逐条分档」处：

```
你是 extractor。脱敏：behavior 只描述行为模式，绝不含业务内容（客户/功能/产品/架构）。
用 Read 读 <BATCH_FILE>（JSON 数组，每元素一会话：session_id/cwd/file_path/signals/turns；turns 是完整真人输入，每条有 uuid/chars/text/anchors）。
通读全部 turns，用 Write 写 <BATCHES_DIR>/obs-<NN>.json，结构严格如下：
{"sessions":[
  {"session_id":"...","file_path":"...","signals":<该会话 signals 原样带上>,
   "posture_counts":{"L1":<n>,"L2":<n>,"L3":<n>,"L4":<n>},
   "notable_turns":[
     {"pointer":"<该会话 file_path>#<该 turn 的 uuid>","anchors":<该 turn 的 anchors>,
      "kind":"posture 或 friction",
      "behavior":"<这条真人输入体现的行为模式，一句话（30 字内），行为级脱敏>"}
   ]}
]}
事一·逐条分档（posture_counts）：对该会话【每一条】turn 判一档计数，四档总和必须等于该会话 turns 条数（规则层逐会话校验，不符整批重派）。判据 ＝ 【金标准判据】（此处嵌入 L1-L4 + 防伪主导 + 拿不准就低）。
事二·notable_turns 选材（每条带 kind）：
- kind="posture"：有 L3/L4 判定价值（命中 anchors、明显主导/纠错）。
- kind="friction"：同一问题反复修不收敛、报错贴了又贴、推倒重来、长拉锯无收口、人机互相误解空转。
看不出行为价值的 turn 直接不选。
pointer 的 uuid 必须原样取自该 turn 的 uuid，绝不拿 session_id 充当——渲染端逐条核验，伪指针公开标注。
batch 里【每个会话】都必须在 sessions 里：无可记录 turn 的也列入并令 "notable_turns":[]，posture_counts 每个会话必填（无输入填全零）。
只回复 Write 成功的确认，不复述内容。
```

派完用 **Bash 工具**跑覆盖校验（glob 必须带引号）：

```
uv run --project <PLUGIN_ROOT> python -m ai_coding_insights verify-obs --batches <BATCHES_DIR> --obs-glob '<BATCHES_DIR>/obs-*.json'
```

- `ok`：进入下一步。
- `missing` / `posture_invalid` 非空：对所列 batch（`posture_invalid` 条目带 `file` 指向所属 batch）**重派** extractor（同 prompt 覆盖重写）并重跑，最多 2 轮；仍有问题告知用户并停止。
- `orphans` / `unreadable` 非空：有写坏的 obs——从本步开头重派**全部** extractor（同 prompt 覆盖）并重跑校验，仅一次机会，再失败告知用户并停止。

校验通过前**不得**进入阶段二——专家读到错配的 obs 不报错，只会安静产出错误结论。

## 3. 阶段二 · 五专家并行（四维度 + 一教练）

用 **Agent 工具**并行派 **5 个专家**。每个 prompt ＝ **【专家共同纪律】整段** ＋ 下面该专家的【输入 / 产出 / 禁忌】；**证据专家因涉及 L3/L4 判定，还须把【金标准判据】整段嵌入其 prompt**（与共同纪律同级，不可只留一句引用）。每个专家先用 **Read** 读**全部** `<BATCHES_DIR>/obs-*.json` ＋ `<BATCHES_DIR>/_aggregate.json`（需要窗口时再读 `_window.json`）——这两个文件第 1 步已落盘，**不要**重写。

- **证据专家** · 输入：kind="posture" 的 `notable_turns`。产出：`{"evidence":[{"pointer","behavior"}…L3/L4 各 1-2 条], "highlights":[{"pointer","behavior"}…2-3 条，技术具体性最强的最佳 L4 实践]}`。每条 `highlights.behavior` ＝「**认知动作 + 机制依据 + AI 的回应/结果**」，具体到可回忆（如「凭框架机制推翻方案、指出配置应动态注入、AI 采纳重做」）；守【脱敏字段集】铁律——只描述认知动作/技术机制/量级/结果，工程通用概念（如「配置动态注入」）可用，**绝不**含客户/功能/产品语义。禁忌：**不**估姿势占比、**不**返回任何姿势数字（规则层已从 posture_counts 组装）；判据 ＝ **【金标准判据】**（派发时整段嵌入此 prompt，同 extractor）；无可选素材返空数组，不编造。
- **水平专家** · 输入：`aggregate` 的 `tool_breadth`/`tool_session_counts`/`subagent_sessions`/`workflow_sessions`/`mcp_sessions`/`model_counts`，及高阶编排信号 `background_task_count`/`background_sessions`（后台委托）与 `max_parallel_agents`/`parallel_agent_turns`（真并行：单轮并发派出的 Agent 峰值与轮次）。产出：`{"breadth":{"headline":"一句话定调","points":["2-4 条，行为+量级"],"metrics":[{"label":"工具广度","value":<n>},{"label":"SubAgent会话","value":<n>}],"tools":["工具能力短语…"]}}`。`points` 每条 ＝ **一句洞察**（这说明你怎样用 AI：解读 / 对比 / 点张力），按「洞察导语 —— 次级展开 / 枚举」用全角破折号 `——` 分两段；**禁止复述指标卡已有数字**（如「工具广度 43 种」单列数字无解读不算洞察），导语承载结论、展开放佐证枚举。广度叙事**不以「越多越好」为导向**：断层式领先的少数工具/技能＝真实工作流主轴，应点出；长尾低频项可提示「按需保留、可考虑卸载」；但一个都不用可能仍是初级。结合 `tool_session_counts`/`skill_total_counts` 的**头部断层 vs 长尾**分布说话，而非单看 `tool_breadth` 绝对值。禁忌：据信号点出后台委托与真并行水平（共同纪律 7：0 是真值）。
- **深度专家** · 输入：`notable_turns` 行为 ＋ `aggregate.anchor_counts` ＋ `thinking_block_count`/`thinking_sessions`（深度推理强度硬锚）。产出：`{"depth":{"headline":"..","points":["..带 SO-WHAT.."],"metrics":[{"label":"override","value":<n>},{"label":"轮次/会话","value":".."}]}, "evidence":[{"pointer","behavior"}…]}`。禁忌：metrics 直接取硬指标（override＝`anchor_counts.override`，轮次/会话＝`avg_turns`，不从 notable_turns 自数）；每条 point 挖到「意味什么/该怎么调」，不止「做了什么」，同样按「洞察导语 —— 展开」用全角破折号 `——` 分两段。
- **成果专家** · 输入：`aggregate` 的 `git_landed_count`（主锚：git 历史硬证据，改动文件命中 AI 编辑的本人提交，文件重叠归属）/`git_commit_total`（窗口内同仓本人提交总数，落地率分母）/`commit_count`（会话内可观测提交）/`dropped_count`（观测到但已不在分支历史）/`edit_count`/`landed_ratio`（落地率 = `git_landed_count` ÷ `git_commit_total`，同口径文件重叠）。产出：`{"outcome":{"headline":"..","points":[".."],"metrics":[{"label":"落地提交","value":<git_landed_count>},{"label":"观测丢弃","value":<dropped_count>}],"landed":<git_landed_count>,"total":<git_commit_total>}}`。`points` 每条 ＝ **一句洞察**（量级背后说明什么、落地节奏的张力在哪），按「洞察导语 —— 次级展开 / 枚举」用全角破折号 `——` 分两段，**禁止复述指标卡已有数字**单列无解读。禁忌：只讲量级与落地节奏；`commit_count`=0 而 `git_landed_count`>0 是**测不到不是没提交**（共同纪律 3），绝不写「无提交收口/落地为零」。
- **教练 / 诊断专家** · 输入：全部 `notable_turns`（重点 kind="friction"）＋ `aggregate`（含 `friction_stats`：error/override 命中会话数与单会话 top3、轮次最长 top3，为「集中于少数会话」提供确定性分布）。产出：`{"frictions":[{"observation":"..","pointers":["/abs/path.jsonl#uuid"],"suggestion":".."}…1-5 条]}`。禁忌：每条观察写出依据数字（取自 `aggregate`/`friction_stats`/会话 `signals`）；`pointers` 1-3 个从 friction 的 notable_turns 原样拷贝（会话级用 file_path）；建议具体到「什么场景+做什么动作+怎么验证」，禁「建议小步提交」这类对谁都成立的话；**「怎么验证」只引报告已呈现的硬指标**（`error_top_counts`/`override_top_counts`/趋势表密度），不引报告未渲染的内部计数门槛（如「compaction 从 5 条降到 2 条」——报告无此数，用户无法自验）；**至少一条直接服务姿态健康**：偏依赖→如何把引导力（主动给目标/约束/验收判据）提上来；偏对抗（L4>20%）→如何减少无脑推翻、把约束前置到提问环节；健康→如何在保持的同时补足绝对用量向上一成熟度档冲。**不再以「L4 越高越好」为导向。**；工具覆盖盲区不归你管（规则层已渲染）。下面只是常见方向**示例**（宁缺勿滥，优先本窗口最突出、最个性化的模式）：反复返工（`edit_count` 相对 `git_landed_count` 偏高）、error 集中（`friction_stats.error_top_counts` 对照 `error_session_count`/`session_count`）、override 集中于少数会话（`friction_stats.override_top_counts`）、单会话轮次远超 `avg_turns` 且无 commit 收口（仅当 `commit_count`>0 可用）。

## 4. 阶段三 · 合成 + 渲染

汇总五专家产出，用 **Write 工具**写 `<BATCHES_DIR>/profile.json`（L1-L4 四档分布由渲染命令从 obs 聚合，画像里**不含任何姿势字段**）：

```json
{"breadth":{"headline":"…","points":["…"],"metrics":[{"label":"…","value":28}],"tools":["…"]},
 "depth":{"headline":"…","points":["…"],"metrics":[{"label":"…","value":149}]},
 "outcome":{"headline":"…","points":["…"],"metrics":[{"label":"…","value":49}],"landed":39,"total":49},
 "frictions":[{"observation":"…","pointers":["/abs/path.jsonl#uuid"],"suggestion":"…"}],
 "evidence":[{"pointer":"/abs/path.jsonl#uuid","behavior":"行为级描述"}],
 "highlights":[{"pointer":"/abs/path.jsonl#uuid","behavior":"行为级最佳实践描述"}]}
```

`evidence` 汇集证据/深度专家精选证据，**必须非空**（空列表渲染校验直接失败）；`highlights` 取证据专家最佳 L4 实践 2-3 条；各维 `headline`/`points` 取对应专家产出，全部行为级；`outcome.landed`/`total` 固定取 `git_landed_count` 与 `git_commit_total`（git 文件重叠同口径，与 `landed_ratio` 分母一致，不填 `commit_count`）。

**发布前自检（逐条过）：**
1. **脱敏**：重读你写的【脱敏字段集】每个字段，逐条确认无【业务词黑名单】词，有则改写成纯行为级。
2. **口径一致**：同一指标在不同区块/口径必须一致（趋势表按每会话密度归一，正文就不能用未归一的绝对计数推「放大」结论）。
3. **能力自洽**：水平维度若判某能力「未激活/为零」（如真并行），别处叙述不得说成已具备。
4. **姿态呼应**：若点出姿态偏离健康带（偏依赖 / 偏对抗），摩擦建议须有一条直接服务姿态健康；不以「L4 越高越好」为导向。
5. **双峰点破**：均值低但存在超长尾时（如单会话均值极低却有超长会话），点破是双峰分布，别让「整体简洁」与「长会话撑爆」并列而互不解释。

然后用 **Bash 工具**跑下面这条**单条**命令（`<N>` ＝ 清单 `aggregate.session_count`，并为清单 `included_projects` 里**每个**项目追加一个 `--project <路径>`）：

```
uv run --project <PLUGIN_ROOT> python -m ai_coding_insights render-profile --plugin-root <PLUGIN_ROOT> --profile <BATCHES_DIR>/profile.json --metrics <BATCHES_DIR>/_aggregate.json --window <BATCHES_DIR>/_window.json --obs-glob '<BATCHES_DIR>/obs-*.json' --session-count <N> --project <项目1> --project <项目2> --run-started <RUN_STARTED> --run-agents <AGENT_N>
```

运行元信息两参数进页脚「本报告由 … 生成 · 运行约 … 分钟 · 编排 … 个 agent」，各自可整体省略（**不确定就省略，不编造**）：`<RUN_STARTED>` 填第 1 步起始时刻；`<AGENT_N>` 填实际派出的 subagent 总数。页脚模型名由规则层自动识别，**不传任何模型参数**；不传 `--out`，报告自动落当前目录 `aci-report-<日期>.html`，成功时 stdout 末行即路径。

- stderr「证据指针未命中」：报告已生成并标注 ⚠，**无需重跑**；小结里如实告知哪几条指针未能回看。
- stderr「画像校验失败：…」：按提示用 Write 重写 `<BATCHES_DIR>/profile.json` 后重跑，最多 3 次。

## 5. 小结

把渲染命令 stdout 输出的报告路径告知用户，口头小结（**同守脱敏铁律**）：
- **取数窗口**（起止 + 天数）+ **取数范围**（`window.mode`＝`all` 明示「个人模式：全部本机会话」，`include` 明示「团队模式」）。
- **四维画像**：先报**姿态健康态**（健康 / 偏依赖 / 偏对抗 / 放手为主 / 样本不足，取自渲染输出，**不要把 L4 占比高当成褒奖**）与**成熟度档位**（探索期/进阶期/精通期/引领期，看绝对用量硬指标）；四档原始数字仍以渲染命令 stdout 的「姿势分布: …」行为准（规则层组装，不自估）+ 水平/深度/成果 + landed/total。
- **摩擦建议 1-2 个要点** + 「较上次进步」（若有同比）+ **本次编排规模**（如「N 个 extractor + 5 个专家」；subagent 的 token 用量拿不到，不报数、不编造）。

追加提醒（命中才加）：
- `aggregate.parse_health.drift_flags` 非空：「检测到 N 个信号疑似随 CC 版本漂移（列信号名），相关维度本窗口数据可能失真，建议核对后修复提取规则」——数据可信度硬前置，不得省略。
- `window.truncated` 为 `true`：「名义窗口自 `since_date` 起，但本机 transcript 实际只保留到 `data_start`（CC 默认 `cleanupPeriodDays=30` 清理）；建议在 `~/.claude/settings.json` 把 `cleanupPeriodDays` 设为 ≥60，下次窗口才完整」。
