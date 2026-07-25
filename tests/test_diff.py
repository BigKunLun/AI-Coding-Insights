from ai_coding_insights.snapshot import diff_metrics, _CORE_KEYS

CURRENT = {
    "landed_ratio": 0.8,
    "commit_count": 46,
    "landed_count": 37,
    "git_landed_count": 41,
    "git_commit_total": 52,
    "dropped_count": 9,
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
    "decision_point_count": 620,
    "plan_mode_sessions": 6,
    "turn_p90": 18,
    "custom_skill_count": 4,
    "background_sessions": 3,
    "max_parallel_agents": 3,
}
PREVIOUS = {
    "landed_ratio": 0.75,
    "commit_count": 40,
    "landed_count": 37,       # 相等 → "→"
    "git_landed_count": 35,
    "git_commit_total": 48,
    "dropped_count": 3,
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
    "decision_point_count": 500,
    "plan_mode_sessions": 4,
    "turn_p90": 15,
    "custom_skill_count": 4,  # 相等 → "→"
    "background_sessions": 1,
    "max_parallel_agents": 2,
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


# ---------- 快照扩键的向后兼容（旧快照没有新键）----------

_NEW_SNAPSHOT_KEYS = ("decision_point_count", "plan_mode_sessions", "turn_p90",
                      "custom_skill_count", "background_sessions", "max_parallel_agents")


def test_diff_新扩键在旧快照里缺失时不出伪同比():
    """旧快照（扩键之前落盘的）根本没有这些键 → 必须 no_base，不能把缺失当 0 算涨幅。"""
    old = {k: v for k, v in PREVIOUS.items() if k not in _NEW_SNAPSHOT_KEYS}
    result = diff_metrics(CURRENT, old)
    for k in _NEW_SNAPSHOT_KEYS:
        assert result[k]["no_base"] is True, k
        assert result[k]["delta"] is None and result[k]["arrow"] is None, k
        assert result[k]["prev"] is None, k
    # 同屏其余老键照常出同比，缺新键不牵连老键
    assert result["session_count"]["arrow"] == "↑"


def test_diff_新扩键两边齐备时正常出同比():
    result = diff_metrics(CURRENT, PREVIOUS)
    assert result["turn_p90"]["delta"] == 3 and result["turn_p90"]["arrow"] == "↑"
    assert result["custom_skill_count"]["arrow"] == "→"


# ---------- 脏快照不炸报告（diff_metrics 跑在渲染之前，渲染层的降级救不了这条路径）----------

def test_diff_脏值不崩且退化为无基线():
    """快照被人手改坏 / 半写坏时，值可能是字符串、bool、NaN、Inf、超大整数。

    diff_metrics 在 render-profile 里跑在渲染**之前**，所以渲染层的降级兜不住它——
    这里一崩，整张报告拿不到，前面所有 subagent 的工作作废。脏值一律退 no_base
    （不出假箭头），now/prev 原样透出交给渲染层显示。
    """
    for bad in ["脏", True, float("nan"), float("inf"), 10 ** 400, [1], {"a": 1}]:
        cur = dict(CURRENT)
        cur["edit_count"] = bad
        result = diff_metrics(cur, PREVIOUS)
        assert result["edit_count"]["no_base"] is True, bad
        assert result["edit_count"]["delta"] is None, bad
        assert result["edit_count"]["arrow"] is None, bad
        # 同屏其余干净键照常出同比，一个脏键不牵连全表
        assert result["session_count"]["arrow"] == "↑", bad


def test_diff_基线侧脏值同样退无基线():
    prev = dict(PREVIOUS)
    prev["edit_count"] = "脏"
    result = diff_metrics(CURRENT, prev)
    assert result["edit_count"]["no_base"] is True
    assert result["edit_count"]["delta"] is None


def test_diff_基线整体不是对象时按无可比基线处理():
    """快照 metrics 字段被写成字符串/列表等非对象 → 等同于没有可用基线，不是崩。"""
    for bad in ["脏", [1, 2], 42]:
        assert diff_metrics(CURRENT, bad) == {"baseline": True}, bad


def test_diff_当前侧不是对象时全表无基线():
    result = diff_metrics("脏", PREVIOUS)
    assert result["session_count"]["no_base"] is True
    assert result["session_count"]["now"] is None
