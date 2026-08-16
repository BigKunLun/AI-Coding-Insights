"""Codex rollout parser 的字段映射与防御测试。

fixtures 是从本机真实 rollout **白名单重建**的脱敏样本：结构 / key / 枚举值 / 条数
与真实一致，所有自由文本（真人输入、助手消息、命令、patch 正文、系统提示）换成占位，
路径改写成 /tmp/repo-a，仓库 URL 改成 example/demo。
"""
import dataclasses
import json
from pathlib import Path

import pytest

from ai_coding_insights import codex_source, sources

FIXTURES = Path(__file__).parent / "fixtures" / "codex"
FULL = FIXTURES / "full-session.jsonl"
EDGE = FIXTURES / "edge-session.jsonl"


@pytest.fixture(scope="module")
def full():
    return codex_source.parse(FULL)


@pytest.fixture(scope="module")
def edge():
    return codex_source.parse(EDGE)


# ---------------------------------------------------------------- 字段逐个映射

def test_source_marked(full):
    assert full.source == sources.CODEX


def test_session_id_from_meta(full):
    assert full.session_id == "019e8cda-3db2-7b33-8be5-1482554bc0a8"


def test_cwd_and_branch(full):
    assert full.cwd == "/tmp/repo-a"
    assert full.git_branch == "master"


def test_timestamps_from_toplevel(full):
    # 只信顶层 .timestamp（UTC 带 Z），不信文件名/目录日期（那是本地时区）
    assert full.first_ts == "2026-06-03T09:39:55.551Z"
    assert full.last_ts == "2026-06-03T10:18:34.200Z"


def test_cli_version_into_cc_versions(full):
    assert full.cc_versions == ["0.136.0-alpha.2"]


def test_models_from_turn_context(full):
    # assistant 消息自身不带 model，只有 turn_context 带
    assert full.models_used == ["gpt-5.5"]


def test_tools_from_both_channels(full):
    # function_call（exec_command ×44）+ custom_tool_call（apply_patch ×2）
    assert full.tools_used == ["apply_patch", "exec_command"]


def test_thinking_counts_reasoning_blocks(full):
    assert full.thinking_block_count == 18


def test_record_type_counts_shape(full):
    rtc = full.record_type_counts
    assert rtc["response_item/function_call"] == 44
    assert rtc["response_item/function_call_output"] == 44
    assert rtc["event_msg/token_count"] == 24
    assert rtc["event_msg/agent_message"] == 23
    assert rtc["response_item/reasoning"] == 18
    assert rtc["event_msg/patch_apply_end"] == 2
    # payload 没有 type 字段的两类退化成裸 type，不写成 ".../None"
    assert rtc["session_meta"] == 3
    assert rtc["turn_context"] == 3
    assert sum(rtc.values()) == 203


def test_no_unmeasured_field_faked(full):
    # Codex 无对位概念的指标一律留空（未测量 ≠ 0，由 sources.unmeasured 兜住语义）
    assert full.commits == []
    assert full.plan_mode_count == 0
    assert full.option_pick_count == 0
    assert full.skill_names == []
    assert full.background_task_count == 0
    assert full.max_parallel_agents == 0


# ---------------------------------------------------------------- 真人轮次

def test_human_turns_deduped_across_channels(full):
    # 真实样本 3 个 turn：每个都同时落 response_item.message[user] 与
    # event_msg.user_message，两通道不能各计一次
    assert len(full.user_turns) == 3


def test_injected_context_not_a_human_turn(full):
    # <environment_context> 是注入的系统上下文，不是真人打的字
    assert all("<environment_context>" not in t.text for t in full.user_turns)
    assert all("<user_instructions>" not in t.text for t in full.user_turns)


def test_developer_role_excluded(full):
    assert all("<permissions instructions>" not in t.text for t in full.user_turns)


def test_turn_uuid_stable_and_unique(full):
    uuids = [t.uuid for t in full.user_turns]
    assert len(set(uuids)) == 3
    assert all(u.startswith("turn-") for u in uuids)
    # 合成 uuid 不得带文件名（可能含业务目录名）
    assert all("rollout" not in u and "/" not in u for u in uuids)


def test_turn_timestamps_present(full):
    assert all(t.timestamp.endswith("Z") for t in full.user_turns)


def test_edge_dedup_only_across_channels(edge):
    # 边界样本里：① 只落 response_item 的一轮 ② 两通道都落的一轮（算一次）
    # ③ 隔了很远之后同通道再发一遍同样的话（真的是两次输入，必须各计一次）
    texts = [t.text.strip() for t in edge.user_turns]
    assert len(texts) == 3
    assert texts.count("占位真人指令：只在 response_item 通道出现") == 2
    assert texts.count("占位真人指令：两个通道都出现") == 1


def test_edge_non_string_user_message_ignored(edge):
    assert all(t.text.strip() for t in edge.user_turns)


