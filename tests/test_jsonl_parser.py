from ai_coding_insights.jsonl_parser import is_human_turn, extract_text


def _user(content, **kw):
    return {"type": "user", "message": {"content": content}, **kw}


def test_real_human_string_turn():
    assert is_human_turn(_user("修复这个 bug")) is True


def test_meta_injection_excluded():
    assert is_human_turn(_user("hook text", isMeta=True)) is False


def test_tool_result_carrier_excluded():
    assert is_human_turn(_user([{"type": "tool_result", "content": "..."}])) is False


def test_text_blocks_turn_included():
    assert extract_text([{"type": "text", "text": "abc"}]) == "abc"


def test_assistant_not_human():
    assert is_human_turn({"type": "assistant", "message": {"content": "hi"}}) is False


def test_multimodal_text_plus_image_kept():
    assert extract_text([{"type": "text", "text": "改配色"}, {"type": "image", "source": {}}]) == "改配色"


def test_image_only_returns_none():
    assert extract_text([{"type": "image", "source": {}}]) is None


import json
from pathlib import Path
from ai_coding_insights.jsonl_parser import parse_session

def test_parse_session(tmp_path):
    lines = [
        {"type":"user","sessionId":"s1","cwd":"/repo","gitBranch":"main",
         "uuid":"u1","timestamp":"2026-06-01T00:00:00Z","message":{"content":"做点事"}},
        {"type":"user","isMeta":True,"message":{"content":"<inject>"}},
        {"type":"assistant","timestamp":"2026-06-01T00:01:00Z",
         "message":{"model":"claude-opus-4-8","content":[
             {"type":"tool_use","name":"Bash"},
             {"type":"tool_use","name":"Agent"}]}},
        {"type":"user","message":{"content":[{"type":"tool_result","content":"x"}]}},
    ]
    p = tmp_path / "s1.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines))
    s = parse_session(p)
    assert s.session_id == "s1" and s.cwd == "/repo" and s.git_branch == "main"
    assert [t.text for t in s.user_turns] == ["做点事"]      # only 1 real human turn
    assert s.tools_used == ["Agent", "Bash"]
    assert s.models_used == ["claude-opus-4-8"]
    assert s.first_ts == "2026-06-01T00:00:00Z" and s.last_ts == "2026-06-01T00:01:00Z"


def test_parse_session_splits_only_on_newline(tmp_path):
    rec = {"type": "user", "sessionId": "s1", "cwd": "/r",
           "timestamp": "2026-06-01T00:00:00Z", "message": {"content": "line break"}}
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
    s = parse_session(p)
    assert s.session_id == "s1"
    assert [t.text for t in s.user_turns] == ["line break"]  # 整条记录完好


def test_parse_session_ts_chronological(tmp_path):
    lines = [
        {"type": "assistant", "timestamp": "2026-06-01T00:00:05Z", "message": {"model": "m", "content": []}},
        {"type": "user", "timestamp": "2026-06-01T00:00:01Z", "message": {"content": "a"}},
    ]
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    s = parse_session(p)
    assert s.first_ts == "2026-06-01T00:00:01Z" and s.last_ts == "2026-06-01T00:00:05Z"


def test_parse_session_skips_synthetic_model(tmp_path):
    lines = [
        {"type": "assistant", "timestamp": "2026-06-01T00:00:00Z", "message": {"model": "<synthetic>", "content": []}},
        {"type": "assistant", "timestamp": "2026-06-01T00:00:01Z", "message": {"model": "claude-opus-4-8", "content": []}},
    ]
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    s = parse_session(p)
    assert s.models_used == ["claude-opus-4-8"]


def test_parse_session_extracts_commits_and_edits(tmp_path):
    import json
    from ai_coding_insights.jsonl_parser import parse_session
    lines = [
        {"type":"user","sessionId":"s","cwd":"/r","uuid":"u1",
         "timestamp":"2026-06-01T00:00:00Z","message":{"content":"改这里"}},
        {"type":"assistant","timestamp":"2026-06-01T00:01:00Z","message":{"model":"claude-opus-4-8",
         "content":[{"type":"tool_use","name":"Edit"}]},
         "toolUseResult":{"structuredPatch":[{"oldStart":1,"oldLines":1,"newStart":1,"newLines":2,"lines":["+a"]}]}},
        {"type":"assistant","timestamp":"2026-06-01T00:02:00Z","message":{"content":[{"type":"tool_use","name":"Bash"}]},
         "toolUseResult":{"gitOperation":{"commit":{"sha":"e037656","kind":"committed"}}}},
        {"type":"assistant","timestamp":"2026-06-01T00:03:00Z","message":{"content":[{"type":"tool_use","name":"Bash"}]},
         "toolUseResult":{"gitOperation":{"commit":{"sha":"e037656","kind":"committed"}}}},  # 重复 sha
    ]
    p = tmp_path/"s.jsonl"; p.write_text("\n".join(json.dumps(x) for x in lines))
    s = parse_session(p)
    assert [c.sha for c in s.commits] == ["e037656"]      # 去重
    assert s.commits[0].kind == "committed"
    assert s.edit_count == 1


