"""会话来源抽象层（多 harness 地基）。

定位：规则层过去只认 Claude Code 的 `~/.claude/projects/**.jsonl`。多端改造后，
「在哪个 harness 里触发就解析哪家会话」，故需要一层把「会话从哪来、怎么解析、
这家能测到什么」收口的抽象。**这是新的承重接缝**，改它必须同步 SKILL.md 与
`tests/test_sources_contract.py`。

三个承重设计，逐条说明为什么这么定：

1. **能力集是显式声明，不是探测出来的**。每个来源用 `capabilities` 声明它**能测**
   哪些概念（子代理 / MCP / plan mode / token 用量 …）。没声明 = 这家 harness 压根
   没有这个概念，或者它的会话记录里不落这个信号。

2. **「未测量 ≠ 0」由 `unmeasured_fields()` 落到具体字段名**。Codex 没有 `Workflow`
   概念，`workflow_sessions` 就恒为 0——若照常渲染成「0 次」，用户读到的是「你没用过
   Workflow 编排」这个**错误结论**。故来源测不到的指标字段一律进 `unmeasured`，渲染层
   打「未测量」、档位判定跳过该判据并挂 caveat。这正是项目定义的最危险故障（不报错、
   只安静产出错误结论）的对症解药。

3. **来源判定跟触发环境走，不检测用户装了什么**。`detect_source()` 只看当前进程的
   环境变量（在哪个 harness 里被调起），不去 PATH 上找二进制——用户在 Codex 里触发就
   分析 Codex 会话，哪怕他机器上也装着 Claude Code。

解析函数一律**惰性导入**：新增一家 parser 出问题（缺依赖 / 语法错）不应让整个
registry 构造失败，连带把另外两家也拖死。
"""
import importlib
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator

from .models import ParsedSession

# ---------------------------------------------------------------- 能力键

# 能力键 = 「这家 harness 的会话记录里能不能测到这个概念」。命名与 AggregateMetrics
# 的字段族对齐，避免出现「能力叫 A、字段叫 B」的二次映射负担。
CAP_TOOL_CALLS = "tool_calls"        # 工具调用可枚举（工具广度 / 各工具会话覆盖）
CAP_SUBAGENT = "subagent"            # 子代理派发（含真并行峰值）
CAP_WORKFLOW = "workflow"            # 确定性工作流编排
CAP_MCP = "mcp"                      # MCP 外部工具
CAP_SKILL = "skill"                  # 可复用技能 / 斜杠命令调用
CAP_PLAN_MODE = "plan_mode"          # 计划模式（先出方案再放行）
CAP_THINKING = "thinking"            # 推理块（深度推理强度）
CAP_BACKGROUND = "background_task"   # 后台委托
CAP_OPTION_PICK = "option_pick"      # 结构化选项应答（L2 协议硬信号）
CAP_TOKEN_USAGE = "token_usage"      # token 计量
CAP_EDITED_PATHS = "edited_paths"    # 会话编辑过哪些文件（git 主锚的交集来源）
CAP_GIT_OP = "git_operation"         # 会话内可观测的 git 提交操作
CAP_EDIT_COUNT = "edit_count"        # 编辑动作计数（结构化 patch）
CAP_CLI_VERSION = "cli_version"      # 记录里带 CLI 版本号（漂移雷达）
CAP_CUSTOM_SKILL = "custom_skill"    # 用户自建扩展可从文件系统扫描
CAP_PROJECT_MD = "project_md"        # 项目级约定文件（CLAUDE.md 一类）
CAP_HOOKS = "hooks"                  # 生命周期 hook（auto-scan 自动快照的前提）
CAP_TODO = "todo_list"               # 任务清单工具（多步任务维护进度）
CAP_WEB = "web_access"               # 联网检索 / 抓取工具

ALL_CAPABILITIES = frozenset({
    CAP_TOOL_CALLS, CAP_SUBAGENT, CAP_WORKFLOW, CAP_MCP, CAP_SKILL,
    CAP_PLAN_MODE, CAP_THINKING, CAP_BACKGROUND, CAP_OPTION_PICK,
    CAP_TOKEN_USAGE, CAP_EDITED_PATHS, CAP_GIT_OP, CAP_EDIT_COUNT,
    CAP_CLI_VERSION, CAP_CUSTOM_SKILL, CAP_PROJECT_MD, CAP_HOOKS,
    CAP_TODO, CAP_WEB,
})

