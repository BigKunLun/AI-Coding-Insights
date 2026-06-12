"""测试新增的 JSONL 解析字段：plan_mode_count, skill_names, mcp_servers。"""
import json
import tempfile
from pathlib import Path
from ai_coding_insights.jsonl_parser import parse_session


def _write_jsonl(lines: list[dict], path: Path):
    with path.open("w", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")


def test_plan_mode_extraction(tmp_path):
    lines = [
        {"type": "user", "sessionId": "s1", "cwd": "/repo",
         "message": {"content": "帮我改代码", "role": "user"}, "timestamp": "2025-01-01T10:00:00Z"},
        {"type": "assistant", "sessionId": "s1",
         "message": {"model": "claude-1", "content": [
             {"type": "tool_use", "name": "EnterPlanMode", "id": "t1",
              "input": {}}
         ]}, "timestamp": "2025-01-01T10:01:00Z"},
        {"type": "assistant", "sessionId": "s1",
         "message": {"model": "claude-1", "content": [
             {"type": "tool_use", "name": "ExitPlanMode", "id": "t2",
              "input": {}}
         ]}, "timestamp": "2025-01-01T10:05:00Z"},
    ]
    _write_jsonl(lines, tmp_path / "test.jsonl")
    s = parse_session(tmp_path / "test.jsonl")
    assert s.plan_mode_count == 2


def test_no_plan_mode_when_absent(tmp_path):
    lines = [
        {"type": "user", "sessionId": "s1", "cwd": "/repo",
         "message": {"content": "hi", "role": "user"}, "timestamp": "2025-01-01T10:00:00Z"},
        {"type": "assistant", "sessionId": "s1",
         "message": {"model": "claude-1", "content": [
             {"type": "tool_use", "name": "Bash", "id": "t1", "input": {}}
         ]}, "timestamp": "2025-01-01T10:01:00Z"},
    ]
    _write_jsonl(lines, tmp_path / "test.jsonl")
    s = parse_session(tmp_path / "test.jsonl")
    assert s.plan_mode_count == 0


def test_skill_names_extraction(tmp_path):
    lines = [
        {"type": "user", "sessionId": "s1", "cwd": "/repo",
         "message": {"content": "提交代码", "role": "user"}, "timestamp": "2025-01-01T10:00:00Z"},
        {"type": "assistant", "sessionId": "s1",
         "message": {"model": "claude-1", "content": [
             {"type": "tool_use", "name": "Skill", "id": "t1",
              "input": {"skill": "git"}}
         ]}, "timestamp": "2025-01-01T10:01:00Z"},
        {"type": "assistant", "sessionId": "s1",
         "message": {"model": "claude-1", "content": [
             {"type": "tool_use", "name": "Skill", "id": "t2",
              "input": {"skill": "superpowers:brainstorming"}}
         ]}, "timestamp": "2025-01-01T10:02:00Z"},
    ]
    _write_jsonl(lines, tmp_path / "test.jsonl")
    s = parse_session(tmp_path / "test.jsonl")
    assert sorted(s.skill_names) == sorted(["git", "superpowers:brainstorming"])


def test_skill_input_missing_defense(tmp_path):
    """Skill tool_use 的 input 非 dict 或缺 skill 键时不应崩溃。"""
    lines = [
        {"type": "user", "sessionId": "s1", "cwd": "/repo",
         "message": {"content": "hi", "role": "user"}, "timestamp": "2025-01-01T10:00:00Z"},
        {"type": "assistant", "sessionId": "s1",
         "message": {"model": "claude-1", "content": [
             {"type": "tool_use", "name": "Skill", "id": "t1",
              "input": "bad_string"}  # 非 dict input
         ]}, "timestamp": "2025-01-01T10:01:00Z"},
        {"type": "assistant", "sessionId": "s1",
         "message": {"model": "claude-1", "content": [
             {"type": "tool_use", "name": "Skill", "id": "t2",
              "input": {}}  # 缺 skill 键
         ]}, "timestamp": "2025-01-01T10:02:00Z"},
    ]
    _write_jsonl(lines, tmp_path / "test.jsonl")
    s = parse_session(tmp_path / "test.jsonl")
    assert s.skill_names == []


def test_mcp_servers_extraction(tmp_path):
    lines = [
        {"type": "user", "sessionId": "s1", "cwd": "/repo",
         "message": {"content": "查文档", "role": "user"}, "timestamp": "2025-01-01T10:00:00Z"},
        {"type": "assistant", "sessionId": "s1",
         "message": {"model": "claude-1", "content": [
             {"type": "tool_use", "name": "mcp__context7__resolve-library-id", "id": "t1",
              "input": {}}
         ]}, "timestamp": "2025-01-01T10:01:00Z"},
        {"type": "assistant", "sessionId": "s1",
         "message": {"model": "claude-1", "content": [
             {"type": "tool_use", "name": "mcp__github__search", "id": "t2",
              "input": {}}
         ]}, "timestamp": "2025-01-01T10:02:00Z"},
        # 重复 server 应去重
        {"type": "assistant", "sessionId": "s1",
         "message": {"model": "claude-1", "content": [
             {"type": "tool_use", "name": "mcp__context7__query-docs", "id": "t3",
              "input": {}}
         ]}, "timestamp": "2025-01-01T10:03:00Z"},
    ]
    _write_jsonl(lines, tmp_path / "test.jsonl")
    s = parse_session(tmp_path / "test.jsonl")
    assert sorted(s.mcp_servers) == sorted(["context7", "github"])


def test_no_mcp_when_absent(tmp_path):
    lines = [
        {"type": "user", "sessionId": "s1", "cwd": "/repo",
         "message": {"content": "hi", "role": "user"}, "timestamp": "2025-01-01T10:00:00Z"},
        {"type": "assistant", "sessionId": "s1",
         "message": {"model": "claude-1", "content": [
             {"type": "tool_use", "name": "Bash", "id": "t1", "input": {}}
         ]}, "timestamp": "2025-01-01T10:01:00Z"},
    ]
    _write_jsonl(lines, tmp_path / "test.jsonl")
    s = parse_session(tmp_path / "test.jsonl")
    assert s.mcp_servers == []
    assert s.plan_mode_count == 0
    assert s.skill_names == []


def test_mcp_empty_server_name_defense(tmp_path):
    """畸形工具名 "mcp__" 不应产生空字符串 server 名。"""
    lines = [
        {"type": "user", "sessionId": "s1", "cwd": "/repo",
         "message": {"content": "test", "role": "user"}, "timestamp": "2025-01-01T10:00:00Z"},
        {"type": "assistant", "sessionId": "s1",
         "message": {"model": "claude-1", "content": [
             {"type": "tool_use", "name": "mcp__", "id": "t1", "input": {}},
             {"type": "tool_use", "name": "mcp__context7__query-docs", "id": "t2", "input": {}},
         ]}, "timestamp": "2025-01-01T10:01:00Z"},
    ]
    _write_jsonl(lines, tmp_path / "test.jsonl")
    s = parse_session(tmp_path / "test.jsonl")
    assert s.mcp_servers == ["context7"]  # 空 server 名被过滤