def test_token_usage_accumulated_per_model(tmp_path):
    lines = [
        {"type": "assistant", "sessionId": "s", "cwd": "/p", "timestamp": "2026-06-01T00:00:00Z",
         "message": {"model": "claude-opus-4-8", "usage": {
             "input_tokens": 100, "output_tokens": 50,
             "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 200},
          "content": []}},
        {"type": "assistant", "sessionId": "s", "cwd": "/p", "timestamp": "2026-06-01T00:01:00Z",
         "message": {"model": "claude-opus-4-8", "usage": {
             "input_tokens": 10, "output_tokens": 5,
             "cache_read_input_tokens": 100, "cache_creation_input_tokens": 0},
          "content": []}},
        {"type": "assistant", "sessionId": "s", "cwd": "/p", "timestamp": "2026-06-01T00:02:00Z",
         "message": {"model": "claude-haiku-4-5", "usage": {"input_tokens": 1, "output_tokens": 2},
          "content": []}},
        {"type": "assistant", "sessionId": "s", "cwd": "/p", "timestamp": "2026-06-01T00:03:00Z",
         "message": {"model": "claude-opus-4-8", "content": []}},
        {"type": "assistant", "sessionId": "s", "cwd": "/p", "timestamp": "2026-06-01T00:04:00Z",
         "message": {"model": "<synthetic>", "usage": {"input_tokens": 9, "output_tokens": 9},
          "content": []}},
    ]
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join(json.dumps(l) for l in lines), encoding="utf-8")
    s = parse_session(p)
    assert s.token_usage == {
        "claude-opus-4-8": {"input": 110, "output": 55, "cache_read": 1100, "cache_creation": 200},
        "claude-haiku-4-5": {"input": 1, "output": 2, "cache_read": 0, "cache_creation": 0},
    }


def test_string_message_record_does_not_crash(tmp_path):
    """message 为字符串（旧版/脏记录）不得炸掉整个会话解析。"""
    lines = [
        {"type": "user", "sessionId": "s2", "cwd": "/repo",
         "timestamp": "2026-06-01T00:00:00Z", "message": "plain string"},
        {"type": "assistant", "timestamp": "2026-06-01T00:01:00Z", "message": "also string"},
        {"type": "user", "uuid": "u9", "timestamp": "2026-06-01T00:02:00Z",
         "message": {"content": "正常输入"}},
    ]
    p = tmp_path / "s2.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines))
    s = parse_session(p)
    assert [t.text for t in s.user_turns] == ["正常输入"]


def test_non_string_timestamp_does_not_crash(tmp_path):
    """timestamp 为数字等非字符串时按缺失处理，不得在 min/max 比较时崩。"""
    lines = [
        {"type": "user", "sessionId": "s3", "cwd": "/repo",
         "timestamp": 12345, "message": {"content": "a"}},
        {"type": "user", "timestamp": "2026-06-01T00:00:00Z", "message": {"content": "b"}},
    ]
    p = tmp_path / "s3.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines))
    s = parse_session(p)
    assert s.first_ts == "2026-06-01T00:00:00Z" and s.last_ts == "2026-06-01T00:00:00Z"


def test_parse_timestamp_non_string_returns_none():
    from ai_coding_insights.timeutil import parse_timestamp
    assert parse_timestamp(12345) is None
    assert parse_timestamp(["2026-06-01"]) is None
    assert parse_timestamp(None) is None


def _ask_pair(tool_id, answers_or_rejection):
    """构造 AskUserQuestion 的 tool_use + tool_result 两条记录。"""
    ask = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "AskUserQuestion", "id": tool_id}]}}
    reply = {"type": "user",
             "message": {"content": [{"type": "tool_result", "tool_use_id": tool_id,
                                      "content": "..."}]},
             "toolUseResult": answers_or_rejection}
    return [ask, reply]


def _write_jsonl(tmp_path, lines):
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines))
    return p


def test_option_pick_counted(tmp_path):
    # 已回答：toolUseResult 是含 answers dict 的结构化形态，按答题数计
    lines = _ask_pair("t1", {"questions": [], "answers": {"问A": "选项1"}})
    s = parse_session(_write_jsonl(tmp_path, lines))
    assert s.option_pick_count == 1
    assert s.user_turns == []          # 选项回答不是真人轮次（既有行为不变）


def test_option_pick_multi_question_counts_each(tmp_path):
    # 一次调用问 2 题 = 2 个决策点
    lines = _ask_pair("t1", {"questions": [], "answers": {"问A": "甲", "问B": "乙,丙"}})
    s = parse_session(_write_jsonl(tmp_path, lines))
    assert s.option_pick_count == 2


def test_option_pick_rejection_not_counted(tmp_path):
    # 拒绝：toolUseResult 是字符串回执，不计
    lines = _ask_pair("t1", "The user doesn't want to proceed with this tool use.")
    s = parse_session(_write_jsonl(tmp_path, lines))
    assert s.option_pick_count == 0


def test_other_tool_result_with_answers_shape_not_counted(tmp_path):
    # 非 AskUserQuestion 的 tool_result 即使带 answers 形态也不计（必须按 id 配对）
    lines = [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "id": "t9"}]}},
        {"type": "user",
         "message": {"content": [{"type": "tool_result", "tool_use_id": "t9",
                                  "content": "x"}]},
         "toolUseResult": {"questions": [], "answers": {"问": "答"}}},
    ]
    s = parse_session(_write_jsonl(tmp_path, lines))
    assert s.option_pick_count == 0


def test_option_pick_same_id_not_double_counted(tmp_path):
    # 同一 tool_use id 的 tool_result 只配对一次
    pair = _ask_pair("t1", {"questions": [], "answers": {"问A": "甲"}})
    lines = pair + [pair[1]]            # 重复一条同 id 的回包
    s = parse_session(_write_jsonl(tmp_path, lines))
    assert s.option_pick_count == 1


def test_option_pick_none_id_not_paired(tmp_path):
    # tool_use 缺 id：不入待配对集，None 的 tool_use_id 不得误配
    lines = [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "AskUserQuestion"}]}},   # 无 id
        {"type": "user",
         "message": {"content": [{"type": "tool_result", "content": "x"}]},  # 无 tool_use_id
         "toolUseResult": {"questions": [], "answers": {"问": "答"}}},
    ]
    s = parse_session(_write_jsonl(tmp_path, lines))
    assert s.option_pick_count == 0
