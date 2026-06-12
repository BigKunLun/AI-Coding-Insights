from ai_coding_insights.models import (
    AggregateMetrics,
    OutcomeStats,
    ParsedSession,
    SessionStats,
    UserTurn,
)
from ai_coding_insights.signals import aggregate_metrics, compute_trend


def _session(sid, cwd, tools, models, first, turn_texts):
    turns = [UserTurn(uuid=f"{sid}-u{i}", text=t, timestamp=first)
             for i, t in enumerate(turn_texts)]
    return ParsedSession("f", sid, cwd, "main", turns, tools, models, first, first)


def _build():
    s1 = _session(
        "s1", "/repoA",
        ["Bash", "Agent", "mcp__ctx__q"], ["claude-opus"],
        "2026-06-01T10:00:00Z",
        ["这段代码应该改成幂等的", "继续"],   # 第一条命中 override 锚点
    )
    s2 = _session(
        "s2", "/repoB",
        ["Workflow", "Edit"], ["claude-sonnet"],
        "2026-06-02T09:00:00Z",
        ["把测试跑一下"],
    )
    s3 = _session(
        "s3", "/repoA",
        ["Bash", "Read"], ["claude-opus"],
        "2026-06-02T15:00:00Z",   # 与 s2 同一自然日
        ["看下这个错误"],
    )
    sessions = [s1, s2, s3]
    stats = [
        SessionStats("s1", "/repoA", 2, 0.5, 600.0, s1.tools_used, s1.models_used),
        SessionStats("s2", "/repoB", 1, 0.0, 20 * 3600, s2.tools_used, s2.models_used),  # 跨天污染应被剔除
        SessionStats("s3", "/repoA", 1, 0.0, 1200.0, s3.tools_used, s3.models_used),
    ]
    outcomes = [
        OutcomeStats("s1", "/repoA", 4, 2, 9),
        OutcomeStats("s2", "/repoB", 1, 1, 3),
        OutcomeStats("s3", "/repoA", 2, 0, 1),
    ]
    return sessions, stats, outcomes


def test_aggregate_basic_counts():
    sessions, stats, outcomes = _build()
    m = aggregate_metrics(sessions, stats, outcomes)
    assert isinstance(m, AggregateMetrics)
    assert m.session_count == 3
    assert m.human_input_count == 4          # 2 + 1 + 1
    assert m.avg_turns == 4 / 3
    assert m.commit_count == 7
    assert m.landed_count == 3
    assert m.edit_count == 13
    assert m.landed_ratio == 3 / 7


def test_aggregate_tools_models_subagent():
    sessions, stats, outcomes = _build()
    m = aggregate_metrics(sessions, stats, outcomes)
    # 去重工具种类: Bash, Agent, mcp__ctx__q, Workflow, Edit, Read = 6
    assert m.tool_breadth == 6
    assert m.tool_session_counts["Bash"] == 2
    assert m.subagent_sessions == 1
    assert m.workflow_sessions == 1
    assert m.mcp_sessions == 1
    assert m.model_counts["claude-opus"] == 2
    assert m.model_counts["claude-sonnet"] == 1


def test_aggregate_active_days_and_anchors():
    sessions, stats, outcomes = _build()
    m = aggregate_metrics(sessions, stats, outcomes)
    # 2026-06-01 与 2026-06-02 两天
    assert m.active_days == 2
    assert m.anchor_counts["override"] >= 1


def test_aggregate_duration_median_excludes_outlier():
    sessions, stats, outcomes = _build()
    m = aggregate_metrics(sessions, stats, outcomes)
    # 仅 600s 与 1200s 参与 (20h 被剔除); 中位数 = (600+1200)/2 = 900s = 15min
    assert m.duration_median_min == 15.0


def test_aggregate_project_breakdown():
    sessions, stats, outcomes = _build()
    m = aggregate_metrics(sessions, stats, outcomes)
    a = m.project_breakdown["/repoA"]
    assert a["sessions"] == 2
    assert a["commits"] == 6     # 4 + 2
    assert a["landed"] == 2      # 2 + 0
    assert a["edits"] == 10      # 9 + 1
    b = m.project_breakdown["/repoB"]
    assert b["sessions"] == 1
    assert b["commits"] == 1


