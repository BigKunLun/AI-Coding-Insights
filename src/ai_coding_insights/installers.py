"""统一安装器：把**单一 playbook 真相源**按各家 harness 的规矩落到对的位置。

spec §2 定的 opendesign 模式——不自建 Harness、不 ship agent，用户 PATH 上已有的 agent CLI
就是语义层引擎。于是编排文档只留一份，**harness 差异全部压进这一层适配器**。
本模块是纯逻辑层：除 `do_install` 外一律无 IO，便于直接测试。

## 事实核查（2026-08-15 联网查证，每条附出处）

以下**除明确标注「推测」者外，均为官方文档原文佐证**。

### Claude Code —— 已查证
- 落位：`~/.claude/skills/<skill-name>/SKILL.md`（Personal 级）。
  出处 https://code.claude.com/docs/en/skills
  原文：*"| Personal | `~/.claude/skills/<skill-name>/SKILL.md` | All your projects |"*
- frontmatter：Claude Code 运行时**全部字段可选**，只推荐 `description`。
  原文：*"All fields are optional. Only `description` is recommended so Claude knows when to
  use the skill."* 可用键含 `name` / `description` / `argument-hint` /
  `disable-model-invocation` / `allowed-tools` / `model` 等。
  注：`allowed-tools` 原文 *"Accepts a space- or comma-separated string, or a YAML list."*
  另注：**这些 CC 专属键只在本机有效**——同页说上传 claude.ai / Skills API 时
  *"Unexpected key(s) in SKILL.md frontmatter: argument-hint"* 会硬报错。本安装器写的是
  本机个人级 skill，不走上传路径，故照 CC 运行时口径配齐。
- 调用：`/<目录名>`（命令名取**目录名**，不取 frontmatter 的 `name`）。
  原文：*"you can invoke one directly with `/skill-name`"*、*"the command still comes from
  the directory name"*。
- 子代理：**有，且可并行**。出处 https://code.claude.com/docs/en/sub-agents
  原文：*"For independent investigations, spawn multiple subagents to work simultaneously"*。

### Codex CLI —— 已查证，且**与既有认知不符，必须看这条**
- `~/.codex/prompts/*.md` 自定义 prompt **已被移除**，不是「还能用只是不推荐」。
  出处 https://learn.chatgpt.com/docs/custom-prompts.md 页首横幅原文：
  *"Custom prompts are deprecated. Use skills for reusable instructions that Codex can
  invoke explicitly or implicitly."*；移除节点见 https://github.com/openai/codex/issues/15941
  维护者原文：*"They have been removed completely starting in 0.117.0. You should convert
  your custom prompts to skills."*（并见已合并 PR https://github.com/openai/codex/pull/16115
  *"Remove remaining custom prompt support"*）。
  → **故本适配器不落 `~/.codex/prompts/`，改落 skills。**
- 落位：`$HOME/.agents/skills/<name>/SKILL.md`。
  出处 https://learn.chatgpt.com/docs/build-skills.md，加载顺序为
  `$CWD/.agents/skills` → `$CWD/../.agents/skills` → `$REPO_ROOT/.agents/skills` →
  `$HOME/.agents/skills` → `/etc/codex/skills` → 内置。用户级取第四条。
- frontmatter：`name` 与 `description` **两者必填**。
  原文：*"The `SKILL.md` file must include `name` and `description`."*
  `argument-hint` **不是** skill 的合法字段（它是已移除的 custom prompt 的字段），故不写。
- 调用：CLI 内 `$<skill-name>`。原文：*"In Codex CLI or the IDE extension, run `/skills`
  or type `$` to mention a skill."*
- 子代理：**有，且默认开启、可并行**。
  出处 https://learn.chatgpt.com/docs/agent-configuration/subagents
  原文：*"ChatGPT Work and Codex can run subagent workflows by spawning specialized agents
  in parallel and then collecting their results in one response."*、
  *"Current Codex releases enable subagent workflows by default."*、
  *"Subagent activity appears in the ChatGPT desktop app, Codex CLI, and the IDE extension."*
- **推测（未查证）**：`$CODEX_HOME` 管的是 `~/.codex`（config / sessions），文档把 skills
  目录写成 `$HOME/.agents/skills` 字面量，未提 `CODEX_HOME` 会改写它——故本适配器
  **不**让 `CODEX_HOME` 影响落位。若日后证实可被改写，此处需跟改。

### opencode —— 已查证
- 落位：全局 `~/.config/opencode/commands/*.md`，项目级 `.opencode/commands/*.md`。
  出处 https://opencode.ai/docs/commands
  原文：*"You can also define commands using markdown files. Place them in:
  - Global: `~/.config/opencode/commands/` - Per-project: `.opencode/commands/`"*
  注：加载器实际 glob 是 `{command,commands}/**/*.md`（单复数都收，见
  packages/core/src/config/plugin/command.ts），本安装器取**文档口径的复数**。
- frontmatter：`description` / `agent` / `model` / `subtask` 均可选；`template` 在 JSON
  配置里必填，但 markdown 文件的**正文即 template**，不作为 frontmatter 键。
  原文：*"The frontmatter defines command properties. The content becomes the template."*、
  *"Agent: This is an optional config option. If not specified, defaults to your current
  agent."* → 我们显式钉 `agent: build`，否则用户在 Plan 这类只读主代理里触发会没有写权限。
  **无 `name` 字段**：原文 *"The markdown file name becomes the command name."*
- 调用：`/<文件名>`。原文：*"Use the command by typing `/` followed by the command name."*
- 子代理：**有，且鼓励并行**。出处 https://opencode.ai/docs/agents
  原文：*"There are two types of agents in OpenCode; primary agents and subagents."*、
  内置 General 子代理说明 *"Use this to run multiple units of work in parallel."*
- **推测（未查证到文档）**：全局目录用 XDG 解析（源码 `xdgConfig` + `Flag.OPENCODE_CONFIG_DIR`），
  故本适配器认 `OPENCODE_CONFIG_DIR` / `XDG_CONFIG_HOME`，两者都没有才退回 `~/.config`。
  环境变量名取自源码里的 Flag 名，未见官方文档正面确认。

## 由查证结论倒推的两个设计后果

1. **三家目前都有并行子代理，`has_subagent` 全为 True**，降级分支暂无在线适配器命中。
   分支照样实现并测（`tests/test_installers.py` 用合成适配器打），理由是 spec §2 把
   「静默劣化」定为本项目最危险故障——机制要先于需求就位，不能等真来一家没子代理的
   harness 时才现写一段没人跑过的代码。
2. **`has_subagent` 不等于 `sources.CAP_SUBAGENT`，两者不可互相推导**。前者问的是
   「这家 harness **运行时**能不能派并行子代理」（决定 playbook 怎么编排），后者问的是
   「这家的会话记录里**能不能测到**子代理使用」（决定指标算不算未测量）。Codex 正是
   反例：运行时有子代理，但 rollout 记录里测不出来，故 `sources.py` 不给它 CAP_SUBAGENT，
   而这里给它 `has_subagent=True`。混为一谈会同时产出两种错结论。
"""
import dataclasses
import json
import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse
from urllib.request import url2pathname

