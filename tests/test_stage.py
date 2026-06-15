from ai_coding_insights.stage import (
    assemble_posture, decide_stage, StageThresholds,
    diagnose_posture, PostureBands,
)


def _m(**kw):
    """构造 aggregate 风格 metrics dict，缺省全 0。"""
    base = dict(active_days=0, human_input_count=0, tool_breadth=0,
                thinking_sessions=0, subagent_sessions=0, plan_mode_sessions=0,
                max_parallel_agents=0, background_sessions=0, custom_skill_count=0,
                git_landed_count=0)
    base.update(kw)
    return base


def test_low_usage_caps_at_explorer_despite_ratio():
    r = decide_stage(_m(active_days=2, human_input_count=20, tool_breadth=12))
    assert r["stage"] == 1 and r["name"] == "探索期"


def test_advancing_stage():
    r = decide_stage(_m(active_days=6, human_input_count=120, tool_breadth=7))
    assert r["stage"] == 2 and r["name"] == "进阶期"
    assert r["criteria"] and all(c["key"] in r["values"] for c in r["criteria"])


def test_master_stage_needs_depth_signal():
    miss = decide_stage(_m(active_days=14, human_input_count=400, tool_breadth=11))
    assert miss["name"] == "进阶期"
    assert any(g["key"] == "depth_signal" for g in miss["gaps"])
    ok = decide_stage(_m(active_days=14, human_input_count=400, tool_breadth=11,
                         thinking_sessions=1))
    assert ok["name"] == "精通期"


def test_leader_stage():
    r = decide_stage(_m(active_days=22, human_input_count=900, tool_breadth=16,
                        thinking_sessions=3, max_parallel_agents=2, git_landed_count=8))
    assert r["stage"] == 4 and r["name"] == "引领期"
    assert r["gaps"] == []


def test_leader_blocked_by_landed():
    r = decide_stage(_m(active_days=22, human_input_count=900, tool_breadth=16,
                        thinking_sessions=3, max_parallel_agents=2, git_landed_count=1))
    assert r["name"] == "精通期"
    assert any(g["key"] == "git_landed_count" for g in r["gaps"])


def test_boundary_inclusive():
    r = decide_stage(_m(active_days=5, human_input_count=80, tool_breadth=6))
    assert r["name"] == "进阶期"


def test_thresholds_overridable():
    strict = StageThresholds(s2_active_days=10)
    r = decide_stage(_m(active_days=6, human_input_count=120, tool_breadth=7),
                     thresholds=strict)
    assert r["name"] == "探索期"


def test_decide_stage_defensive_none():
    r = decide_stage(None)
    assert r["name"] == "探索期"


def test_advanced_orchestration_background_only_counts_one():
    # 仅后台会话达标 → 高阶编排信号计 1（够引领期的 s4_advanced=1 闸门那项）
    r = decide_stage(_m(active_days=22, human_input_count=900, tool_breadth=16,
                        thinking_sessions=3, background_sessions=2, git_landed_count=8))
    assert r["values"]["advanced_orchestration"] == 1
    assert r["name"] == "引领期"


def test_advanced_orchestration_parallel_boundary_inclusive():
    # max_parallel_agents=1 不计入；=2（默认 s4_parallel_min）计入，边界含等号
    below = decide_stage(_m(max_parallel_agents=1))
    assert below["values"]["advanced_orchestration"] == 0
    at = decide_stage(_m(max_parallel_agents=2))
    assert at["values"]["advanced_orchestration"] == 1


def test_synthetic_signal_thresholds_overridable():
    # 把后台门抬到 3：background_sessions=2 不再计入
    t = StageThresholds(s4_background_min=3)
    r = decide_stage(_m(background_sessions=2), thresholds=t)
    assert r["values"]["advanced_orchestration"] == 0