# ---------------------------------------------------------------- 编辑与落地

def test_edit_count_is_successful_patches(full):
    assert full.edit_count == 2


def test_edited_paths_are_change_keys(full):
    assert full.edited_paths == [
        "/tmp/repo-a/doc-a.md", "/tmp/repo-a/doc-b.md",
        "/tmp/repo-a/docs/doc-c.md", "/tmp/repo-a/docs/doc-d.md"]


def test_failed_patch_not_counted(edge):
    # success=false 与 success="true"（脏值）都不算落地；changes 非 dict 也不算
    assert edge.edit_count == 1
    assert "/tmp/repo-b/failed.md" not in edge.edited_paths
    assert "/tmp/repo-b/not-bool-success.md" not in edge.edited_paths


def test_relative_change_key_joined_to_cwd(edge):
    # 相对路径按工作区根拼回绝对（丢掉就等于落地率分子无声变小）；空 key 丢弃
    assert edge.edited_paths == ["/tmp/repo-b/docs/rel.md", "/tmp/repo-b/ok.md"]


def test_edited_paths_all_absolute(edge, full):
    for s in (edge, full):
        assert all(p.startswith("/") for p in s.edited_paths)


# ---------------------------------------------------------------- 隐私铁律

_PATCH_SECRETS = ("占位新增文件全文", "占位旧行", "占位新行", "unified_diff",
                  "@@ -1 +1 @@", "占位系统提示", "占位命令回执", "ENCRYPTED_PLACEHOLDER",
                  "Begin Patch", "占位助手消息", "占位模式说明")


def _walk(v):
    """把 dataclass 全字段摊平成字符串流，用于关键词扫描。"""
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        for f in dataclasses.fields(v):
            yield from _walk(getattr(v, f.name))
    elif isinstance(v, dict):
        for k, x in v.items():
            yield from _walk(k)
            yield from _walk(x)
    elif isinstance(v, (list, tuple, set)):
        for x in v:
            yield from _walk(x)
    else:
        yield str(v)


@pytest.mark.parametrize("keyword", _PATCH_SECRETS)
def test_patch_body_never_enters_parsed_session(full, keyword):
    # 🚨 patch 的 content / unified_diff 里是文件全文与 diff：进任何字段都等于业务代码出本机
    blob = "\n".join(_walk(full))
    assert keyword not in blob, f"{keyword!r} 泄漏进 ParsedSession"


def test_system_prompt_never_read(full):
    blob = "\n".join(_walk(full))
    assert "base_instructions" not in blob
    assert "dynamic_tools" not in blob
    assert "placeholder_tool_0" not in blob


def test_only_human_text_is_free_text(full):
    # 唯一允许出现的自由文本是真人输入（与 CC 同口径，出规则层前过 redact）
    assert [t.text.strip() for t in full.user_turns] == [
        "占位真人指令一：请梳理一下当前目录结构并给出计划",
        "占位真人指令二：按你的建议来",
        "好了吗"]


# ---------------------------------------------------------------- token

def test_token_takes_last_cumulative_not_sum(full):
    # 24 条 token_count 的 total_token_usage 单调递增到 858556；累加会双算
    bucket = full.token_usage["gpt-5.5"]
    assert bucket["cache_read"] == 727552
    assert bucket["output"] == 11305
    assert bucket["cache_creation"] == 0        # Codex 无写缓存概念
    # input 扣掉 cached：Codex 的 input_tokens 含缓存，CC 口径不含
    assert bucket["input"] == 847251 - 727552


def test_token_buckets_sum_to_total_tokens(full):
    # 四桶之和必须等于 Codex 自报的 total_tokens，否则跨来源 token_total 不可比
    bucket = full.token_usage["gpt-5.5"]
    assert sum(bucket.values()) == 858556


def test_token_dirty_values_do_not_crash(edge):
    # 脏 token_count（字符串/None/bool）在前、干净的在后：既不炸，也仍取最后一条
    bucket = edge.token_usage["gpt-5.5"]
    assert bucket == {"input": 600, "output": 50, "cache_read": 400, "cache_creation": 0}


# ---------------------------------------------------------------- MCP 宽松匹配

def test_mcp_three_shapes_all_recognized(edge):
    # 本机零 MCP 样本，三种形状都认；认不到也不许报错
    assert edge.mcp_servers == ["atlas", "demo", "legacy"]


def test_no_mcp_in_full_sample(full):
    assert full.mcp_servers == []


# ---------------------------------------------------------------- 脏数据防御

def test_edge_session_parses_without_meta(edge):
    # 整个文件没有 session_meta：cwd 退化到 turn_context，session_id 为空
    assert edge.session_id == ""
    assert edge.cwd == "/tmp/repo-b"
    assert edge.git_branch is None
    assert edge.cc_versions == []