from . import sources

# playbook 在各家落位时的统一名字（CC/Codex 是目录名，opencode 是文件名）。
# 它同时就是用户的调用名：CC `/ai-coding-insights`、Codex `$ai-coding-insights`、
# opencode `/ai-coding-insights`。
PLAYBOOK_NAME = "ai-coding-insights"

# 安装期占位符：**安装器负责替换干净**，渲染后一个都不许剩。
# 与之相对的运行期占位符（<PLUGIN_ROOT> / <BATCHES_DIR> / <RUN_STARTED> / <AGENT_N> /
# <BATCH_FILE> / <NN> / <N>）由 LLM 从 scan 清单现取，**安装器碰它们就是 bug**：
# 安装期把运行时的值钉成常量，等于让每次评估都读同一批陈旧路径。
INSTALL_PLACEHOLDERS = ("<ACI>", "<SOURCE>", "<PLUGIN_ROOT_OPT>")

# 各家 harness 里给用户看的一句话描述。避免出现 ": "（会给极简 YAML 序列化添麻烦），
# 也避免任何业务语义（隐私铁律：出本机的自由文本只谈行为模式与量级）。
_DESCRIPTION = (
    "Analyze your own AI-coding sessions on this machine and produce a local "
    "four-dimension profile (posture / breadth / depth / outcome) plus friction-and-advice, "
    "over an incremental window since your last check. Everything stays on this machine."
)


