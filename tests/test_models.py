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
