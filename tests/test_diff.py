from ai_coding_insights.snapshot import diff_metrics, _CORE_KEYS

CURRENT = {
    "landed_ratio": 0.8,
    "commit_count": 46,
    "landed_count": 37,
    "edit_count": 886,
    "session_count": 107,
    "human_input_count": 588,
    "tool_breadth": 14,
    "active_days": 20,
    "token_total": 1_200_000,
    "subagent_sessions": 12,
    "workflow_sessions": 3,
    "mcp_sessions": 5,
    "duration_median_min": 42.0,
    "plan_mode_sessions": 8,
    "max_concurrent_sessions": 3,
    "plan_mode_count": 15,
}
PREVIOUS = {
    "landed_ratio": 0.75,
    "commit_count": 40,
    "landed_count": 37,       # 相等 → "→"
    "edit_count": 900,        # 下降 → "↓"
    "session_count": 100,
    "human_input_count": 500,
    "tool_breadth": 14,       # 相等 → "→"
    "active_days": 18,
    "token_total": 1_000_000,
    "subagent_sessions": 8,
    "workflow_sessions": 3,   # 相等 → "→"
    "mcp_sessions": 6,        # 下降 → "↓"
    "duration_median_min": 40.0,
    "plan_mode_sessions": 5,
    "max_concurrent_sessions": 2,
    "plan_mode_count": 10,
}


def test_diff_up_delta_and_arrow():
    result = diff_metrics(CURRENT, PREVIOUS)
    assert result["landed_ratio"]["arrow"] == "↑"
    assert abs(result["landed_ratio"]["delta"] - 0.05) < 1e-9
    assert result["commit_count"]["delta"] == 6
    assert result["commit_count"]["arrow"] == "↑"
    assert result["commit_count"]["now"] == 46
    assert result["commit_count"]["prev"] == 40


def test_diff_down_arrow():
    result = diff_metrics(CURRENT, PREVIOUS)
    assert result["edit_count"]["delta"] == -14
    assert result["edit_count"]["arrow"] == "↓"


def test_diff_equal_arrow():
    result = diff_metrics(CURRENT, PREVIOUS)
    assert result["landed_count"]["delta"] == 0
    assert result["landed_count"]["arrow"] == "→"
    assert result["tool_breadth"]["arrow"] == "→"


def test_diff_baseline_when_previous_none():
    assert diff_metrics(CURRENT, None) == {"baseline": True}


def test_diff_no_baseline_key_when_previous_present():
    result = diff_metrics(CURRENT, PREVIOUS)
    assert "baseline" not in result


def test_diff_all_core_keys_present():
    # 两边都齐全 → 每个核心键都是正常箭头形态，无 no_base 标记
    result = diff_metrics(CURRENT, PREVIOUS)
    for k in _CORE_KEYS:
        assert set(result[k]) == {"now", "prev", "delta", "arrow"}
        assert "no_base" not in result[k]


def test_diff_empty_previous_no_fake_full_arrow():
    """根治满值：previous 是空快照 dict（核心键全缺）→ 每键 no_base，绝不出 ↑满值。"""
    cur = dict(CURRENT)
    cur["session_count"] = 108
    result = diff_metrics(cur, {})
    for k in _CORE_KEYS:
        assert result[k]["no_base"] is True
        assert result[k]["arrow"] is None
        assert result[k]["delta"] is None
        assert result[k]["prev"] is None
    # 关键反断言：session_count 绝不能渲染成 108 的假上涨
    assert result["session_count"]["now"] == 108
    assert result["session_count"]["arrow"] is None
    assert result["session_count"]["delta"] is None


def test_diff_partial_baseline_mixes_normal_and_no_base():
    """部分基线：previous 只含 landed_ratio → 该键正常箭头，其余键 no_base。"""
    cur = dict(CURRENT)
    prev = {"landed_ratio": 0.75}
    result = diff_metrics(cur, prev)
    # 有基线的键：正常箭头
    assert "no_base" not in result["landed_ratio"]
    assert result["landed_ratio"]["arrow"] == "↑"
    assert abs(result["landed_ratio"]["delta"] - 0.05) < 1e-9
    # 缺基线的键：no_base、无假箭头
    for k in ("session_count", "commit_count", "active_days"):
        assert result[k]["no_base"] is True
        assert result[k]["arrow"] is None
        assert result[k]["delta"] is None


def test_diff_none_value_in_current_is_no_base():
    """current 某键为 None（空 metrics）→ 也标 no_base，不计算 delta。"""
    cur = dict(CURRENT)
    cur["commit_count"] = None
    result = diff_metrics(cur, PREVIOUS)
    assert result["commit_count"]["no_base"] is True
    assert result["commit_count"]["arrow"] is None
    assert result["commit_count"]["delta"] is None
    assert result["commit_count"]["now"] is None