class InstallError(RuntimeError):
    """安装被拒。**必须报错中止，不静默继续**。

    两种触发：目标已存在而未给 `--force`（用户可能手改过 playbook，覆盖即丢改动）、
    目标落进了某家 harness 的会话原文目录（污染取数源）。
    """


# ---------------------------------------------------------------- 落位路径

def _cc_target() -> Path:
    return Path.home() / ".claude" / "skills" / PLAYBOOK_NAME / "SKILL.md"


def _codex_target() -> Path:
    # 注意不是 ~/.codex/skills：Codex 的 skills 走 `.agents/skills` 约定，用户级在 $HOME 下。
    return Path.home() / ".agents" / "skills" / PLAYBOOK_NAME / "SKILL.md"


def _opencode_config_dir() -> Path:
    """opencode 全局配置目录。XDG 覆盖属推测项，见模块 docstring。"""
    for var in ("OPENCODE_CONFIG_DIR", "XDG_CONFIG_HOME"):
        raw = (os.environ.get(var) or "").strip()
        if raw:
            base = Path(raw).expanduser()
            if base.is_absolute():
                # OPENCODE_CONFIG_DIR 直指 opencode 目录本身，XDG_CONFIG_HOME 是它的父目录。
                return base if var == "OPENCODE_CONFIG_DIR" else base / "opencode"
    return Path.home() / ".config" / "opencode"


def _opencode_target() -> Path:
    return _opencode_config_dir() / "commands" / f"{PLAYBOOK_NAME}.md"


# ---------------------------------------------------------------- 降级断点

DEGRADED_MARKER = "## 降级编排断点（本 harness 无并行子代理）"

DEGRADED_ORCHESTRATION = (
    "**本 harness 不支持派发并行子代理，三阶段并行编排在此不可用。改按下面的替代跑法执行：**\n"
    "\n"
    "1. **提取阶段**：不要「派 N 个 extractor 并行」。改为**单轮顺序**处理——按批次编号从小到大，"
    "**逐批**读入、逐批产出对应的 obs 文件，一批写完再读下一批；每批之间不要携带上一批的原文，"
    "只携带已写盘的结构化结果。\n"
    "2. **分析阶段**：不要「5 个专家并行」。改为**单轮顺序**处理——**逐维**分析，"
    "一次只戴一顶专家帽子，把该维度的结论写定后再进入下一维度；姿态分档同样在这一轮里顺序完成。\n"
    "3. **合成阶段**：照常，读齐全部逐批 / 逐维结果后再合成画像。\n"
    "\n"
    "顺序执行会更慢也更省上下文，但**不得因此跳过任何批次或任何维度**——"
    "少跑一批就是少一段证据，而报告不会因此报错。"
)

DEGRADED_REPORT_CAVEAT = (
    "**必须写进报告小结（这是硬性要求，不是可选提示）**：在最终小结里明确告知用户"
    "「本次为降级编排（当前 harness 无并行子代理），姿态分档与专家分析由单轮顺序完成，"
    "深度低于并行版」。**不许省略、不许弱化措辞、不许只在心里知道**——"
    "静默劣化（不报错、只安静产出一份看起来正常的低质报告）是本项目定义的最危险故障，"
    "用户有权知道这份报告是降级产出的。"
)

_DEGRADED_BLOCK = f"{DEGRADED_MARKER}\n\n{DEGRADED_ORCHESTRATION}\n\n{DEGRADED_REPORT_CAVEAT}\n"


# ---------------------------------------------------------------- Adapter

@dataclass(frozen=True)
class Adapter:
    """一家 harness 的安装适配器。**每个适配器是一条接缝，各配契约测试**（spec §3.4）。"""

    source: str                       # 来源名，与 sources.SOURCE_NAMES 一一对应
    label: str                        # 展示名，与 sources 的 label 保持一致
    target: Callable[[], Path]        # playbook 落位绝对路径（已展开 ~）
    command_prefix: str               # 替换正文里的 <ACI>
    frontmatter: dict                 # 该 harness 要求的 frontmatter 键值
    has_subagent: bool                # False → 注入降级断点，走单轮顺序编排
    doc_url: str                      # 查证出处，便于日后复查漂移
    # 替换 <PLUGIN_ROOT_OPT>：只有 CC 插件形态需要 `--plugin-root ...`（配置随插件走），
    # 其余形态必须渲染成空串（见 render_playbook 里的说明）。
    plugin_root_opt: str = ""