def test_aggregate_empty():
    m = aggregate_metrics([], [], [])
    assert m.session_count == 0
    assert m.avg_turns == 0.0
    assert m.duration_median_min is None
    assert m.landed_ratio == 0.0


_PS_SEQ = [0]


def _ps(token_usage=None, first_ts="2026-06-01T10:00:00Z", turn_texts=("x",)):
    _PS_SEQ[0] += 1
    s = _session(f"tok{_PS_SEQ[0]}", "/repoT", ["Bash"], ["claude-opus"],
                 first_ts, list(turn_texts))
    if token_usage is not None:
        s.token_usage = token_usage
    return s


def _st(s):
    return SessionStats(s.session_id, s.cwd, 1, 0.0, 60.0, s.tools_used, s.models_used)


def _oc(s, commit_count=0, landed_count=0, edit_count=0):
    return OutcomeStats(s.session_id, s.cwd, commit_count, landed_count, edit_count)


def test_aggregate_token_usage_merged_across_sessions():
    s1 = _ps(token_usage={"claude-opus-4-8": {"input": 100, "output": 50, "cache_read": 0, "cache_creation": 0}})
    s2 = _ps(token_usage={"claude-opus-4-8": {"input": 10, "output": 5, "cache_read": 20, "cache_creation": 0},
                          "claude-haiku-4-5": {"input": 1, "output": 1, "cache_read": 0, "cache_creation": 0}})
    m = aggregate_metrics([s1, s2], [_st(s1), _st(s2)], [_oc(s1), _oc(s2)])
    assert m.token_usage["claude-opus-4-8"] == {"input": 110, "output": 55, "cache_read": 20, "cache_creation": 0}
    assert m.token_total == 110 + 55 + 20 + 0 + 1 + 1   # 全部四项跨模型总和


def test_compute_trend_splits_by_time_midpoint():
    early = _ps(first_ts="2026-05-01T00:00:00Z")   # 中点 5/16 前
    late = _ps(first_ts="2026-05-30T00:00:00Z")
    late2 = _ps(first_ts="2026-05-31T00:00:00Z")
    sessions = [early, late, late2]
    stats = [_st(x) for x in sessions]
    outcomes = [_oc(early, commit_count=1, landed_count=0),
                _oc(late, commit_count=2, landed_count=2),
                _oc(late2, commit_count=0, landed_count=0)]
    t = compute_trend(sessions, stats, outcomes)
    assert t["first_half"]["sessions"] == 1 and t["second_half"]["sessions"] == 2
    assert t["second_half"]["landed_ratio"] == 1.0


def test_compute_trend_returns_none_when_insufficient():
    assert compute_trend([], [], []) is None
    one = _ps(first_ts="2026-05-01T00:00:00Z")
    assert compute_trend([one], [_st(one)], [_oc(one)]) is None


def test_aggregate_decision_points():
    from ai_coding_insights.signals import compute_stats
    # 2 会话：A 3 turns(1 短) + 2 picks；B 1 turn(1 短) + 0 picks
    sa = _session("sa", "/repoA", [], [], "2026-06-01T10:00:00Z",
                  ["继续", "改成纯函数并补测试", "再跑一遍全量测试"])
    sa.option_pick_count = 2
    sb = _session("sb", "/repoB", [], [], "2026-06-02T09:00:00Z", ["ok"])
    sta = compute_stats(sa, short_turn_max_chars=6)
    stb = compute_stats(sb, short_turn_max_chars=6)
    oa = OutcomeStats(session_id="sa", cwd="/repoA",
                      commit_count=0, landed_count=0, edit_count=0)
    ob = OutcomeStats(session_id="sb", cwd="/repoB",
                      commit_count=0, landed_count=0, edit_count=0)
    m = aggregate_metrics([sa, sb], [sta, stb], [oa, ob])
    assert m.short_turn_count == 2
    assert m.option_pick_count == 2
    assert m.human_input_count == 4
    assert m.decision_point_count == 6      # 4 turns + 2 picks


def test_aggregate_includes_trend():
    early = _ps(first_ts="2026-05-01T00:00:00Z")
    late = _ps(first_ts="2026-05-31T00:00:00Z")
    sessions = [early, late]
    m = aggregate_metrics(sessions, [_st(early), _st(late)],
                          [_oc(early), _oc(late)])
    assert m.trend is not None
    assert m.trend["first_half"]["sessions"] == 1
