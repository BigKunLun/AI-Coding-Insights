from ai_coding_insights.profile_schema import validate_profile


def _ok():
    return {"breadth": {"summary": "广度高", "tools": ["Bash"]},
            "depth": {"summary": "多轮打磨"},
            "outcome": {"summary": "落地多", "landed": 18, "total": 23},
            "evidence": [{"pointer": "file#u1", "behavior": "纠正了一处实现方案"}]}


def test_valid_profile_passes():
    assert validate_profile(_ok()) == []


def test_l4_share_deprecated():
    p = _ok(); p["l4_share"] = 0.4
    assert any("l4_share 已废弃" in e for e in validate_profile(p))


def test_posture_fields_absent_is_valid():
    assert validate_profile(_ok()) == []      # 画像不含任何姿势字段即合法


def test_frictions_require_pointers_key():
    p = _ok()
    p["frictions"] = [{"observation": "o", "suggestion": "s"}]      # 缺 pointers
    assert any("pointers" in e for e in validate_profile(p))
    p["frictions"] = [{"observation": "o", "suggestion": "s", "pointers": []}]
    assert validate_profile(p) == []                                 # 空列表合法
    p["frictions"] = [{"observation": "o", "suggestion": "s", "pointers": [1]}]
    assert any("pointers" in e for e in validate_profile(p))         # 非字符串项


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
