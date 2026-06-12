from ai_coding_insights.models import ParsedSession, UserTurn
from ai_coding_insights.signals import compute_stats

def _session(texts, first="2026-06-01T00:00:00Z", last="2026-06-01T00:10:00Z"):
    turns = [UserTurn(uuid=f"u{i}", text=t, timestamp=first) for i, t in enumerate(texts)]
    return ParsedSession("f","s1","/repo","main",turns,[],[],first,last)

def test_short_turn_ratio_and_duration():
    s = _session(["继续", "ok", "把这个函数重构成幂等的并补单测"])
    st = compute_stats(s, short_turn_max_chars=6)
    assert st.turn_count == 3
    assert round(st.short_turn_ratio, 3) == round(2/3, 3)   # 继续/ok 算极短
    assert st.duration_seconds == 600.0

def test_empty_session_no_div_by_zero():
    st = compute_stats(_session([]), short_turn_max_chars=6)
    assert st.turn_count == 0 and st.short_turn_ratio == 0.0

def test_short_turn_count_exact():
    s = _session(["继续", "ok", "把这个函数改成纯函数并补测试"])
    st = compute_stats(s, short_turn_max_chars=6)
    assert st.short_turn_count == 2
    assert st.turn_count == 3
