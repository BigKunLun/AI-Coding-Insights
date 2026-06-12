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


def test_repo_outcome_fields():
    from ai_coding_insights.models import RepoOutcome
    r = RepoOutcome(landed_count=3, outside_count=2)
    assert r.landed_count == 3 and r.outside_count == 2


def _agg(**kw):
    from ai_coding_insights.models import AggregateMetrics
    base = dict(session_count=1, human_input_count=1, active_days=1, avg_turns=1.0,
                tool_breadth=1, tool_session_counts={}, subagent_sessions=0,
                workflow_sessions=0, mcp_sessions=0, model_counts={},
                commit_count=0, landed_count=0, edit_count=0,
                duration_median_min=None, project_breakdown={}, anchor_counts={})
    base.update(kw)
    return AggregateMetrics(**base)


def test_aggregate_landed_ratio_git_anchor():
    # 双源可观测：git 落地 8，transcript 观测 10 中 7 存活 → 丢弃 3 → 8/11
    m = _agg(commit_count=10, landed_count=7, git_landed_count=8, git_outside_count=2)
    assert m.dropped_count == 3
    assert abs(m.landed_ratio - 8 / 11) < 1e-9


def test_aggregate_landed_ratio_transcript_blind():
    # transcript 不可观测（旧版 CC）：丢弃未知=0，git 有落地 → 1.0
    m = _agg(commit_count=0, landed_count=0, git_landed_count=5)
    assert m.dropped_count == 0
    assert m.landed_ratio == 1.0


def test_aggregate_landed_ratio_no_evidence():
    m = _agg()
    assert m.landed_ratio == 0.0