# 能力键 → 缺了它就「未测量」的 AggregateMetrics 字段名。
# 这是**口径映射层的真相源**：报告渲染、档位判定、能力盲区都按它判「该不该把 0 当真值」。
# 只登记「该能力缺失时值必然恒 0/空、且 0 会被误读成负面结论」的字段；
# 像 session_count / human_input_count 这类任何来源都测得到的，不进这里。
CAPABILITY_METRICS: dict[str, tuple[str, ...]] = {
    CAP_TOOL_CALLS: ("tool_breadth", "tool_session_counts"),
    CAP_SUBAGENT: ("subagent_sessions", "max_parallel_agents", "parallel_agent_turns"),
    CAP_WORKFLOW: ("workflow_sessions",),
    CAP_MCP: ("mcp_sessions", "mcp_server_counts"),
    CAP_SKILL: ("skill_counts", "skill_total_counts"),
    CAP_PLAN_MODE: ("plan_mode_sessions", "plan_mode_count"),
    CAP_THINKING: ("thinking_block_count", "thinking_sessions"),
    CAP_BACKGROUND: ("background_task_count", "background_sessions"),
    CAP_OPTION_PICK: ("option_pick_count",),
    CAP_TOKEN_USAGE: ("token_usage", "token_total"),
    CAP_EDITED_PATHS: ("git_landed_count", "git_commit_total", "landed_ratio"),
    CAP_GIT_OP: ("commit_count", "landed_count", "dropped_count"),
    CAP_EDIT_COUNT: ("edit_count",),
    CAP_CUSTOM_SKILL: ("custom_skill_count",),
    CAP_PROJECT_MD: ("claude_md_sessions",),
    # CAP_CLI_VERSION / CAP_HOOKS / CAP_TODO / CAP_WEB 不对应 aggregate 标量字段：
    # 前两者各自在自己那层降级（parse_health 的版本漂移雷达 / auto-scan 能否接线），
    # 后两者只用于「能力盲区」该不该报（见 capabilities.py）——它们没有独立指标，
    # 只体现为 tool_session_counts 里有没有那个工具名。
}


# ---------------------------------------------------------------- 来源名
# 定义在此（而非 _SOURCES 旁边）：下面的工具名规范表要用它们做键。
CLAUDE_CODE = "claude-code"
CODEX = "codex"
OPENCODE = "opencode"


# 工具名规范化：各家给同一个概念起的名字不同（CC `TodoWrite` / opencode `todowrite` /
# Codex `update_plan`）。规则层的能力判定按 CC 名做（它是参照来源），故各来源的工具名
# 在**进聚合之前**统一改写成这套规范名。
#
# **是改名不是加名**——加名会让 `tool_breadth` 凭空虚高一档。
# 只登记有实测/源码证据的名字：猜一个名字进来，命中了是运气，没命中就是又一条
# 「不报错的错报告」（报告说人家没用过任务清单，其实人家一直在用）。
CANONICAL_TOOL_NAMES: dict[str, dict[str, str]] = {
    CLAUDE_CODE: {},        # 参照来源，规范名就是它自己的名字
    CODEX: {
        # 依据：本机 logs_2.sqlite 里发往 API 的工具注册表实读。
        # Codex 的 update_plan 是 todo 清单语义（不是 CC 的 plan mode 放行门），
        # 故映到 TodoWrite 而**不是** EnterPlanMode。
        "update_plan": "TodoWrite",
    },
    OPENCODE: {
        # 依据：反编译 v1.18.18 服务端 bundle 里的内置工具清单（legacy + 新 effect 两套同名）。
        "todowrite": "TodoWrite",
        "websearch": "WebSearch",
        "webfetch": "WebFetch",
        "skill": "Skill",
        # plan_exit 是「退出 plan 模式、转入执行」的门，语义与 CC 的 ExitPlanMode 对齐
        "plan_exit": "ExitPlanMode",
        # task → Agent 由 opencode_source 在 parser 层就改好了（signals 按字面量数
        # subagent_sessions，不改名那里会恒 0）；这里再列一次是幂等的，也让这张表读起来完整。
        "task": "Agent",
    },
}


