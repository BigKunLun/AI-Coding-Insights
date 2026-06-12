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


@dataclass
class ParsedSession:
    file_path: str
    session_id: str
    cwd: str
    git_branch: str | None
    user_turns: list[UserTurn]
    tools_used: list[str]
    models_used: list[str]
    first_ts: str | None
    last_ts: str | None
    commits: list = field(default_factory=list)   # list[CommitRef]
    edit_count: int = 0
    token_usage: dict = field(default_factory=dict)  # {model: {input,output,cache_read,cache_creation}}
    option_pick_count: int = 0      # AskUserQuestion 已答题数（每题=1 个决策点）


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
    token_usage: dict = field(default_factory=dict)  # {model: {input,output,cache_read,cache_creation}} 跨会话累加
    token_total: int = 0                             # 四项跨模型总和(含cache,非计费口径)，进快照同比
    trend: dict | None = None                        # 窗口前半 vs 后半硬指标对比;会话/时间戳不足则 None
    short_turn_count: int = 0       # 窗口内极短输入总数（L1 硬锚分子）
    option_pick_count: int = 0      # 窗口内 AskUserQuestion 已答题总数（L2 硬锚分子）
    decision_point_count: int = 0   # = human_input_count + option_pick_count（姿势分布分母）

    @property
    def landed_ratio(self) -> float:
        return self.landed_count / self.commit_count if self.commit_count else 0.0
