import math
import re
import statistics
from collections import defaultdict
from datetime import timedelta

from .models import AggregateMetrics, OutcomeStats, ParsedSession, SessionStats
from .sources import (CLAUDE_CODE, Source, canonical_tools, get_source,
                      unmeasured_fields)
from .timeutil import parse_timestamp


def _p90_index(n: int) -> int:
    """P90 最近秩下标 = ceil(0.9·n)−1，小样本不退化为末位（即 max）。

    旧式 int(0.9·n) 在 n≤10 时恒等于 n−1（末位），把 P100 当 P90 上报；
    最近秩公式在 n=10 时取第 9 个、n≥11 与旧式一致。调用方需保证 n≥1。
    """
    return max(0, math.ceil(n * 0.9) - 1)

_FOLLOW_WORDS = {"继续","好","好的","可以","行","对","嗯","ok","okay","yes","y",
                 "go","next","下一步","sure"}

_CODE = re.compile(r"```|^\s{4}\S|[{};]\s*$", re.M)
_ERR = re.compile(r"(?i)error|traceback|exception|失败|报错|panic|stacktrace")
_LINK = re.compile(r"https?://")
_OVERRIDE = re.compile(r"不对|不是|应该|为什么|重来|错了|其实|别这样|改成")


def anchors(text: str) -> dict:
    return {"code": bool(_CODE.search(text)), "error": bool(_ERR.search(text)),
            "link": bool(_LINK.search(text)), "override": bool(_OVERRIDE.search(text))}


_DURATION_OUTLIER_SEC = 12 * 3600

def _is_short(turn, max_chars: int) -> bool:
    t = turn.text.strip()
    # 长度按去空白后算：纯空白/回车垫长的输入是放行不是实质输入，不应抬高「非短」占比
    # follow-word OR 分支为 §13 后续更严 tier（max_chars≤2）预留；当前 max_chars=6 下被长度分支覆盖、不额外触发
    return len(t) <= max_chars or t.lower() in _FOLLOW_WORDS

def compute_stats(session: ParsedSession, short_turn_max_chars: int) -> SessionStats:
    turns = session.user_turns
    n = len(turns)
    short = sum(1 for t in turns if _is_short(t, short_turn_max_chars))
    a, b = parse_timestamp(session.first_ts), parse_timestamp(session.last_ts)
    dur = (b - a).total_seconds() if a and b else None
    return SessionStats(
        session_id=session.session_id, cwd=session.cwd,
        # MVP proxy：把 §5 B 两个信号（极短占比 + 跟随词频）融成一个比值；tier 校准属 §13 后续，勿当已校准 L1 信号读
        turn_count=n, short_turn_ratio=(short / n if n else 0.0),
        duration_seconds=dur, tools_used=session.tools_used,
        models_used=session.models_used, short_turn_count=short)


def compute_trend(sessions, stats, outcomes) -> dict | None:
    """窗口内前半 vs 后半硬指标对比。按会话 first_ts 的实际起止时间中点切分。

    会话不足 2 个、或有效时间戳不足 2 个 → None（首次短窗口不强行出趋势）。
    """
    stamped = [(ts, s, st, oc)
               for s, st, oc in zip(sessions, stats, outcomes)
               if (ts := parse_timestamp(s.first_ts))]
    if len(stamped) < 2:
        return None
    times = [t for t, *_ in stamped]
    mid = min(times) + (max(times) - min(times)) / 2

    def _half(items):
        n = len(items)
        commits = sum(oc.commit_count for _, _, _, oc in items)
        landed = sum(oc.landed_count for _, _, _, oc in items)
        override = error = 0
        for _, s, _, _ in items:
            for turn in s.user_turns:
                a = anchors(turn.text)
                override += a["override"]
                error += a["error"]
        turns = sum(st.turn_count for _, _, st, _ in items)
        shorts = sum(st.short_turn_count for _, _, st, _ in items)
        return {"sessions": n, "commits": commits, "landed": landed,
                "landed_ratio": landed / commits if commits else None,
                "override": override, "error": error,
                "short_ratio": shorts / turns if turns else 0.0}

    first = [x for x in stamped if x[0] <= mid]
    second = [x for x in stamped if x[0] > mid]
    if not first or not second:
        return None
    return {"first_half": _half(first), "second_half": _half(second)}