def canonical_tools(tools, source_name: str) -> list[str]:
    """把一组工具名改写成跨来源可比的规范名（纯函数，去重排序）。

    未登记的名字**原样保留**：那是该 harness 特有的工具，本来就该以本名出现在
    工具广度里，不该被抹平也不该被丢掉。
    """
    table = CANONICAL_TOOL_NAMES.get(source_name, {})
    return sorted({table.get(t, t) for t in (tools or ())})


def unmeasured_fields(capabilities) -> tuple[str, ...]:
    """该来源**测不到**的 aggregate 字段名（排序去重）。

    纯函数。入参是来源的能力集，出参交给渲染层打「未测量」、交给 `decide_stage`
    跳过对应判据。**空集不代表全测得到**——只代表这家把 CAPABILITY_METRICS 登记的
    能力都占全了。
    """
    caps = frozenset(capabilities or ())
    missing: set[str] = set()
    for cap, fields in CAPABILITY_METRICS.items():
        if cap not in caps:
            missing.update(fields)
    return tuple(sorted(missing))


# ---------------------------------------------------------------- Source 抽象

@dataclass(frozen=True)
class Source:
    """一家 harness 的会话来源。

    `iter_sessions` 直接产出 `ParsedSession` 而不是「文件列表 + parse」——因为
    opencode 一类把会话存在单个 SQLite 库里，根本没有「每会话一个文件」这回事；
    强行套文件模型会逼着 DB 源造假路径。
    """
    name: str
    label: str
    capabilities: frozenset[str]
    # 触发环境判定用的环境变量名：进程里出现任一个即认定跑在这家 harness 里
    env_markers: tuple[str, ...]
    default_root_fn: Callable[[], Path]
    iter_sessions_fn: Callable[[Path], Iterator[ParsedSession]]
    earliest_ts_fn: Callable[[Path], str | None]
    # 报告与安装器展示用：这家的会话数据落在哪（给用户看的说明，不参与逻辑）
    root_hint: str = ""

    @property
    def default_root(self) -> Path:
        return self.default_root_fn()

    @property
    def unmeasured(self) -> tuple[str, ...]:
        return unmeasured_fields(self.capabilities)

    def supports(self, cap: str) -> bool:
        return cap in self.capabilities

    def iter_sessions(self, root) -> Iterator[ParsedSession]:
        return self.iter_sessions_fn(Path(root))

    def earliest_ts(self, root) -> str | None:
        return self.earliest_ts_fn(Path(root))


def _lazy(module: str, func: str) -> Callable:
    """惰性绑定 `ai_coding_insights.<module>.<func>`。

    registry 在 import 期构造，若此处直接 import 三家 parser，任何一家有问题
    （缺 stdlib 可选模块如 sqlite3、或自身语法错）都会让整个 CLI 起不来——
    包括另外两家本来好好的来源。惰性绑定把故障面收窄到「真的用到那家时」。
    """
    def call(*args, **kwargs):
        return getattr(importlib.import_module(f".{module}", __package__), func)(*args, **kwargs)
    return call


# ---------------------------------------------------------------- 三家来源