# `command_prefix` 三家目前同值（`uvx ai-coding-insights`，即 spec §3.1 的分发入口）。
# 之所以仍按适配器逐家声明而不抽成常量：这是**每家各自的调用约定**，哪天有一家需要不同
# 前缀（比如必须带 `--`透传或换 runner），改的是那一家的一行，而不是去拆一个共享常量。
_ACI_ENTRY = "uvx ai-coding-insights"


# ------------------------------------------------- 命令前缀跟安装来源走（PEP 610）
#
# **为什么不能把前缀写死成 `uvx ai-coding-insights`**：那条命令只有包发到 PyPI 才解析
# 得出来。从 git 装（`uvx --from git+… install`）或从仓库目录装（`uv run … install`）时，
# install 本身一切正常、落位文件也对，但 playbook 里每条命令都会去 PyPI 找一个不存在的
# 包——用户看到的是「装好了，一跑就报找不到包」。分发路径不止 PyPI 一条（还有 git、
# 仓库 clone、CC marketplace 插件），前缀就不能只认一条。
#
# 推断依据是 PEP 610 的 `direct_url.json`：uv / pip 从 URL、VCS 或本地目录装包时会把
# 「这个包是从哪来的」写进发行版元数据。我们把它翻译成「该怎么再调起我」。
# 读不到、读不懂、或来源形态本身不可复现（比如直接装一个 .whl）→ 一律退回 PyPI 口径，
# **不许瞎猜**：猜错等于把一条跑不通的命令钉进用户的 playbook。

_DIST_NAME = "ai-coding-insights"


def entry_from_direct_url(raw: str | None) -> str | None:
    """PEP 610 `direct_url.json` 正文 → 再调起本工具的命令前缀。**纯函数**，推断不出返回 None。"""
    if not raw:
        return None
    try:
        info = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(info, dict):
        return None
    url = info.get("url")
    if not isinstance(url, str) or not url:
        return None

    vcs = info.get("vcs_info")
    if isinstance(vcs, dict):
        # 只认 git：别家 VCS 的 `uvx --from` 写法没查证过，宁可退回也不写一条没跑过的命令。
        if vcs.get("vcs") != "git":
            return None
        # 只在用户当初显式指定了修订时才钉。**不拿 commit_id 顶替**——那会把「跟默认
        # 分支走」的意图偷偷冻结在安装当天那个提交上，用户再也拿不到修复。
        rev = vcs.get("requested_revision")
        suffix = f"@{rev}" if isinstance(rev, str) and rev else ""
        return f"uvx --from git+{url}{suffix} {_DIST_NAME}"

    if isinstance(info.get("dir_info"), dict):
        # 本地目录（editable 与否都算）：指回那个目录，不经任何索引。
        # 这就是「直接通过项目走」——clone 下来跑一次 install，playbook 从此指着这份 clone。
        path = url2pathname(urlparse(url).path)
        if not path:
            return None
        # 必须 quote：路径含空格时不引起来，shell 会把 `--project` 的取值切断，
        # `uv run` 转头去跑当前目录的项目——又一个不报错的错。
        return f"uv run --project {shlex.quote(path)} python -m ai_coding_insights"

    # archive_info（直接装一个 wheel/sdist 文件）等形态：没有可复现的再调起写法。
    return None


def read_direct_url() -> str | None:
    """读本进程所属发行版的 `direct_url.json` 正文。**薄 IO 层**，读不到返回 None。

    单独拆出来只为可测：推断逻辑全在 `entry_from_direct_url` 那个纯函数里。
    """
    try:
        from importlib.metadata import PackageNotFoundError, distribution
        return distribution(_DIST_NAME).read_text("direct_url.json")
    except Exception:      # noqa: BLE001 —— 元数据缺失绝不能拖垮安装，退回默认即可
        return None


def detect_entry() -> str:
    """推断命令前缀；推断不出退回 PyPI 口径。**永远返回一条非空命令**。"""
    return entry_from_direct_url(read_direct_url()) or _ACI_ENTRY


