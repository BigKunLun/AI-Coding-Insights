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


def test_trend_first_half_unobservable_landed_ratio_is_none():
    # 前半提交不可观测（commits=0）→ landed_ratio 应为 None（不可观测），不退化 0.0，
    # 否则报告把「前半测不到」渲染成「落地率 0%→72% 提升」假趋势。
    from ai_coding_insights.signals import compute_trend
    from ai_coding_insights.models import ParsedSession, UserTurn, OutcomeStats

    def _sess(first):
        return ParsedSession("f", "s", "/r", "main",
                             [UserTurn(uuid="u", text="x", timestamp=first)],
                             [], [], first, first)
    s1, s2 = _sess("2026-06-01T00:00:00Z"), _sess("2026-06-10T00:00:00Z")
    st1, st2 = compute_stats(s1, 6), compute_stats(s2, 6)
    o1 = OutcomeStats("s", "/r", 0, 0, 0)     # 前半：无提交观测
    o2 = OutcomeStats("s", "/r", 5, 3, 4)     # 后半：5 提交 3 落地
    tr = compute_trend([s1, s2], [st1, st2], [o1, o2])
    assert tr["first_half"]["commits"] == 0
    assert tr["first_half"]["landed_ratio"] is None       # 不可观测，非 0.0
    assert round(tr["second_half"]["landed_ratio"], 4) == 0.6