_SOURCES: dict[str, Source] = {
    CLAUDE_CODE: Source(
        name=CLAUDE_CODE,
        label="Claude Code",
        # 参照来源：本项目全部指标最初都是照它的 transcript 形状定义的，能力全集。
        capabilities=ALL_CAPABILITIES,
        # CLAUDECODE=1 是 CC 给子进程注入的标记；SESSION_ID 供 detect_run_model 用，
        # 两者任一出现即认定跑在 CC 里。
        env_markers=("CLAUDECODE", "CLAUDE_CODE_SESSION_ID"),
        default_root_fn=lambda: Path.home() / ".claude" / "projects",
        iter_sessions_fn=_lazy("cc_source", "iter_sessions"),
        earliest_ts_fn=_lazy("cc_source", "earliest_ts"),
        root_hint="~/.claude/projects/**/*.jsonl",
    ),
    CODEX: Source(
        name=CODEX,
        label="Codex CLI",
        # 能力集以**实测本机 rollout 记录**为准，逐条依据见 codex_source.py 顶部的
        # 字段映射说明。声明得保守：拿不准的一律不声明（未测量比假装测到安全）。
        # 明确不声明的几项与理由：
        # - CAP_MCP：工具规格里 MCP 以 `mcp__<server>` 命名空间出现，但本机零调用样本，
        #   真实调用时 function_call.name 的形状未验证。parser 仍会宽松匹配收集，
        #   真收到了会由 `aggregate_metrics` 把它从 unmeasured 里摘掉（见那里的规则）。
        # - CAP_SUBAGENT / CAP_WORKFLOW / CAP_PLAN_MODE / CAP_BACKGROUND / CAP_OPTION_PICK：
        #   Codex 无对位概念（update_plan 是 todo 清单不是放行门；子会话落在另一个
        #   rollout 文件、父子关系只在 sqlite 里）。
        # - CAP_GIT_OP：Codex 的 git 提交只是一条普通 exec_command，无结构化回执。
        # - CAP_HOOKS：Codex 无生命周期 hook 机制（config.toml 的 notify 语义不同），
        #   故 auto-scan 自动快照在 Codex 下不可用。
        # CAP_TODO：update_plan 已在工具注册表里实读到。
        # 不声明 CAP_WEB：注册表里没看到联网检索工具，且没有真实调用样本可证——
        # 声明了就会在报告里报「你没用过 Web 取证」这条盲区，而那可能纯属冤枉。
        capabilities=frozenset({
            CAP_TOOL_CALLS, CAP_TOKEN_USAGE, CAP_EDITED_PATHS, CAP_EDIT_COUNT,
            CAP_THINKING, CAP_CLI_VERSION, CAP_PROJECT_MD, CAP_CUSTOM_SKILL,
            CAP_TODO,
        }),
        env_markers=("CODEX_SANDBOX", "CODEX_HOME", "CODEX_SESSION_ID"),
        default_root_fn=lambda: Path.home() / ".codex" / "sessions",
        iter_sessions_fn=_lazy("codex_source", "iter_sessions"),
        earliest_ts_fn=_lazy("codex_source", "earliest_ts"),
        root_hint="~/.codex/sessions/**/rollout-*.jsonl",
    ),
    OPENCODE: Source(
        name=OPENCODE,
        label="opencode",
        # 依据：本机 v1.18.18 实库（session/message/part 三张表）+ 反编译服务端 bundle 里
        # 的 effect-schema 定义，逐条见 opencode_source.py 顶部。
        # 明确不声明的几项与理由：
        # - CAP_MCP：MCP 工具名是 `<server>_<tool>`（下划线拼接），而内置工具里
        #   apply_patch / todowrite / plan_exit 也含下划线，纯字符串匹配会误判；要准就得
        #   去读 ~/.config/opencode/opencode.jsonc 的 mcp 键（那文件里有明文密钥，只能取键名）。
        #   v1 保守不声明，parser 真识别到了会由 aggregate_metrics 自动摘出 unmeasured。
        # - CAP_WORKFLOW / CAP_GIT_OP / CAP_BACKGROUND / CAP_OPTION_PICK / CAP_HOOKS /
        #   CAP_CUSTOM_SKILL：无对位概念或未在本机数据中验证。
        # 另注：git 分支基本取不到（只有实验性 workspace 表才记 branch，普通会话不记），
        # 故 ParsedSession.git_branch 对本来源恒为 None——它不进 unmeasured 字段集，
        # 因为归属判定走的是 cwd，分支只是展示信息。
        # CAP_TODO / CAP_WEB / CAP_SKILL：内置工具清单里 todowrite / websearch+webfetch /
        # skill 都在（反编译 bundle 实读），故这三项能判「用没用过」。
        capabilities=frozenset({
            CAP_TOOL_CALLS, CAP_TOKEN_USAGE, CAP_EDITED_PATHS, CAP_EDIT_COUNT,
            CAP_SUBAGENT, CAP_THINKING, CAP_PLAN_MODE, CAP_CLI_VERSION,
            CAP_PROJECT_MD, CAP_CUSTOM_SKILL, CAP_TODO, CAP_WEB, CAP_SKILL,
        }),
        env_markers=("OPENCODE", "OPENCODE_SESSION_ID", "OPENCODE_CONFIG_DIR"),
        default_root_fn=lambda: Path.home() / ".local" / "share" / "opencode",
        iter_sessions_fn=_lazy("opencode_source", "iter_sessions"),
        earliest_ts_fn=_lazy("opencode_source", "earliest_ts"),
        root_hint="~/.local/share/opencode/opencode*.db（SQLite；$OPENCODE_DB 可覆盖）",
    ),
}