def bash_glob_for(entry: str) -> str:
    """命令前缀 → allowed-tools 里对应的 Bash 白名单条目。**纯函数**。

    白名单与前缀失配的表现不是报错，而是**每条命令都弹一次权限确认**，编排卡在半路。
    故这里是唯一真相源：前缀一变，白名单跟着这个函数走，不许两边各写各的。
    """
    toks = entry.split()
    # `uv run` 要连着取两个词：白名单写 `Bash(uv *)` 会顺带放行 `uv pip` 之类，太宽。
    head = "uv run" if toks[:2] == ["uv", "run"] else (toks[0] if toks else entry)
    return f"Bash({head} *)"


def with_entry(adapter: "Adapter", entry: str | None) -> "Adapter":
    """换掉适配器的命令前缀，**并同步 frontmatter 里的 allowed-tools**。纯函数。

    返回新对象：`ADAPTERS` 是模块级共享 dict，原地改会污染同进程里的其他调用。
    `entry` 为空即原样返回（调用方不必自己判空）。
    """
    if not entry or not entry.strip():
        return adapter
    entry = entry.strip()
    fm = dict(adapter.frontmatter)
    allowed = fm.get("allowed-tools")
    # 没有 allowed-tools 的家（Codex / opencode 的规范里就没这个键）不许凭空加一个：
    # 多写一个字段就是给未来的严格解析器埋雷。
    if isinstance(allowed, str) and allowed:
        old, new = bash_glob_for(adapter.command_prefix), bash_glob_for(entry)
        # 替换而非追加：并存等于白名单越放越宽，把已经用不上的入口继续开着。
        fm["allowed-tools"] = allowed.replace(old, new) if old in allowed else \
            f"{new}, {allowed}"
    return dataclasses.replace(adapter, command_prefix=entry, frontmatter=fm)


ADAPTERS: dict[str, Adapter] = {
    sources.CLAUDE_CODE: Adapter(
        source=sources.CLAUDE_CODE,
        label="Claude Code",
        target=_cc_target,
        command_prefix=_ACI_ENTRY,
        # CC 运行时全字段可选，但这几个键实打实影响行为：
        # disable-model-invocation 挡住模型自作主张触发（本工具由用户手动触发，spec 定位级约束）；
        # allowed-tools 免掉全程权限打断；argument-hint 提示可传天数。
        frontmatter={
            "name": PLAYBOOK_NAME,
            "description": _DESCRIPTION,
            "disable-model-invocation": True,
            "argument-hint": "[days]",
            "allowed-tools": "Bash(uvx *), Bash(date *), Agent, Read, Write",
        },
        has_subagent=True,
        doc_url="https://code.claude.com/docs/en/skills",
    ),
    sources.CODEX: Adapter(
        source=sources.CODEX,
        label="Codex CLI",
        target=_codex_target,
        command_prefix=_ACI_ENTRY,
        # 只出规范要求的两个必填键。多写一个字段就是给未来的严格解析器埋雷，
        # 而 Codex skill 规范里既没有 argument-hint 也没有 allowed-tools。
        frontmatter={
            "name": PLAYBOOK_NAME,
            "description": _DESCRIPTION,
        },
        has_subagent=True,
        doc_url="https://learn.chatgpt.com/docs/build-skills.md",
    ),
    sources.OPENCODE: Adapter(
        source=sources.OPENCODE,
        label="opencode",
        target=_opencode_target,
        command_prefix=_ACI_ENTRY,
        # 无 name（命令名取文件名）。agent 显式钉成 build：默认「跟当前 agent 走」，
        # 用户若在 Plan 这类受限主代理里触发，playbook 需要的 Bash/Write 会被挡掉。
        frontmatter={
            "description": _DESCRIPTION,
            "agent": "build",
        },
        has_subagent=True,
        doc_url="https://opencode.ai/docs/commands",
    ),
}