def test_decide_stage_defensive_per_key_coercion():
    # 杂值逐键强制：字符串数字按数值、浮点 round、None 按 0，均不抛错
    r = decide_stage(_m(active_days=6, human_input_count="120", tool_breadth=7.0,
                        thinking_sessions=None))
    assert r["values"]["human_input_count"] == 120
    assert r["values"]["tool_breadth"] == 7
    assert r["name"] == "进阶期"


def test_assemble_posture_from_counts():
    # LLM 计数 95 条 + 5 个 AskUserQuestion 答题 → 分母 100，picks 并入 L2
    pd = assemble_posture({"L1": 50, "L2": 10, "L3": 25, "L4": 10}, option_pick_count=5)
    assert pd == {"L1": 0.5, "L2": 0.15, "L3": 0.25, "L4": 0.1}
    assert abs(sum(pd.values()) - 1.0) < 1e-9


def test_assemble_posture_zero_inputs():
    zero = {"L1": 0.0, "L2": 0.0, "L3": 0.0, "L4": 0.0}
    assert assemble_posture({}, 0) == zero
    assert assemble_posture(None, None) == zero


def test_assemble_posture_picks_only_all_l2():
    pd = assemble_posture({"L1": 0, "L2": 0, "L3": 0, "L4": 0}, 4)
    assert pd == {"L1": 0.0, "L2": 1.0, "L3": 0.0, "L4": 0.0}


def test_assemble_posture_defensive_bad_values():
    # 负数/bool/非整数按 0 计；picks 非法按 0
    pd = assemble_posture({"L1": -3, "L2": True, "L3": "x", "L4": 10}, "bad")
    assert pd == {"L1": 0.0, "L2": 0.0, "L3": 0.0, "L4": 1.0}


def test_posture_insufficient_sample():
    r = diagnose_posture({"L3": 0.5, "L4": 0.5}, decision_point_count=10)
    assert r["state"] == "样本不足"


def test_posture_adversarial_l4_over_ceiling():
    r = diagnose_posture({"L1": 0.2, "L2": 0.2, "L3": 0.35, "L4": 0.25},
                         decision_point_count=100)
    assert r["state"] == "偏对抗"


def test_posture_healthy():
    r = diagnose_posture({"L1": 0.2, "L2": 0.25, "L3": 0.4, "L4": 0.15},
                         decision_point_count=100)
    assert r["state"] == "健康"


def test_posture_dependent_low_guidance():
    r = diagnose_posture({"L1": 0.5, "L2": 0.35, "L3": 0.1, "L4": 0.05},
                         decision_point_count=100)
    assert r["state"] == "偏依赖"


def test_posture_handsoff_with_depth_evidence():
    r = diagnose_posture({"L1": 0.5, "L2": 0.35, "L3": 0.1, "L4": 0.05},
                         decision_point_count=100, plan_mode_sessions=3)
    assert r["state"] == "放手为主"


def test_posture_handsoff_with_thinking_evidence():
    r = diagnose_posture({"L1": 0.5, "L2": 0.35, "L3": 0.1, "L4": 0.05},
                         decision_point_count=100, thinking_sessions=2)
    assert r["state"] == "放手为主"


def test_posture_healthy_conservative_fallback():
    # L3+L4=0.30 达引导下限，L4=0.10 在健康带，但 L3=0.20<0.25 → 走保守兜底，仍判健康
    r = diagnose_posture({"L1": 0.4, "L2": 0.3, "L3": 0.2, "L4": 0.1},
                         decision_point_count=100)
    assert r["state"] == "健康"
    assert "偏保守" not in r["reason"]   # 不再甩锅 L4


def test_posture_l4_ceiling_boundary_inclusive():
    r = diagnose_posture({"L1": 0.2, "L2": 0.25, "L3": 0.35, "L4": 0.2},
                         decision_point_count=100)
    assert r["state"] == "健康"


def test_posture_bands_overridable():
    bands = PostureBands(l4_healthy_ceiling=0.10)
    r = diagnose_posture({"L1": 0.2, "L2": 0.25, "L3": 0.4, "L4": 0.15},
                         decision_point_count=100, bands=bands)
    assert r["state"] == "偏对抗"