SOURCE_NAMES = tuple(_SOURCES)


class UnknownSourceError(ValueError):
    """未知来源名。必须报错中止，不静默回退到 claude-code——
    静默回退会让 Codex 用户拿到一份分析了别人（CC）会话的报告。"""


def get_source(name: str) -> Source:
    try:
        return _SOURCES[name]
    except KeyError:
        raise UnknownSourceError(
            f"未知会话来源 {name!r}，可选：{', '.join(SOURCE_NAMES)}") from None


def detect_source(env: dict | None = None) -> str:
    """按**触发环境**判定来源（不检测用户装了什么）。

    判定顺序（命中即停）：
    1. `ACI_SOURCE` 显式覆盖（调试 / 安装器写死时用）；
    2. harness 注入的环境变量标记；
    3. 兜底 `claude-code`（本项目的参照来源，也是绝大多数存量用户）。

    纯函数（默认读 `os.environ`，可注入）。返回来源名字符串，不返回 Source 对象——
    调用方通常还要把它写进报告标注，字符串更省事。
    """
    e = os.environ if env is None else env
    explicit = (e.get("ACI_SOURCE") or "").strip()
    if explicit:
        get_source(explicit)   # 拼错要立刻炸，不静默回退
        return explicit
    for name, src in _SOURCES.items():
        if any(e.get(marker) for marker in src.env_markers):
            return name
    return CLAUDE_CODE


def resolve_root(source: Source, explicit: str | None) -> Path:
    """会话数据根目录：显式路径优先，否则取该来源默认根（展开 ~）。"""
    if explicit:
        return Path(explicit).expanduser()
    return Path(source.default_root).expanduser()


# ---------------------------------------------------------------- 文件型来源脚手架

def file_scanner(pattern: str, parse_fn: Callable[[Path], ParsedSession]
                 ) -> Callable[[Path], Iterator[ParsedSession]]:
    """把「按 glob 找文件 + 逐个解析」包成 `iter_sessions`。

    单个文件解析失败不吞整场扫描——一份写坏的 transcript 不该让用户完全拿不到报告。
    """
    def iter_sessions(root: Path) -> Iterator[ParsedSession]:
        for path in sorted(Path(root).glob(pattern)):
            try:
                yield parse_fn(path)
            except (OSError, UnicodeDecodeError):
                continue
    return iter_sessions


def head_earliest_ts(pattern: str, ts_of_line: Callable[[dict], datetime | None],
                     head_lines: int = 50) -> Callable[[Path], str | None]:
    """把「每个文件只读前 N 行找最早时间戳」包成 `earliest_ts`。

    只读文件头：transcript 可能极大，绝不全读；记录按时间追加，首个可解析时间即
    该文件最早。用途是与窗口 since_date 比对，识别 harness 自身清理策略导致的
    「名义窗口 vs 实际数据起点」错位。
    """
    import json

    def earliest_ts(root: Path) -> str | None:
        earliest: datetime | None = None
        for path in Path(root).glob(pattern):
            try:
                with path.open(encoding="utf-8") as f:
                    for _, line in zip(range(head_lines), f):
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(rec, dict):
                            continue
                        ts = ts_of_line(rec)
                        if ts is not None:
                            if earliest is None or ts < earliest:
                                earliest = ts
                            break
            except (OSError, UnicodeDecodeError):
                continue
        return earliest.isoformat() if earliest is not None else None
    return earliest_ts
