from ai_coding_insights.models import RemoteIdentity, RemoteRule, UserTurn


def test_user_turn_char_len():
    t = UserTurn(uuid="u1", text="继续", timestamp="2026-06-01T00:00:00Z")
    assert t.char_len == 2


def test_remote_rule_org_optional():
    assert RemoteRule(host="git.example.com").org is None
    assert RemoteRule(host="github.com", org="example-org").org == "example-org"


from ai_coding_insights.models import CommitRef, ParsedSession, OutcomeStats

def test_commit_ref():
    c = CommitRef(sha="e037656", kind="committed")
    assert c.sha == "e037656" and c.kind == "committed"

def test_parsed_session_new_fields_default():
    s = ParsedSession("f","s","/r",None,[],[],[],None,None)
    assert s.commits == [] and s.edit_count == 0

def test_outcome_landed_ratio():
    o = OutcomeStats(session_id="s", cwd="/r", commit_count=4, landed_count=3, edit_count=10)
    assert o.landed_ratio == 0.75
    assert OutcomeStats("s","/r",0,0,0).landed_ratio == 0.0


def test_repo_outcome_landed_total():
    from ai_coding_insights.models import RepoOutcome
    r = RepoOutcome(landed_count=3, total_count=5)
    assert r.landed_count == 3 and r.total_count == 5


def _agg(**kw):
    from ai_coding_insights.models import AggregateMetrics
    base = dict(session_count=1, human_input_count=1, active_days=1, avg_turns=1.0,
                tool_breadth=1, tool_session_counts={}, subagent_sessions=0,
                workflow_sessions=0, mcp_sessions=0, model_counts={},
                commit_count=0, landed_count=0, edit_count=0,
                duration_median_min=None, project_breakdown={}, anchor_counts={})
    base.update(kw)
    return AggregateMetrics(**base)


def test_aggregate_landed_ratio_same_caliber():
    # 同口径落地率：文件重叠落地 / 窗口同仓本人提交总数。
    assert abs(_agg(git_landed_count=3, git_commit_total=4).landed_ratio - 0.75) < 1e-9
    assert _agg(git_landed_count=0, git_commit_total=0).landed_ratio == 0.0


def test_aggregate_dropped_count_independent():
    # dropped_count 仍是独立观测信号（不再喂 landed_ratio）。
    m = _agg(commit_count=10, landed_count=7)
    assert m.dropped_count == 3
