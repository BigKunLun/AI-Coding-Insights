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
