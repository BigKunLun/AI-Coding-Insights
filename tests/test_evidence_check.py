from ai_coding_insights.evidence_check import (extract_turn_uuids,
                                               flag_missing_pointers, split_pointer)


def test_extract_turn_uuids_matches_field_not_substring():
    lines = [
        '{"sessionId": "sess-1", "uuid": "turn-a", "message": {}}',
        '{"sessionId": "sess-1", "uuid":"turn-b", "toolUseResult": "引用 sess-1"}',
        '{"sessionId": "sess-1", "type": "summary"}',   # 无 uuid 字段
        "not-json-line",
    ]
    got = extract_turn_uuids(lines)
    assert got == {"turn-a", "turn-b"}
    # 会话 id 只以 sessionId 字段出现，不得被收进 uuid 集合
    assert "sess-1" not in got


def test_split_pointer_forms():
    assert split_pointer("/a/s.jsonl#u-1") == ("/a/s.jsonl", "u-1")
    # 会话级指针：无 # 或 # 后为空，uuid 均为 None
    assert split_pointer("/a/s.jsonl") == ("/a/s.jsonl", None)
    assert split_pointer("/a/s.jsonl#") == ("/a/s.jsonl", None)
    assert split_pointer(None) == ("", None)
    assert split_pointer("") == ("", None)


def test_split_pointer_path_containing_hash():
    # 路径自身含 #：按最后一个 # 切出 uuid
    assert split_pointer("/a/proj#2/s.jsonl#u-1") == ("/a/proj#2/s.jsonl", "u-1")
    # 含 # 路径的会话级指针：切出的「uuid」段含 / → 整串是路径
    assert split_pointer("/a/proj#2/s.jsonl") == ("/a/proj#2/s.jsonl", None)


def test_flag_missing_pointers_marks_only_misses():
    profile = {
        "evidence": [
            {"pointer": "/ok.jsonl#u1", "behavior": "命中"},
            {"pointer": "/ok.jsonl#fake", "behavior": "uuid 不存在"},
            {"pointer": "/gone.jsonl#u1", "behavior": "文件不存在"},
        ],
        "highlights": [{"pointer": "/ok.jsonl", "behavior": "会话级，只验文件"}],
    }
    ok = lambda path, uuid: path == "/ok.jsonl" and uuid in (None, "u1")
    out, misses = flag_missing_pointers(profile, ok)
    assert "pointer_missing" not in out["evidence"][0]
    assert out["evidence"][1]["pointer_missing"] is True
    assert out["evidence"][2]["pointer_missing"] is True
    assert "pointer_missing" not in out["highlights"][0]
    assert misses == ["/ok.jsonl#fake", "/gone.jsonl#u1"]
    # 不修改入参
    assert "pointer_missing" not in profile["evidence"][1]


def test_flag_missing_pointers_empty_pointer_is_miss():
    out, misses = flag_missing_pointers(
        {"evidence": [{"pointer": "", "behavior": "x"}]}, lambda p, u: True)
    assert out["evidence"][0]["pointer_missing"] is True
    assert misses == [""]


def test_flag_missing_pointers_tolerates_absent_lists():
    out, misses = flag_missing_pointers({"evidence": "not-a-list"}, lambda p, u: True)
    assert out["evidence"] == "not-a-list"
    assert misses == []
