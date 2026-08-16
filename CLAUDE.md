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
uv run python -m ai_coding_insights scan --plugin-root . --emit-batches ~/.ai-coding-insights/run

# 指定来源（不传则跟触发环境走）
uv run python -m ai_coding_insights scan --source codex --emit-batches ~/.ai-coding-insights/run

# 安装器预演：看 playbook 会落到哪、有没有走降级版（不写盘）
uv run python -m ai_coding_insights install --print
```

零运行时依赖（纯 stdlib），dev 仅 pytest。

规则层共 8 个子命令，正常由 skill 编排调用，单独调试时也可直接跑（点到存在即可，参数以代码为准）。**这份清单有测试守着**（`tests/test_skill_contract.py`）：子命令名与条数必须与 `cli.py` 实际注册面一致，加了子命令不补这里即测试红。

- `scan` —— 扫描 / 窗口决策 / 分批 / 硬指标；`--emit-batches` 是编排主路径，四种输出形态互斥（`--emit-batches` / `--profile-input` / `--json` / 默认渲染 HTML）。
- `init` —— 交互配置向导，从本机会话来源勾选团队归属。
- `verify-obs` —— 校验 LLM 观测（obs）对批次的覆盖与 posture 计数完整性。
- `render-profile` —— 渲染最终画像 HTML 报告。
- `auto-scan` —— `SessionEnd` hook 后台自动评估（接线在 `hooks/hooks.json`；自带 lock 防重入 + 滚动日志，失败对用户静默）。
- `reset` —— 清空本机可再生产物（`snapshots/` / `reports/` / `run/` / `auto-scan.log`）解除 30 天增量窗口闸门，**并把今日写进 `.auto-scan.lock`**（而非删它），压住 `SessionEnd` 的 auto-scan 当天抢先写新快照重新武装闸门——这是「reset 后重跑仍 too_soon」的根因修复。按白名单删、`--dry-run` 只预览，永不碰 `config.toml` 与会话原文。slash 入口 `commands/reset.md`。
- `install` —— 统一安装器：把**单一真相源 playbook**（`skills/ai-coding-insights/SKILL.md`）渲染并落到当前 harness 该放的位置。「装哪一家」与取数一样**跟触发环境走**（`--source` 可覆盖）；`--print` 只预演落点不写盘，目标已存在须 `--force` 才覆盖（用户可能改过自己的 playbook）。三个薄适配器在 `installers.py`，各配一条契约测试。**无子代理能力的 harness 会装降级编排版**，且降级状态写进报告 caveat——静默劣化是本项目定义的最危险故障。
- `calibrate` —— 手动调试命令：给出各指标分布与当前阈值的分位定位，**不进 SKILL.md 编排、不产 HTML**。两种取数来源：默认读 `snapshots/` 里已脱敏的历史标量；`--replay` 则把本机会话按等长窗口切片重放成伪快照（`--replay-window` 默认对齐 `WINDOW_FLOOR_DAYS`，`--replay-step` 可滑动）。**回放存在的理由**：窗口闸门是「不足 30 天即 too_soon」，快照最快 30 天落一个，攒 20 个要 1.6 年——靠等快照校准阈值走不通，而档位闸门用的全是规则层硬指标，可直接从既有会话按同口径重算。**切片长度必须与评估窗口同口径**，改小即跨口径（阈值是按「一个 30 天窗口内的量级」定的）。回放不跑 git log、不跑 LLM，git 三键与姿态四档整键不放进伪快照（未测量 ≠ 0）。只给本机单人分布，不是人群分位；样本不足时逐层挂 caveat，且 n < 5 时压住「过门/未过门」的定性读法，只给数字。

## 架构原则

**不自建 Harness**：用户 PATH 上已有的 agent CLI（Claude Code / Codex / opencode）就是语义层引擎，本项目不 ship agent、不起子进程调别人的 CLI。**数据源跟触发环境走**——在哪个 harness 里触发就解析哪家会话（`sources.detect_source`，只看环境变量，不去 PATH 上探测「用户装了什么」：装了不等于在用）。

**「未测量 ≠ 0」是跨来源的承重约束**。`sources.py` 为每家声明能力集，`unmeasured_fields()` 把缺失能力翻译成具体的 aggregate 字段名，全链路据此降级：渲染层打「未测量」而非 0（`view_model.UNMEASURED_TEXT`）、`decide_stage` 跳过对应判据并把档位标为不可比、能力盲区不报该项、LLM 专家被禁止据其下结论（SKILL.md 共同纪律 7 的例外条款）。反向不成立：**观测到非零就说明测得到**，`signals._drop_measured` 会把这类字段从 unmeasured 里摘掉（能力集是保守声明，宁可事后放宽）。少了这条，Codex 用户会读到「你没用过 Workflow」——而 Codex 压根没有 Workflow 这个概念。

「未测量」**怎么摆**是另一件事，与「标不标」分开：指标网格只放测得到的格，未测量项由 `view_model.fold_unmeasured` 摘进网格下方的折叠区块（`report` 同时给指标卡挂 `fam-compact`，让族按内容宽度横向流动，不留 4 列网格的空洞）。理由是产品的而非技术的——Codex 报告 16 格里 7 格写着「未测量」时，第一观感是「这工具对我没用」。**折叠只改呈现位置，信息一条不少**：折叠项保留原值（仍是「未测量」，绝不改写成 0 或「—」），置顶的来源口径 caveat 卡片照旧列全部未测量字段（那是全集，折叠区只是网格里被藏起来的子集）。兜底：真出现「一格都测不到」就原样退回不折叠——空白的指标区无从解释，满屏「未测量」至少自证了原因。守卫在 `tests/test_unmeasured_fold.py`（含「两处并集 == 完整网格」这条防丢格断言）。

双层分工，职责不混：

- **规则层**（本仓库 Python）：确定性工作——会话发现与归属判定、解析、硬指标计算、渲染。凡是能用规则算的，不交给 LLM。
- **LLM 层**（`skills/` 下的 SKILL.md，编排用户自己的 cc）：只做语义判定，产出结构化数据；像素（HTML）一律由脚本渲染。

两层之间靠**文件契约**衔接，**改任何接缝必须两侧同步**：动了 CLI 输出或 schema，就要同步改 SKILL.md，反之亦然。接缝失配的危害不是报错而是**不报错**——SKILL.md 自己就写着「专家读到错配的 obs 不报错，只会安静产出错误结论」，用户会拿到一份看起来正常的错报告。故这份清单**不只是纪律，还有可执行闸门**：`tests/test_skill_contract.py` 用纯文本/内省比对把下列接缝钉死（CLI 参数双向差集、SKILL.md 内嵌 profile 示例过 schema、reset 白名单三处真相源、锁协议格式、stdout 四行与值域）。**加接缝时顺手加断言**，别让清单退回纯人工纪律。

当前接缝有名有姓（文件名即契约，改名或改字段须同步 SKILL.md）：

- **manifest**：`scan --emit-batches` 的 stdout JSON。含 `schema_version`（规则层 ↔ LLM 层的契约版本，真相源 `cli.MANIFEST_SCHEMA_VERSION`，playbook 抄了一份、契约测试比对两侧）——**改中间产物结构就要 +1**。它存在的理由：playbook 装在用户机器上而规则层由 uvx 每次拉最新，两边必然各自漂移；没有它，旧 playbook 配新规则层的表现是「读到的键不存在 → 当空处理 → 安静产出错报告」。另含 `source` 与 `capabilities`（来源与其正面能力声明）。
- **中间 JSON**（落 `--emit-batches` 目录）：`batch-NN.json`（LLM 分批输入）、`obs-*.json`（extractor 产出）、`_window.json`、`_aggregate.json`（已剥掉含项目名的 `project_breakdown`，含 `parse_health` / `customization_signals` 等字段）、`profile.json`（合成画像）。
- **CLI 参数**：`render-profile` 的 `--metrics` / `--window` / `--obs-glob` / `--run-*`。编排链路（`scan` / `verify-obs` / `render-profile`）新增参数时，要么写进 SKILL.md，要么在契约测试的豁免白名单里写明理由——没有「默认放过」。
- **render-profile 的 stdout 四行**：报告路径 + `姿势分布: …` / `姿态健康态: …` / `成熟度档位: …`。SKILL.md 第 5 步要求**逐字照搬**这几行，行前缀改名等于让编排端取空值；两个值域（姿态 5 值 / 档位 4 档）的真相源在 `stage.py`，SKILL.md 抄了一份供编排端判读，改档名须两侧同步。值由 `view_model.build_view` 统一算定（纯函数，cli 与 HTML 各调一次必得同值）。
- **profile schema**：`profile_schema.py`。
- **来源注册表**：`sources.py` 的 `_SOURCES`。**三处必须同步**：新增一家来源要同时补 ①`<name>_source.py`（`iter_sessions` / `earliest_ts` 两个函数，惰性导入，名字对不上即运行时才炸）②`installers.ADAPTERS` 里的适配器（契约测试钉死 `set(ADAPTERS) == set(SOURCE_NAMES)`）③ 能力集声明。能力键 → aggregate 字段的映射表 `CAPABILITY_METRICS` 里的字段名必须真在 `AggregateMetrics` 上（契约测试守着）——写错字段名的后果是该字段永远标不上「未测量」，静默退回 0。
- **工具名规范化（口径映射层）**：`signals.py` 用**字面量**判高阶能力（`"Agent" in tools` 数子代理、`startswith("mcp__")` 数 MCP），故各家 parser 负责在**出规则层前**把等价概念改名成这套规范名——opencode 的 `task` → `Agent`、`<server>_<tool>` → `mcp__<server>__<tool>`。**是改名不是加名**（加名会让 `tool_breadth` 虚高）。不改名的后果：声明了 `CAP_SUBAGENT` 却恒输出 0，报告说「你没用过子代理」——又一个不报错的错报告。加来源时对照这份规范名清单逐个过。
- **playbook 占位符**：`<ACI>`（命令前缀）与 `<SOURCE>`（来源名）由安装器落位时替换；`<PLUGIN_ROOT>` / `<BATCHES_DIR>` / `<RUN_STARTED>` / `<AGENT_N>` / `<BATCH_FILE>` / `<NN>` 是**运行时**占位符，由 LLM 从 scan 清单取值，安装器碰它们就是 bug。playbook 正文两份拷贝（仓库 `skills/ai-coding-insights/SKILL.md` 与 wheel 里的 `ai_coding_insights/playbook/PLAYBOOK.md`，由 pyproject 的 `force-include` 搬运）必须逐字相同，`playbook.py` 两段式解析、契约测试比对。
- **命令前缀 `<ACI>` 跟安装来源走**：分发路径不止 PyPI 一条（还有 git、仓库 clone、CC marketplace 插件），前缀写死成 `uvx ai-coding-insights` 就只有 PyPI 那条能跑。`installers.detect_entry` 读本发行版的 PEP 610 `direct_url.json`，把「我是从哪装的」翻译成「该怎么再调起我」：git → `uvx --from git+<url>[@<用户当初指定的修订>] ai-coding-insights`（**不拿 commit_id 顶替 requested_revision**，那会把「跟默认分支走」冻结在安装当天）、本地目录 → `uv run --project <path> python -m ai_coding_insights`、推断不出 → 退回 PyPI 口径（**不许瞎猜**）。`--entry` 显式覆盖，`--print` 的 `entry` 字段透出落进去的前缀。失配的表现照例不报错：install 成功、落位文件也对，一跑才报「找不到包」。**改前缀必须同步 allowed-tools**——唯一真相源是纯函数 `bash_glob_for`，白名单与前缀失配的表现是每条命令都弹权限确认、编排卡在半路。不变量「出厂适配器的白名单与其前缀自洽」有测试守着（`tests/test_install_entry.py`）。
- **reset 删除白名单**（`cli.py` 的 `_RESET_PRODUCTS`）：横跨 3 处真相源的「汇聚契约」——`snapshots`（引用 `DEFAULT_SNAPSHOT_DIR` 随动）、`reports`（`hooks/auto-scan-hook.sh` 的 `REPORT_DIR`）、`run`（SKILL.md 的 `--emit-batches` 落点）、`auto-scan.log`。**改任一落点须同步此白名单**，否则 reset 静默漏删 → 30 天闸门不解除。
- **reset ↔ auto-scan 锁协议**：`.auto-scan.lock` 内容为 UTC `%Y-%m-%d`；`auto-scan` 见锁等于今日即整天跳过（`_cmd_auto_scan` 顶部），`reset` 借此把今日写进锁来压住抢占。**改锁格式/路径须三处同步**（auto-scan 写锁、auto-scan 读锁、reset 置锁）。

## 不变约束（定位级，违反即 bug）

- **隐私**：会话原文与业务语义永不出本机。所有进入画像/证据/建议的自由文本只描述**行为模式与量级**，绝不含客户/功能/产品/架构等业务内容。承重机制（改动即可能捅破隐私网）：归属边界单点在 `Config.discovery_rules`；`_aggregate.json` 与跨次快照主动剥离含项目名的 `project_breakdown`；批次出规则层前过 `redact` 密钥网；文件路径仅在 `git_outcome` 本机内做交集匹配、只出计数，路径与 diff 永不出本机。
- **人在环**：机器不下最终判决、不自动定奖惩；产出是软信号初筛 + 可验证证据入口。
- **硬成果可验证**：与奖励挂钩的指标必须基于可独立验证的硬证据，不依赖 LLM 判断。落地率为 git 主锚口径——按 git author 历史 + 本机 `user.email`，以「提交改动文件 ∩ 会话编辑文件」归属落地（与时间无关），落地率 = 落地 ÷ 窗口同仓本人提交总数；路径仅本机内匹配、只出计数，独立于 LLM，规则以 `git_outcome.py` 为准。
- **归属宁漏勿误**：公司项目判定不确定的一律不纳入，私人会话从机制上进不来。

数值阈值与具体策略（窗口、分批、判定规则等）以代码为准，不在本文档复述。

## 工程规范

- 先写测试再实现；决策逻辑写成无 IO 纯函数，便于直接测试。
- commit message 与文档命名用中文。