# ---------------------------------------------- CC 插件（marketplace）变体
#
# 为什么它不在 ADAPTERS 里：**它不是一个来源，是同一个来源（claude-code）的第二种
# 分发形态**。marketplace 装的是整个仓库，skill 由插件自带，规则层就在
# `${CLAUDE_PLUGIN_ROOT}` 底下——不该也不能走 `uvx` 去 PyPI 再拉一份。
# 把它塞进 ADAPTERS 会立刻破坏「适配器与来源一一对应」这条契约。
#
# 承重后果：`skills/ai-coding-insights/SKILL.md` 因此是**生成物**而非手写文件——
# 它由本适配器渲染 `playbook/PLAYBOOK.md` 得到（`scripts/render-plugin-skill.py`），
# 契约测试比对两者是否同步。若它退回手写，模板一改、插件用户就会拿到一份旧 playbook，
# 而且**不报错**。
PLUGIN_COMMAND_PREFIX = "uv run --project ${CLAUDE_PLUGIN_ROOT} python -m ai_coding_insights"

PLUGIN_ADAPTER = Adapter(
    source=sources.CLAUDE_CODE,
    label="Claude Code（插件形态）",
    # 生成物落在仓库里，不装到用户家目录：插件形态由 marketplace 分发整个仓库。
    target=lambda: Path(__file__).resolve().parent.parent.parent
    / "skills" / PLAYBOOK_NAME / "SKILL.md",
    command_prefix=PLUGIN_COMMAND_PREFIX,
    # allowed-tools 必须与 command_prefix 对得上：前缀是 `uv run …`，白名单就得是
    # `Bash(uv run *)`。两者失配的表现是每条命令都弹权限确认，编排卡在半路。
    frontmatter={
        "description": _DESCRIPTION,
        "disable-model-invocation": True,
        "argument-hint": "[days]",
        "allowed-tools": "Bash(uv run *), Bash(date *), Agent, Read, Write",
    },
    has_subagent=True,
    doc_url="https://code.claude.com/docs/en/plugins",
    plugin_root_opt="--plugin-root ${CLAUDE_PLUGIN_ROOT}",
)


# ---------------------------------------------------------------- 纯函数

