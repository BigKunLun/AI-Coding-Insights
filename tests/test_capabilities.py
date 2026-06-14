from ai_coding_insights.capabilities import unused_capabilities


def test_all_used_returns_empty():
    used = {"Agent": 3, "Workflow": 1, "Skill": 2, "EnterPlanMode": 1,
            "TaskCreate": 1, "WebSearch": 1, "mcp__foo__bar": 1}
    assert unused_capabilities(used) == []


def test_unused_detected_with_scene():
    gaps = unused_capabilities({"Read": 5, "Bash": 9})
    labels = [g["label"] for g in gaps]
    assert "SubAgent 委派" in labels and "Workflow 编排" in labels
    assert all(g["scene"] for g in gaps)      # 每条都带场景提示


def test_mcp_prefix_counts_as_used():
    gaps = unused_capabilities({"mcp__context7__query-docs": 2})
    assert "MCP 外部工具" not in [g["label"] for g in gaps]


# ---- 真并行：用过 Agent ≠ 真并行激活（修「真并行零 vs 全覆盖」矛盾）----
_FULL_TOOLS = {"Agent": 5, "Workflow": 1, "Skill": 2, "EnterPlanMode": 1,
               "TaskCreate": 1, "WebSearch": 1, "mcp__f__b": 1}
_FULL_CS = {"has_custom_skills": True, "claude_md_sessions": 3, "has_hooks": True}


def test_parallel_subagent_gap_when_used_agent_but_never_parallel():
    # 用过 Agent 但单轮峰值<2（从未真并行）→「并行 SubAgent」单列为盲区，
    # 「SubAgent 委派」不再报（用过即激活）。06 不再误判「全覆盖」。
    gaps = unused_capabilities(_FULL_TOOLS, _FULL_CS, {"max_parallel_agents": 1})
    labels = [g["label"] for g in gaps]
    assert "并行 SubAgent" in labels
    assert "SubAgent 委派" not in labels


def test_parallel_subagent_no_gap_when_activated():
    gaps = unused_capabilities(_FULL_TOOLS, _FULL_CS, {"max_parallel_agents": 3})
    assert gaps == []                       # 真并行已激活 → 全覆盖成立


def test_parallel_subagent_skipped_without_metrics():
    # 无 metrics（老数据）→ 并行项不判，不报假阳性，保持旧行为
    assert unused_capabilities(_FULL_TOOLS, _FULL_CS) == []


def test_parallel_subagent_not_reported_when_agent_unused():
    # 没用过 Agent → 由「SubAgent 委派」覆盖，并行项不冗余报
    gaps = unused_capabilities({"Read": 1}, None, {"max_parallel_agents": 0})
    labels = [g["label"] for g in gaps]
    assert "SubAgent 委派" in labels
    assert "并行 SubAgent" not in labels