def test_session_id_falls_back_to_filename(tmp_path):
    p = tmp_path / "rollout-2026-06-03T17-39-33-019e8cda-3db2-7b33-8be5-1482554bc0a8.jsonl"
    p.write_text('{"timestamp":"2026-06-03T09:00:00.000Z","type":"turn_context",'
                 '"payload":{"cwd":"/tmp/repo-c","model":"gpt-5.5"}}\n', encoding="utf-8")
    assert codex_source.parse(p).session_id == "019e8cda-3db2-7b33-8be5-1482554bc0a8"


def test_unknown_payload_types_still_counted(edge):
    assert edge.record_type_counts["event_msg/mystery_event"] == 1
    assert edge.record_type_counts["future_record/whatever"] == 1
    # payload 缺失 / payload 非 dict 的记录退化成裸 type
    assert edge.record_type_counts["response_item"] >= 2


def test_empty_file(tmp_path):
    p = tmp_path / "rollout-empty.jsonl"
    p.write_text("", encoding="utf-8")
    s = codex_source.parse(p)
    assert s.session_id == "" and s.user_turns == [] and s.first_ts is None
    assert s.source == sources.CODEX


def test_all_lines_garbage(tmp_path):
    p = tmp_path / "rollout-garbage.jsonl"
    p.write_text("nope\n[1,2]\n\"str\"\n7\nnull\n", encoding="utf-8")
    s = codex_source.parse(p)
    assert s.user_turns == [] and s.record_type_counts == {}


def test_non_string_timestamp_ignored(edge):
    # 有一条 agent_message 的 timestamp 是数字：不能进 min/max 比较
    assert edge.first_ts == "2026-06-04T01:00:00.000Z"
    assert edge.last_ts == "2026-06-04T01:00:26.000Z"


def test_tool_without_name_skipped(edge):
    assert "" not in edge.tools_used
    assert sorted(edge.tools_used) == [
        "exec_command", "fetch", "mcp__", "mcp__demo__query", "search"]


# ---------------------------------------------------------------- Source 接线

def test_iter_sessions_finds_nested_rollouts(tmp_path):
    day = tmp_path / "2026" / "06" / "03"
    day.mkdir(parents=True)
    (day / "rollout-a-019e8cda-3db2-7b33-8be5-1482554bc0a8.jsonl").write_text(
        FULL.read_text(encoding="utf-8"), encoding="utf-8")
    (day / "not-a-rollout.jsonl").write_text("{}\n", encoding="utf-8")
    got = list(codex_source.iter_sessions(tmp_path))
    assert len(got) == 1
    assert got[0].source == sources.CODEX


def test_iter_sessions_empty_root(tmp_path):
    assert list(codex_source.iter_sessions(tmp_path)) == []


def test_earliest_ts_reads_head_only(tmp_path):
    day = tmp_path / "2026" / "06" / "03"
    day.mkdir(parents=True)
    (day / "rollout-a-019e8cda-3db2-7b33-8be5-1482554bc0a8.jsonl").write_text(
        FULL.read_text(encoding="utf-8"), encoding="utf-8")
    assert codex_source.earliest_ts(tmp_path).startswith("2026-06-03T09:39:55")


def test_earliest_ts_none_when_empty(tmp_path):
    assert codex_source.earliest_ts(tmp_path) is None


def test_registry_wiring_uses_this_module(tmp_path):
    # sources.py 里 CODEX 的两个惰性绑定必须真能调起来（改名即红）
    src = sources.get_source(sources.CODEX)
    assert list(src.iter_sessions(tmp_path)) == []
    assert src.earliest_ts(tmp_path) is None


def test_session_files_lists_rollouts(tmp_path):
    day = tmp_path / "2026" / "06" / "03"
    day.mkdir(parents=True)
    f = day / "rollout-a-019e8cda-3db2-7b33-8be5-1482554bc0a8.jsonl"
    f.write_text("", encoding="utf-8")
    assert codex_source.session_files(tmp_path) == [f]


# ---------------------------------------------------------------- fixture 自查

def test_fixtures_carry_no_business_words():
    """fixtures 脱敏闸门：真实会话里的业务标识不得残留。"""
    blob = FULL.read_text(encoding="utf-8") + EDGE.read_text(encoding="utf-8")
    for word in ("shijianing", "CodingTime", "Notebook",
                 "content-map", "content-organization"):
        assert word not in blob, f"fixture 残留业务词 {word!r}"
    assert "/Users/" not in blob
    assert "https://github.com/example/demo.git" in blob   # 仓库 URL 已换成示例值


def test_fixture_line_count_matches_real_sample():
    # 结构保真：条数与本机真实 rollout 一致（203 行）
    assert len(FULL.read_text(encoding="utf-8").splitlines()) == 203


def test_fixture_is_valid_jsonl_with_three_top_keys():
    for line in FULL.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        assert sorted(rec) == ["payload", "timestamp", "type"]
