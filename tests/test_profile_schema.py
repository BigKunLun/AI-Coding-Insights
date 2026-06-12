from ai_coding_insights.profile_schema import validate_profile


def _ok():
    return {"l4_share": 0.2,
            "breadth": {"summary": "广度高", "tools": ["Bash"]},
            "depth": {"summary": "多轮打磨"},
            "outcome": {"summary": "落地多", "landed": 18, "total": 23},
            "evidence": [{"pointer": "file#u1", "behavior": "纠正了一处实现方案"}]}


def test_valid_profile_passes():
    assert validate_profile(_ok()) == []


def test_l4_share_required_and_bounded():
    assert validate_profile(_ok()) == []      # fixture 已切到 l4_share

    p2 = _ok(); del p2["l4_share"]
    assert any("l4_share" in e for e in validate_profile(p2))

    p3 = _ok(); p3["l4_share"] = 1.2
    assert any("0-1" in e for e in validate_profile(p3))

    p4 = _ok(); p4["l4_share"] = True         # bool 不是合法数字
    assert any("l4_share" in e for e in validate_profile(p4))

    p5 = _ok(); p5["l4_share"] = 0
    assert validate_profile(p5) == []         # 边界 0 含
    p6 = _ok(); p6["l4_share"] = 1
    assert validate_profile(p6) == []         # 边界 1 含


def test_posture_distribution_deprecated():
    p = _ok()
    p["posture_distribution"] = {"L1": 0.1, "L2": 0.1, "L3": 0.4, "L4": 0.4}
    assert any("已废弃" in e for e in validate_profile(p))


def test_evidence_required():
    bad = _ok(); bad["evidence"] = []
    assert validate_profile(bad)
    bad2 = _ok(); bad2["evidence"] = [{"pointer": "x"}]   # 缺 behavior
    assert validate_profile(bad2)


def test_missing_dimension():
    bad = _ok(); del bad["depth"]
    assert any("depth" in e for e in validate_profile(bad))


def test_highlights_optional_and_validated():
    p = _ok()
    assert validate_profile(p) == []                         # 无 highlights 不报错
    p["highlights"] = [{"pointer": "f#u", "behavior": "推翻一处实现并给出更优约束"}]
    assert validate_profile(p) == []
    p["highlights"] = [{"pointer": "f#u"}]    # 缺 behavior
    assert any("highlights" in e for e in validate_profile(p))


def test_highlights_behavior_scanned_for_business_terms():
    p = _ok()
    p["highlights"] = [{"pointer": "f#u", "behavior": "重构支付通道"}]
    errs = validate_profile(p, business_terms=["支付"])
    assert any("支付" in e for e in errs)
