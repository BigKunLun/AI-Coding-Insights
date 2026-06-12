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
    # L4 够但落地率不够引领 → 精通期，gaps 指出落地率差距
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
    # 百分数形态（和≈100）应归一化；L4 实际 0.20 < 0.35，l34=0.60 < 0.70 → 精通期
    r = decide_stage({"L1": 10, "L2": 30, "L3": 40, "L4": 20}, 28, 0.69)
    assert r["name"] != "引领期"
    assert r["name"] == "精通期"


def test_float_boundary_sum_inclusive():
    # L3+L4 浮点和恰 0.55，round 后应达标精通期
    r = decide_stage({"L3": 0.08, "L4": 0.47}, 12, 0.2)
    assert r["name"] == "精通期"


def test_defensive_none_inputs():
    r = decide_stage(None, None, None)
    assert r["name"] == "探索期"


def test_assemble_posture_normal():
    # 100 决策点：10 短输入、5 picks，剩余 0.85 按 l4_share=0.4 切
    pd = assemble_posture(100, 10, 5, 0.4)
    assert pd == {"L1": 0.1, "L2": 0.05,
                  "L3": round(0.85 * 0.6, 10), "L4": round(0.85 * 0.4, 10)}
    assert abs(sum(pd.values()) - 1.0) < 1e-9


def test_assemble_posture_zero_decision_points():
    assert assemble_posture(0, 0, 0, 0.5) == {"L1": 0.0, "L2": 0.0, "L3": 0.0, "L4": 0.0}


def test_assemble_posture_share_endpoints_and_clamp():
    assert assemble_posture(10, 0, 0, 0.0)["L4"] == 0.0
    assert assemble_posture(10, 0, 0, 1.0)["L3"] == 0.0
    assert assemble_posture(10, 0, 0, 1.7)["L4"] == 1.0     # share clamp 到 1
    assert assemble_posture(10, 0, 0, None)["L4"] == 0.0    # 缺失按 0


def test_assemble_posture_defensive_rest_floor():
    # 防御：分子异常超过分母时剩余质量钳 0，不出负数
    pd = assemble_posture(10, 8, 8, 0.5)
    assert pd["L3"] == 0.0 and pd["L4"] == 0.0
    assert all(v >= 0 for v in pd.values())