def _yaml_scalar(value) -> str:
    """极简 YAML 标量序列化（零依赖，只覆盖 frontmatter 用得到的类型）。

    只在**必要时**加引号：`argument-hint: [days]` 不加引号会被读成流式序列，
    含 `: ` 的值不加引号会被读成嵌套映射——都是「不报错、只解析成别的东西」的故障。
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    hostile = (
        text == ""
        or text[0] in "[]{}&*!|>%@`\"'#,?:-"
        or ": " in text
        or " #" in text
        or text.strip() != text
    )
    if hostile:
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text


def render_frontmatter(frontmatter: dict) -> str:
    """把 frontmatter dict 渲染成 `---` 包裹的 YAML 块（末尾带换行）。纯函数。"""
    body = "\n".join(f"{k}: {_yaml_scalar(v)}" for k, v in frontmatter.items())
    return f"---\n{body}\n---\n"


def strip_frontmatter(text: str) -> str:
    """剥掉开头 `---` 包裹的 frontmatter，返回正文。没有 frontmatter 就原样返回。纯函数。"""
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    rest = text[end + len("\n---"):]
    # 吃掉闭合 `---` 那一行剩下的部分（含行尾换行）
    newline = rest.find("\n")
    return rest[newline + 1:] if newline != -1 else ""


def render_playbook(playbook_text: str, adapter: Adapter) -> str:
    """把单一真相源 playbook 渲染成某家 harness 能直接用的文件内容。**纯函数**。

    做三件事，且**只做这三件**：
    1. 换掉整段 frontmatter（原文那份是给 CC 插件用的，键集和别家对不上）；
    2. 替换安装期占位符 `<ACI>` / `<SOURCE>`；
    3. 无并行子代理时，在正文最前面注入降级断点。

    **不碰运行期占位符**——那是 LLM 的取值，不是安装器的。
    """
    body = strip_frontmatter(playbook_text)
    body = body.replace("<ACI>", adapter.command_prefix)
    body = body.replace("<SOURCE>", adapter.source)
    # `--plugin-root` 只对 CC 插件形态有意义（配置随插件分发）。别家渲染成空串——
    # 留着 `--plugin-root ${CLAUDE_PLUGIN_ROOT}` 是**真会炸的**：那个变量在非 CC 环境
    # 下为空，shell 把整个词吃掉，argparse 于是拿后面的 `--emit-batches` 当它的取值，
    # 编排静默走成「默认渲染 HTML」而不是产出批次——不报错，只是什么都不对。
    body = body.replace("<PLUGIN_ROOT_OPT>", adapter.plugin_root_opt).replace("  ", " ")
    if not adapter.has_subagent:
        # 放正文最前面：降级指引必须在编排端读到任何步骤之前生效，
        # 否则它可能已经照并行版起了头才看到断点。
        body = f"\n{_DEGRADED_BLOCK}\n{body.lstrip(chr(10))}"
    return render_frontmatter(adapter.frontmatter) + body


def unsubstituted_placeholders(text: str) -> list[str]:
    """渲染后仍残留的**安装期**占位符（排序）。纯函数。

    只查 `<ACI>` / `<SOURCE>`。运行期占位符残留是正常状态，报出来会让调用方误以为出错。
    """
    return sorted(p for p in INSTALL_PLACEHOLDERS if p in text)


def session_root_conflict(target: Path) -> str | None:
    """目标是否落进了某家 harness 的会话原文目录；是则返回那家的来源名，否则 None。纯函数。

    红线守卫：安装器写进会话目录会同时捅两个洞——污染取数源（自己的文件被当会话解析），
    以及在隐私边界内凭空多出一个规则层不认识的写入点。
    """
    target = Path(target)
    for name in sources.SOURCE_NAMES:
        root = Path(sources.get_source(name).default_root).expanduser()
        if target == root or target.is_relative_to(root):
            return name
    return None


# 各家的触发写法：**取目录名还是文件名，三家不一样**。
# - Claude Code：skill 名取**所在目录名**（不是 frontmatter 的 name，也不是文件名）
# - Codex：skill 名同样取目录名，但前缀是 `$` 不是 `/`
# - opencode：command 名取**文件名**（该家一个命令就是一个 .md 文件，没有目录层）
# 装完却告诉用户一个调不出来的名字，等于装了个用户找不到的东西——这也是「不报错的错」。
_INVOCATION: dict[str, tuple[str, str]] = {
    # 来源名 → (前缀, 取名方式: "dir" | "stem")
    sources.CLAUDE_CODE: ("/", "dir"),
    sources.CODEX: ("$", "dir"),
    sources.OPENCODE: ("/", "stem"),
}


def invocation_hint(adapter: Adapter, target: Path | None = None) -> str:
    """装完后告诉用户「在这家里怎么调起来」。纯函数。"""
    target = Path(target) if target is not None else adapter.target()
    prefix, how = _INVOCATION.get(adapter.source, ("/", "stem"))
    name = target.parent.name if how == "dir" else target.stem
    return f"{prefix}{name}"


def plan_install(playbook_text: str, adapter: Adapter) -> dict:
    """`--print` 预演：算出会写什么、写到哪，**不碰 IO**（`exists` 的探测除外）。纯函数级。"""
    rendered = render_playbook(playbook_text, adapter)
    target = adapter.target()
    return {
        "source": adapter.source,
        "target": str(target),
        "bytes": len(rendered.encode("utf-8")),
        "exists": target.exists(),
        "frontmatter": dict(adapter.frontmatter),   # 拷贝，别让调用方改坏共享的 ADAPTERS
        # 前缀跟安装来源走，`--print` 是用户写盘前唯一的核对窗口，必须看得见落进去的是哪条
        "entry": adapter.command_prefix,
        "degraded": not adapter.has_subagent,
        "invocation": invocation_hint(adapter, target),
    }


# ---------------------------------------------------------------- 唯一 IO

def do_install(playbook_text: str, adapter: Adapter, force: bool = False) -> Path:
    """把渲染结果写到落位路径，返回该路径。**本模块唯一有 IO 的函数**。

    目标已存在且未给 `force` 时抛 `InstallError`：用户可能手改过 playbook，
    静默覆盖等于无声吞掉他的改动。
    """
    target = adapter.target()
    conflict = session_root_conflict(target)
    if conflict is not None:
        raise InstallError(
            f"拒绝安装：{adapter.label} 的落位路径 {target} 落在 {conflict} 的会话原文目录内")
    if target.exists() and not force:
        raise InstallError(
            f"{target} 已存在。若确认可覆盖（你对它做过的手改会丢失）请加 --force")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_playbook(playbook_text, adapter), encoding="utf-8")
    return target
