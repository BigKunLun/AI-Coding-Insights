from dataclasses import dataclass, field


@dataclass(frozen=True)
class RemoteIdentity:
    host: str
    org: str | None


@dataclass(frozen=True)
class RemoteRule:
    host: str
    org: str | None = None


@dataclass(frozen=True)
class UserTurn:
    uuid: str
    text: str
    timestamp: str

    @property
    def char_len(self) -> int:
        return len(self.text)


@dataclass(frozen=True)
class CommitRef:
    sha: str
    kind: str


@dataclass(frozen=True)
class RepoOutcome:
    """单仓库窗口级 git 成果（git log × 会话时间窗细口径归属，见 git_outcome.py）。"""
    landed_count: int     # 落入某会话时间窗(±宽限)的本人提交数（HEAD 祖先=已落地硬证据）
    outside_count: int    # 窗口内本人提交但在所有会话窗之外（仅参考，不进任何比率）


@dataclass
class ParsedSession:
    # --- 必填字段（无默认值）---
    file_path: str
    session_id: str
    cwd: str
    git_branch: str | None
    user_turns: list[UserTurn]
    tools_used: list[str]
    models_used: list[str]
    first_ts: str | None
    last_ts: str | None
    # --- 可选字段（有默认值）---
    commits: list = field(default_factory=list)   # list[CommitRef]
    edit_count: int = 0
    token_usage: dict = field(default_factory=dict)  # {model: {input,output,cache_read,cache_creation}}
    option_pick_count: int = 0      # AskUserQuestion 已答题数（每题=1 个决策点）
    plan_mode_count: int = 0        # plan 信号次数：EnterPlanMode/ExitPlanMode tool_use 与 permission-mode:plan 记录之并集
    skill_names: list = field(default_factory=list)   # 去重 skill 名列表（从 Skill tool_use.input.skill 提取）
    mcp_servers: list = field(default_factory=list)    # 去重 MCP server 名列表（从 mcp__<server>__<tool> 解析）
    thinking_block_count: int = 0   # assistant content 里 type=="thinking" 的块数（深度推理强度）
    background_task_count: int = 0  # 带 run_in_background:true 的 tool_use 数（后台委托）
    max_parallel_agents: int = 0    # 单条 assistant message 内 Agent tool_use 的峰值（真并行度）
    parallel_agent_turns: int = 0   # 单条 message 内并发派出 ≥2 个 Agent 的轮次数
    cc_versions: list = field(default_factory=list)        # 去重排序的 CC version 列表（来自记录 version 字段）
    record_type_counts: dict = field(default_factory=dict) # {记录 type: 条数}，含 parser 不处理的类型，供漂移雷达


@dataclass
class SessionStats:
    session_id: str
    cwd: str
    turn_count: int
    short_turn_ratio: float
    duration_seconds: float | None
    tools_used: list[str]
    models_used: list[str]
    short_turn_count: int = 0       # 极短/跟随输入条数（ratio 的分子，避免反推浮点误差）


@dataclass
class InsightsReport:
    generated_at: str
    lookback_days: int
    sessions: list[SessionStats]
    included_projects: list[str]
    completeness: dict


@dataclass
class OutcomeStats:
    session_id: str
    cwd: str
    commit_count: int
    landed_count: int
    edit_count: int

    @property
    def landed_ratio(self) -> float:
        return self.landed_count / self.commit_count if self.commit_count else 0.0


