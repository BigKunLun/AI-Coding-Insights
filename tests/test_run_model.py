import json

from ai_coding_insights.run_model import detect_run_model, extract_run_model


def _aline(model):
    return json.dumps({"type": "assistant", "uuid": "u",
                       "message": {"model": model, "content": []}})


def test_extract_takes_last_assistant_model():
    lines = [
        _aline("claude-old-model"),
        json.dumps({"type": "user", "message": {"content": "x"}}),
        _aline("claude-fable-5"),
    ]
    assert extract_run_model(lines) == "claude-fable-5"


def test_extract_skips_synthetic_and_non_assistant_lines():
    lines = [
        _aline("claude-fable-5"),
        # 子 agent 派发参数等非 assistant 行里的 model 值不得干扰
        json.dumps({"type": "progress", "data": {"model": "sonnet"}}),
        # CC 对失败 turn 写 <synthetic>，不是真实模型
        _aline("<synthetic>"),
    ]
    assert extract_run_model(lines) == "claude-fable-5"


def test_extract_none_when_absent():
    assert extract_run_model([json.dumps({"type": "user", "uuid": "u"})]) is None
    assert extract_run_model([]) is None


def test_detect_run_model_locates_session_transcript(tmp_path):
    proj = tmp_path / "proj-slug"
    proj.mkdir()
    (proj / "sess-1.jsonl").write_text(_aline("claude-fable-5") + "\n",
                                       encoding="utf-8")
    assert detect_run_model("sess-1", tmp_path) == "claude-fable-5"


def test_detect_run_model_missing_inputs_return_none(tmp_path):
    assert detect_run_model(None, tmp_path) is None
    assert detect_run_model("", tmp_path) is None
    assert detect_run_model("no-such-session", tmp_path) is None
    assert detect_run_model("sess-1", tmp_path / "no-such-dir") is None
