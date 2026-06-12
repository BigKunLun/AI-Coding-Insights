from ai_coding_insights.stage import assemble_posture, decide_stage


def _pd(l1=0.0, l2=0.0, l3=0.0, l4=0.0):
    return {"L1": l1, "L2": l2, "L3": l3, "L4": l4}


def test_leader_stage():
    r = decide_stage(_pd(l3=0.40, l4=0.45), tool_breadth=28, landed_ratio=0.69)
    assert r["stage"] == 4 and r["name"] == "引领期"
    assert r["gaps"] == []          # 已是最高阶段
    assert r["criteria"]            # 判定依据非空，报告要印
    # 结构化判据：渲染层按 key 取实际值，key 必须落在 values 里
    assert all(c["key"] in r["values"] for c in r["criteria"])


def test_master_stage_missing_landed():
    # L4/L3+L4/工具都够引领，但落地率不够 → 精通期，gaps 指出落地率差距
    r = decide_stage(_pd(l3=0.30, l4=0.40), tool_breadth=20, landed_ratio=0.3)
    assert r["name"] == "精通期"
    assert any("落地率" in g["desc"] for g in r["gaps"])
    assert any(g["key"] == "landed_ratio" for g in r["gaps"])


def test_competent_stage():
    r = decide_stage(_pd(l3=0.30, l4=0.10), tool_breadth=8, landed_ratio=0.2)
    assert r["name"] == "进阶期"


def test_explorer_stage():
    r = decide_stage(_pd(l1=0.6, l2=0.3, l3=0.1), tool_breadth=3, landed_ratio=0.0)
    assert r["name"] == "探索期"
    assert any("L3" in g["desc"] or "引导" in g["desc"] for g in r["gaps"])


def test_boundary_exact_thresholds_inclusive():
    # 阈值取等号应落在高阶段（>= 语义）
    r = decide_stage(_pd(l3=0.35, l4=0.35), tool_breadth=15, landed_ratio=0.5)
    assert r["name"] == "引领期"


def test_percent_form_normalized():
    # 百分数形态（和≈100）应归一化；lr=0.3 不够引领 → 精通期
    r = decide_stage({"L1": 10, "L2": 30, "L3": 40, "L4": 20}, 28, 0.3)
    assert r["name"] != "引领期"
    assert r["name"] == "精通期"


def test_float_boundary_sum_inclusive():
    # L3+L4 浮点和恰 0.35（精通阈值），round 后应达标
    r = decide_stage({"L3": 0.08, "L4": 0.27}, 12, 0.2)
    assert r["name"] == "精通期"


def test_v2_thresholds_plain_directive_user_lands_competent():
    # v2 口径典型画像：放行/选择为主 + 两成引导 → 进阶期（旧阈值下会掉探索期）
    r = decide_stage(_pd(l1=0.45, l2=0.30, l3=0.20, l4=0.05), tool_breadth=8,
                     landed_ratio=0.4)
    assert r["name"] == "进阶期"


def test_defensive_none_inputs():
    r = decide_stage(None, None, None)
    assert r["name"] == "探索期"


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