@dataclass
class AggregateMetrics:
    """窗口聚合指标 —— 字段按 Python dataclass 规则排列（无默认值先，有默认值后）。

    聚合规则分类（新增字段时对照选择，避免求和/取峰/去重混用）：
    - count 类（跨会话累加 sum）：session_count, human_input_count, active_days, ...
    - peak 类（取最大值）：tool_breadth, max_concurrent_sessions
    - dict 类（按 key 跨会话累加 values）：tool_session_counts, skill_counts, ...
    - list 类（收集后排序）：daily
    - derived 类（从其他字段计算）：avg_turns, landed_ratio, duration_median_min, trend
    """
    # === 必填字段（无默认值）===
    session_count: int
    human_input_count: int          # = sum(每会话 turn_count)
    active_days: int                # 不同自然日(UTC)数
    avg_turns: float
    tool_breadth: int               # 去重工具种类数(跨所有会话)
    tool_session_counts: dict       # {tool: 用到它的会话数}
    subagent_sessions: int          # tools_used 含 "Agent" 的会话数
    workflow_sessions: int          # 含 "Workflow" 的会话数
    mcp_sessions: int               # 含任一以 "mcp__" 开头的工具的会话数
    model_counts: dict              # {model: 用到它的会话数}
    commit_count: int               # sum
    landed_count: int               # sum
    edit_count: int                 # sum
    duration_median_min: float | None  # 剔跨天污染后的时长中位数(分钟);无有效值则 None
    project_breakdown: dict         # {cwd: {"sessions","commits","landed","edits"}}
    anchor_counts: dict             # {"override","error","code","link": 命中该锚点的 turn 总数}

    # === 可选字段（有默认值）===
    token_usage: dict = field(default_factory=dict)  # {model: {input,output,cache_read,cache_creation}} 跨会话累加
    token_total: int = 0                             # 四项跨模型总和(含cache,非计费口径)，进快照同比
    trend: dict | None = None                        # 窗口前半 vs 后半硬指标对比;会话/时间戳不足则 None
    short_turn_count: int = 0       # 窗口内极短输入总数（L1 硬锚分子）
    option_pick_count: int = 0      # 窗口内 AskUserQuestion 已答题总数（L2 硬锚分子）
    decision_point_count: int = 0   # = human_input_count + option_pick_count（姿势分布分母）
    git_landed_count: int = 0       # git 主锚：窗口内本人提交 × 会话窗归属（与奖励挂钩）
    git_outside_count: int = 0      # 窗口内本人提交但在会话窗外（参考）
    friction_stats: dict = field(default_factory=dict)  # error/override 会话集中度 + 轮次 top（纯数字，无业务语义，供教练专家个性化判摩擦）
    plan_mode_sessions: int = 0     # 使用过 EnterPlanMode/ExitPlanMode 的会话数
    plan_mode_count: int = 0        # 跨会话 EnterPlanMode 总次数
    concurrent_days: int = 0        # 出现过并发(≥2个重叠≥300s的会话)的天数
    claude_md_sessions: int = 0     # 编辑过 CLAUDE.md 的会话数
    max_concurrent_sessions: int = 1  # 窗口内最大并发会话数（≥300s 重叠）
    skill_counts: dict = field(default_factory=dict)       # {skill_name: 使用会话数}
    mcp_server_counts: dict = field(default_factory=dict)  # {server_name: 使用会话数}
    daily: list = field(default_factory=list)  # [{date, session_count, human_input_count, commit_count, landed_count, edit_count, token_total}]
    custom_skill_count: int = 0        # 用户自建 skill 文件数（来自文件系统扫描，非 transcript）
    duration_p90_min: float | None = None  # P90 会话时长（分钟），剔除微会话污染，重度用户更准确
    turn_p90: int = 0                      # P90 轮次，剔除微会话污染，重度用户更准确
    thinking_block_count: int = 0   # sum：深度推理块总数（深度信号）
    thinking_sessions: int = 0      # count：出现过 thinking 块的会话数
    background_task_count: int = 0  # sum：后台委托(run_in_background) tool_use 总数
    background_sessions: int = 0    # count：出现过后台委托的会话数
    max_parallel_agents: int = 0    # peak：跨会话单轮最大并行 Agent 数（真并行度）
    parallel_agent_turns: int = 0   # sum：真并行轮次总数（单轮 ≥2 Agent 同发）

    @property
    def dropped_count(self) -> int:
        # transcript 观测到但已不在 HEAD 历史的提交（被 reset/丢弃的硬证据）
        return max(0, self.commit_count - self.landed_count)

    @property
    def landed_ratio(self) -> float:
        # 已知证据下的落地率：git 主锚落地 /（落地 + 已知丢弃）。
        # transcript 不可观测（commit_count=0，如旧版 CC 无 gitOperation 回执）时
        # dropped 未知按 0 计——有 git 落地即 1.0，无任何证据为 0.0。
        denom = self.git_landed_count + self.dropped_count
        return self.git_landed_count / denom if denom else 0.0
