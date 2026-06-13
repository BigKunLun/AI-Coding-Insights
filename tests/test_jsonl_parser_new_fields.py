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


# ---- 三个高阶维度信号（Thinking / Background Work / 真并行）----
# 形状以本机真实 transcript 校准：thinking 是 assistant content block type=="thinking"；
# run_in_background 是 Bash/Agent tool_use 的 input 布尔；真并行 = 单条 assistant
# message 内 name=="Agent" 的 tool_use 块数（API 并行工具调用落同一条消息）。

def test_thinking_block_count(tmp_path):
    """assistant content 里 type=="thinking" 的块计入 thinking_block_count。"""
    lines = [
        {"type": "user", "sessionId": "s1", "cwd": "/repo",
         "message": {"content": "深入分析下", "role": "user"}, "timestamp": "2025-01-01T10:00:00Z"},
        {"type": "assistant", "sessionId": "s1",
         "message": {"model": "claude-1", "content": [
             {"type": "thinking", "thinking": "let me reason", "signature": "x"},
             {"type": "text", "text": "好的"},
         ]}, "timestamp": "2025-01-01T10:01:00Z"},
        {"type": "assistant", "sessionId": "s1",
         "message": {"model": "claude-1", "content": [
             {"type": "thinking", "thinking": "again", "signature": "y"},
         ]}, "timestamp": "2025-01-01T10:02:00Z"},
    ]
    _write_jsonl(lines, tmp_path / "test.jsonl")
    s = parse_session(tmp_path / "test.jsonl")
    assert s.thinking_block_count == 2


def test_thinking_block_count_zero_when_absent(tmp_path):
    lines = [
        {"type": "assistant", "sessionId": "s1",
         "message": {"model": "claude-1", "content": [
             {"type": "text", "text": "纯文本回复"}]},
         "timestamp": "2025-01-01T10:01:00Z"},
    ]
    _write_jsonl(lines, tmp_path / "test.jsonl")
    s = parse_session(tmp_path / "test.jsonl")
    assert s.thinking_block_count == 0


def test_background_task_count_bash_and_agent(tmp_path):
    """Bash 与 Agent 的 tool_use 带 run_in_background:true 各计一次。"""
    lines = [
        {"type": "assistant", "sessionId": "s1",
         "message": {"model": "claude-1", "content": [
             {"type": "tool_use", "name": "Bash", "id": "t1",
              "input": {"command": "sleep 1", "run_in_background": True}},
             {"type": "tool_use", "name": "Agent", "id": "t2",
              "input": {"prompt": "p", "subagent_type": "x", "run_in_background": True}},
         ]}, "timestamp": "2025-01-01T10:01:00Z"},
    ]
    _write_jsonl(lines, tmp_path / "test.jsonl")
    s = parse_session(tmp_path / "test.jsonl")
    assert s.background_task_count == 2


def test_background_task_count_false_or_absent_not_counted(tmp_path):
    """run_in_background 为 false 或缺失的 tool_use 不计。"""
    lines = [
        {"type": "assistant", "sessionId": "s1",
         "message": {"model": "claude-1", "content": [
             {"type": "tool_use", "name": "Bash", "id": "t1",
              "input": {"command": "ls", "run_in_background": False}},
             {"type": "tool_use", "name": "Bash", "id": "t2",
              "input": {"command": "pwd"}},  # 缺该键
         ]}, "timestamp": "2025-01-01T10:01:00Z"},
    ]
    _write_jsonl(lines, tmp_path / "test.jsonl")
    s = parse_session(tmp_path / "test.jsonl")
    assert s.background_task_count == 0


def test_parallel_agents_single_dispatch(tmp_path):
    """每条 message 只派一个 Agent → 峰值并行=1，真并行轮次=0（顺序派发不算并行）。"""
    lines = [
        {"type": "assistant", "sessionId": "s1",
         "message": {"model": "claude-1", "content": [
             {"type": "tool_use", "name": "Agent", "id": "t1", "input": {}}]},
         "timestamp": "2025-01-01T10:01:00Z"},
        {"type": "assistant", "sessionId": "s1",
         "message": {"model": "claude-1", "content": [
             {"type": "tool_use", "name": "Agent", "id": "t2", "input": {}}]},
         "timestamp": "2025-01-01T10:02:00Z"},
    ]
    _write_jsonl(lines, tmp_path / "test.jsonl")
    s = parse_session(tmp_path / "test.jsonl")
    assert s.max_parallel_agents == 1
    assert s.parallel_agent_turns == 0


def test_parallel_agents_multiple_in_one_message(tmp_path):
    """单条 message 内 3 个 Agent → 峰值=3，真并行轮次=1。"""
    lines = [
        {"type": "assistant", "sessionId": "s1",
         "message": {"model": "claude-1", "content": [
             {"type": "tool_use", "name": "Agent", "id": "t1", "input": {}},
             {"type": "tool_use", "name": "Agent", "id": "t2", "input": {}},
             {"type": "tool_use", "name": "Agent", "id": "t3", "input": {}},
             {"type": "tool_use", "name": "Bash", "id": "t4", "input": {}},
         ]}, "timestamp": "2025-01-01T10:01:00Z"},
    ]
    _write_jsonl(lines, tmp_path / "test.jsonl")
    s = parse_session(tmp_path / "test.jsonl")
    assert s.max_parallel_agents == 3
    assert s.parallel_agent_turns == 1


def test_parallel_agents_zero_when_no_agent(tmp_path):
    lines = [
        {"type": "assistant", "sessionId": "s1",
         "message": {"model": "claude-1", "content": [
             {"type": "tool_use", "name": "Bash", "id": "t1", "input": {}}]},
         "timestamp": "2025-01-01T10:01:00Z"},
    ]
    _write_jsonl(lines, tmp_path / "test.jsonl")
    s = parse_session(tmp_path / "test.jsonl")
    assert s.max_parallel_agents == 0
    assert s.parallel_agent_turns == 0


# ---- plan_mode 多形状：permission-mode:plan 记录（新版本 CC 的 plan 信号）----

def test_plan_mode_from_permission_mode_record(tmp_path):
    """permission-mode:plan 记录计入 plan_mode_count（新版本 CC 的 plan 形状）。"""
    lines = [
        {"type": "permission-mode", "sessionId": "s1", "permissionMode": "plan"},
        {"type": "permission-mode", "sessionId": "s1", "permissionMode": "plan"},
        {"type": "assistant", "sessionId": "s1",
         "message": {"model": "claude-1", "content": [
             {"type": "tool_use", "name": "EnterPlanMode", "id": "t1", "input": {}}]},
         "timestamp": "2025-01-01T10:01:00Z"},
    ]
    _write_jsonl(lines, tmp_path / "t.jsonl")
    s = parse_session(tmp_path / "t.jsonl")
    assert s.plan_mode_count == 3   # 2 条 permission-mode:plan + 1 个 EnterPlanMode（并集）


def test_permission_mode_non_plan_not_counted(tmp_path):
    lines = [
        {"type": "permission-mode", "sessionId": "s1", "permissionMode": "auto"},
        {"type": "permission-mode", "sessionId": "s1", "permissionMode": "bypassPermissions"},
    ]
    _write_jsonl(lines, tmp_path / "t.jsonl")
    s = parse_session(tmp_path / "t.jsonl")
    assert s.plan_mode_count == 0