def _compute_daily(sessions, stats, outcomes) -> list[dict]:
    """按 first_ts UTC 日期分组，汇总每日基础指标。

    无时间戳的会话不参与；返回按日期升序的 dict 列表。
    """
    buckets: dict = defaultdict(lambda: {"session_count": 0, "human_input_count": 0,
                                          "commit_count": 0, "landed_count": 0,
                                          "edit_count": 0, "token_total": 0})
    for s, st, oc in zip(sessions, stats, outcomes):
        ts = parse_timestamp(s.first_ts)
        if ts is None:
            continue
        d = ts.date()
        b = buckets[d]
        b["session_count"] += 1
        b["human_input_count"] += st.turn_count
        b["commit_count"] += oc.commit_count
        b["landed_count"] += oc.landed_count
        b["edit_count"] += oc.edit_count
        b["token_total"] += sum(v for b2 in (s.token_usage or {}).values() for v in b2.values())

    return [{"date": d.isoformat(), **buckets[d]} for d in sorted(buckets)]


def compute_concurrency(sessions, overlap_threshold_sec: int = 300
                        ) -> tuple[int, int]:
    """检测窗口内会话并发情况。

    只有时间区间重叠 ≥ *overlap_threshold_sec* 才算并发。
    算法：对每个会话起点 t 扫描所有区间，统计覆盖 [t, t+threshold] 的个数。
    区间覆盖条件等价于 pairwise 重叠 ≥ threshold（区间图中存在共同交点的
    充要条件是所有区间交集非空）。O(n²)，当前会话数规模下可接受。
    返回 (max_concurrent, concurrent_days)。
    """
    intervals = []
    for s in sessions:
        a = parse_timestamp(s.first_ts)
        b = parse_timestamp(s.last_ts)
        if a is None or b is None or b <= a:
            continue
        intervals.append((a, b))

    if not intervals:
        return (1, 0)

    thresh = timedelta(seconds=overlap_threshold_sec)
    # 收集所有候选时间点：每个区间的起点
    starts = sorted({a for a, _ in intervals})

    max_concurrent = 1
    concurrent_days: set = set()

    for t in starts:
        t_end = t + thresh
        count = sum(1 for a, b in intervals if a <= t and b >= t_end)
        if count > max_concurrent:
            max_concurrent = count
        if count >= 2:
            concurrent_days.add(t.date())

    return (max_concurrent, len(concurrent_days))


def resolve_metric_source(sessions, source=None) -> Source:
    """本窗口数据来自哪家 harness（纯函数）。

    显式传入优先；否则从会话自带的 `source` 标记推断——同一次取数恒为单一来源
    （来源跟触发环境走，不混合），故取第一条即可；空窗口兜底 claude-code。
    """
    if source is not None:
        return source if isinstance(source, Source) else get_source(source)
    for s in sessions or ():
        if getattr(s, "source", None):
            return get_source(s.source)
    return get_source(CLAUDE_CODE)


