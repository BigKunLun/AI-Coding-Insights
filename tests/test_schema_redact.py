from ai_coding_insights.config import load_config
from ai_coding_insights.profile_schema import validate_profile


def _ok():
    return {"breadth": {"summary": "广度高", "tools": ["Bash"]},
            "depth": {"summary": "多轮打磨"},
            "outcome": {"summary": "落地多", "landed": 18, "total": 23},
            "evidence": [{"pointer": "file#u1", "behavior": "纠正了一处实现方案"}]}


def test_business_term_in_outcome_summary_flagged():
    p = _ok()
    p["outcome"]["summary"] = "围绕虚拟手环落地多"
    errs = validate_profile(p, business_terms=["虚拟手环", "EVAL"])
    assert any("虚拟手环" in e for e in errs)


def test_business_term_in_evidence_behavior_flagged():
    p = _ok()
    p["evidence"] = [{"pointer": "file#u1", "behavior": "推进 EVAL 流程"}]
    errs = validate_profile(p, business_terms=["虚拟手环", "EVAL"])
    assert any("EVAL" in e for e in errs)


def test_no_business_terms_keeps_old_behavior():
    p = _ok()
    p["outcome"]["summary"] = "围绕虚拟手环落地多"
    # 不传 business_terms：保持旧行为，不因业务词报错，结构合法 → []
    assert validate_profile(p) == []


def test_load_config_reads_business_terms(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('business_terms = ["x"]\n')
    cfg = load_config(p)
    assert cfg.business_terms == ["x"]


def test_load_config_business_terms_defaults_empty(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('lookback_days = 30\n')
    cfg = load_config(p)
    assert cfg.business_terms == []


# ---- v5 结构化字段 (headline / points / metrics) + frictions ----

def _ok_v5():
    """结构化合法 profile：depth 含 headline+points+metrics，顶层含合法 frictions。"""
    return {"breadth": {"headline": "工具面铺得开",
                        "points": ["覆盖多类工具", "切换自如"],
                        "metrics": [{"label": "工具数", "value": "7"}],
                        "tools": ["Bash"]},
            "depth": {"headline": "多轮打磨到位",
                      "points": ["反复迭代实现", "主动验证假设"],
                      "metrics": [{"label": "平均轮次", "value": "4.2"}]},
            "outcome": {"headline": "落地率高",
                        "points": ["大多数方案落地"],
                        "metrics": [{"label": "落地", "value": "18/23"}],
                        "landed": 18, "total": 23},
            "evidence": [{"pointer": "file#u1", "behavior": "纠正了一处实现方案"}],
            "frictions": [{"observation": "上下文偶尔丢失",
                           "pointers": [],
                           "suggestion": "拆小任务分批推进"}]}


def test_v5_structured_profile_no_false_positive():
    # 结构化合法 profile，不传 business_terms → 不因新字段报错
    assert validate_profile(_ok_v5()) == []


def test_backward_compat_old_summary_only():
    # 仅含旧 summary（无 headline/points/frictions）→ 校验通过，不报缺字段
    assert validate_profile(_ok()) == []


def test_business_term_in_frictions_suggestion_flagged():
    p = _ok_v5()
    p["frictions"][0]["suggestion"] = "建议先做虚拟手环的拆解"
    errs = validate_profile(p, business_terms=["虚拟手环"])
    hits = [e for e in errs if "虚拟手环" in e]
    assert hits
    assert any("frictions" in e and "suggestion" in e for e in hits)


def test_business_term_in_frictions_observation_flagged():
    p = _ok_v5()
    p["frictions"][0]["observation"] = "在虚拟手环模块反复绕"
    errs = validate_profile(p, business_terms=["虚拟手环"])
    assert any("虚拟手环" in e and "frictions" in e and "observation" in e for e in errs)


def test_business_term_in_breadth_headline_flagged():
    p = _ok_v5()
    p["breadth"]["headline"] = "围绕虚拟手环铺工具"
    errs = validate_profile(p, business_terms=["虚拟手环"])
    assert any("虚拟手环" in e and "breadth.headline" in e for e in errs)


def test_business_term_in_depth_points_flagged():
    p = _ok_v5()
    p["depth"]["points"][1] = "深入虚拟手环细节"
    errs = validate_profile(p, business_terms=["虚拟手环"])
    assert any("虚拟手环" in e and "depth.points[1]" in e for e in errs)


def test_points_not_list_is_type_error():
    p = _ok_v5()
    p["depth"]["points"] = "不该是字符串"
    errs = validate_profile(p)
    assert any("points" in e for e in errs)


def test_metrics_item_missing_value_is_type_error():
    p = _ok_v5()
    p["depth"]["metrics"] = [{"label": "轮次"}]  # 缺 value
    errs = validate_profile(p)
    assert any("metrics" in e for e in errs)


def test_frictions_must_be_list():
    p = _ok_v5()
    p["frictions"] = {"observation": "x", "suggestion": "y"}  # 不是 list
    errs = validate_profile(p)
    assert any("frictions" in e for e in errs)


def test_frictions_item_missing_suggestion_is_type_error():
    p = _ok_v5()
    p["frictions"] = [{"observation": "只有观察"}]  # 缺 suggestion
    errs = validate_profile(p)
    assert any("frictions" in e for e in errs)


def test_business_term_dedup_across_structured_fields():
    p = _ok_v5()
    p["breadth"]["headline"] = "虚拟手环 A"
    p["depth"]["points"][0] = "虚拟手环 B"
    p["frictions"][0]["suggestion"] = "虚拟手环 C"
    errs = validate_profile(p, business_terms=["虚拟手环"])
    # 去重：同一词只报一次
    assert sum(1 for e in errs if "虚拟手环" in e) == 1


# ---- 兜底网覆盖渲染会经过的全部自由文本字段 ----

def test_business_term_in_metrics_label_flagged():
    p = _ok_v5()
    p["depth"]["metrics"] = [{"label": "虚拟手环轮次", "value": "4"}]
    errs = validate_profile(p, business_terms=["虚拟手环"])
    assert any("虚拟手环" in e for e in errs)


def test_business_term_in_evidence_pointer_flagged():
    # 指针是文件路径，含业务词的文件名同属泄露面（铁律明文禁止）
    p = _ok_v5()
    p["evidence"] = [{"pointer": "/Users/x/虚拟手环/s.jsonl#u1", "behavior": "纠错"}]
    errs = validate_profile(p, business_terms=["虚拟手环"])
    assert any("虚拟手环" in e for e in errs)


def test_business_term_in_breadth_tools_flagged():
    p = _ok_v5()
    p["breadth"]["tools"] = ["虚拟手环数据工具"]
    errs = validate_profile(p, business_terms=["虚拟手环"])
    assert any("虚拟手环" in e for e in errs)