def aggregate_metrics(sessions, stats, outcomes, repo_outcomes=None,
                      custom_skill_count: int = 0,
                      claude_md_sessions: int = 0,
                      source=None, extra_unmeasured=()) -> AggregateMetrics:
    # sessions: list[ParsedSession]; stats: list[SessionStats]; outcomes: list[OutcomeStats]
    # 三个列表一一对应(同序、等长)。repo_outcomes: {仓库根: RepoOutcome}——git 主锚
    # 由 cli 按窗口采集后传入，None=未采集（如旧调用路径）按零计。
    # source: 来源名 / Source / None（None 从会话标记推断）。它决定 `unmeasured`——
    # 该来源测不到的字段，其 0 是「没测」不是「没做」，全链路据此降级。
    # extra_unmeasured: 本次取数路径**主动省掉**的测量（如 auto-scan 不跑 git log），
    # 与来源能力无关但同样「未测量≠0」，一并并入。
    src = resolve_metric_source(sessions, source)
    unmeasured = sorted(set(unmeasured_fields(src.capabilities)) | set(extra_unmeasured))
    session_count = len(sessions)
    human_input_count = sum(st.turn_count for st in stats)
    short_turn_count = sum(st.short_turn_count for st in stats)
    option_pick_count = sum(s.option_pick_count for s in sessions)
    decision_point_count = human_input_count + option_pick_count
    avg_turns = human_input_count / session_count if session_count else 0.0

    days = {ts.date() for s in sessions if (ts := parse_timestamp(s.first_ts))}
    active_days = len(days)

    tool_session_counts: dict = {}
    model_counts: dict = {}
    subagent_sessions = 0
    workflow_sessions = 0
    mcp_sessions = 0
    for s in sessions:
        # 工具名先规范化再聚合：各家给同一概念起的名字不同（`todowrite` / `TodoWrite` /
        # `update_plan`），不统一的话「用没用过任务清单」这类判定在非 CC 来源上恒为否，
        # 报告会误报一屏根本不存在的能力盲区。规范表在 sources.CANONICAL_TOOL_NAMES。
        tools = set(canonical_tools(s.tools_used, getattr(s, "source", CLAUDE_CODE)))
        for t in tools:
            tool_session_counts[t] = tool_session_counts.get(t, 0) + 1
        if "Agent" in tools:
            subagent_sessions += 1
        if "Workflow" in tools:
            workflow_sessions += 1
        if any(t.startswith("mcp__") for t in tools):
            mcp_sessions += 1
        for m in set(s.models_used):
            model_counts[m] = model_counts.get(m, 0) + 1
    tool_breadth = len(tool_session_counts)

    commit_count = sum(o.commit_count for o in outcomes)
    landed_count = sum(o.landed_count for o in outcomes)
    edit_count = sum(o.edit_count for o in outcomes)
    git_landed_count = sum(r.landed_count for r in (repo_outcomes or {}).values())
    git_commit_total = sum(r.total_count for r in (repo_outcomes or {}).values())

    durations = [st.duration_seconds for st in stats
                 if st.duration_seconds is not None
                 and 0 <= st.duration_seconds <= _DURATION_OUTLIER_SEC]
    duration_median_min = statistics.median(durations) / 60 if durations else None
    # P90 指标：剔除大量微会话（1-2 轮即关闭）拉低均值/中位数，P90 反映"实际工作会话"水平
    durations_sorted = sorted(durations)
    duration_p90_min = (durations_sorted[_p90_index(len(durations_sorted))] / 60
                        if durations_sorted else None)
    turns_sorted = sorted(st.turn_count for st in stats)
    turn_p90 = turns_sorted[_p90_index(len(turns_sorted))] if turns_sorted else 0

    project_breakdown: dict = {}
    for s, o in zip(sessions, outcomes):
        entry = project_breakdown.setdefault(
            s.cwd, {"sessions": 0, "commits": 0, "landed": 0, "edits": 0})
        entry["sessions"] += 1
        entry["commits"] += o.commit_count
        entry["landed"] += o.landed_count
        entry["edits"] += o.edit_count

    anchor_counts = {"override": 0, "error": 0, "code": 0, "link": 0}
    per_sess_err: list[int] = []
    per_sess_ovr: list[int] = []
    for s in sessions:
        err = ovr = 0
        for turn in s.user_turns:
            hits = anchors(turn.text)
            for key in anchor_counts:
                if hits[key]:
                    anchor_counts[key] += 1
            err += hits["error"]
            ovr += hits["override"]
        per_sess_err.append(err)
        per_sess_ovr.append(ovr)

    # 摩擦集中度：全局计数看不出「集中于少数会话」，教练专家又被禁止从均值推
    # 分布——这里给确定性的会话级分布摘要（命中会话数 + 单会话 top3 + 轮次 top3），
    # 纯数字不带会话 id / 路径，零脱敏面。
    friction_stats = {
        "error_session_count": sum(1 for x in per_sess_err if x),
        "error_top_counts": sorted((x for x in per_sess_err if x), reverse=True)[:3],
        "override_session_count": sum(1 for x in per_sess_ovr if x),
        "override_top_counts": sorted((x for x in per_sess_ovr if x), reverse=True)[:3],
        "top_session_turns": sorted((st.turn_count for st in stats), reverse=True)[:3],
    }

    token_usage: dict = {}
    for s in sessions:
        for model, b in (s.token_usage or {}).items():
            agg = token_usage.setdefault(model, {"input": 0, "output": 0,
                                                 "cache_read": 0, "cache_creation": 0})
            for k in agg:
                agg[k] += b.get(k, 0)
    # 含 cache_read/cache_creation；非计费口径，仅作量级粗指标，勿当成本读
    token_total = sum(v for b in token_usage.values() for v in b.values())

    # -- plan_mode 聚合 --
    plan_mode_sessions = sum(1 for s in sessions if s.plan_mode_count > 0)
    plan_mode_count = sum(s.plan_mode_count for s in sessions)

    # -- skill 会话覆盖（skill_counts=使用会话数）+ 调用频次（skill_total_counts=调用次数）/ MCP 聚合 --
    skill_counts: dict = {}
    for s in sessions:
        for sk in s.skill_names:
            skill_counts[sk] = skill_counts.get(sk, 0) + 1
    skill_total_counts: dict = {}
    for s in sessions:
        for sk, c in (s.skill_invoke_counts or {}).items():
            skill_total_counts[sk] = skill_total_counts.get(sk, 0) + c
    mcp_server_counts: dict = {}
    for s in sessions:
        for sv in s.mcp_servers:
            mcp_server_counts[sv] = mcp_server_counts.get(sv, 0) + 1

    # -- 高阶维度信号聚合（thinking/background 走「总量+命中会话」，真并行走「峰值+轮次和」）--
    thinking_block_count = sum(s.thinking_block_count for s in sessions)
    thinking_sessions = sum(1 for s in sessions if s.thinking_block_count > 0)
    background_task_count = sum(s.background_task_count for s in sessions)
    background_sessions = sum(1 for s in sessions if s.background_task_count > 0)
    max_parallel_agents = max((s.max_parallel_agents for s in sessions), default=0)
    parallel_agent_turns = sum(s.parallel_agent_turns for s in sessions)

    # -- 日粒度聚合 --
    daily = _compute_daily(sessions, stats, outcomes)

    # -- 并发检测 --
    max_concurrent_sessions, concurrent_days = compute_concurrency(sessions)

    return AggregateMetrics(
        session_count=session_count,
        human_input_count=human_input_count,
        active_days=active_days,
        avg_turns=avg_turns,
        tool_breadth=tool_breadth,
        tool_session_counts=tool_session_counts,
        subagent_sessions=subagent_sessions,
        workflow_sessions=workflow_sessions,
        mcp_sessions=mcp_sessions,
        model_counts=model_counts,
        commit_count=commit_count,
        landed_count=landed_count,
        edit_count=edit_count,
        duration_median_min=duration_median_min,
        project_breakdown=project_breakdown,
        anchor_counts=anchor_counts,
        token_usage=token_usage,
        token_total=token_total,
        trend=compute_trend(sessions, stats, outcomes),
        short_turn_count=short_turn_count,
        option_pick_count=option_pick_count,
        decision_point_count=decision_point_count,
        git_landed_count=git_landed_count,
        git_commit_total=git_commit_total,
        friction_stats=friction_stats,
        plan_mode_sessions=plan_mode_sessions,
        plan_mode_count=plan_mode_count,
        skill_counts=skill_counts,
        skill_total_counts=skill_total_counts,
        mcp_server_counts=mcp_server_counts,
        daily=daily,
        max_concurrent_sessions=max_concurrent_sessions,
        concurrent_days=concurrent_days,
        custom_skill_count=custom_skill_count,
        claude_md_sessions=claude_md_sessions,
        duration_p90_min=duration_p90_min,
        turn_p90=turn_p90,
        thinking_block_count=thinking_block_count,
        thinking_sessions=thinking_sessions,
        background_task_count=background_task_count,
        background_sessions=background_sessions,
        max_parallel_agents=max_parallel_agents,
        parallel_agent_turns=parallel_agent_turns,
        source=src.name,
        capabilities=sorted(src.capabilities),
        unmeasured=unmeasured,
    )
    # 「测到了就不算未测量」：能力集是**保守声明**（拿不准的不声明），但如果这一窗口
    # 里该字段真的收到了非零观测，那它显然测得到——继续标「未测量」反而是把用户已经
    # 做出的行为抹掉。典型场景：Codex 的 MCP 调用形状未验证故不声明 CAP_MCP，
    # 一旦 parser 真收到了 mcp 服务器名，就该按真值呈现。
    # 反向不成立：观测为 0/空**不能**反推「测得到但没做」——那正是 unmeasured 要防的错读。
    return _drop_measured(metrics)


def _drop_measured(metrics: AggregateMetrics) -> AggregateMetrics:
    """把「其实收到了非零观测」的字段从 unmeasured 里摘掉（原地改，返回同一对象）。"""
    def _empty(v) -> bool:
        if isinstance(v, bool):
            return not v
        if isinstance(v, (int, float)):
            return v == 0
        return not v      # dict / list / None / ""
    metrics.unmeasured = [f for f in metrics.unmeasured
                          if _empty(getattr(metrics, f, None))]
    return metrics
