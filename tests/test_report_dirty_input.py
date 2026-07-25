"""渲染层脏输入不炸报告：report.py 里直接吃外部 JSON 的转换点。

报告是整条编排链的最终产物，任何一处 int()/float() 吃到脏值就崩，
等于前面所有 subagent 的工作全部作废。这里钉死「崩溃 → 明确降级」。
降级口径与 view_model 一致：取不到数出「—」，绝不静默填 0。
"""

from ai_coding_insights.report import (
    _drift_flag_text, _fmt_delta, _fmt_tokens, _fmt_window, _render_token_details,
    render_profile_report,
)


def _profile(**over):
    p = {
        "breadth": {"headline": "工具广度跨 8 类"},
        "depth": {"headline": "多轮打磨"},
        "outcome": {"headline": "落地稳健", "landed": 37, "total": 46},
        "evidence": [{"pointer": "a.jsonl#u1", "behavior": "证据"}],
    }
    p.update(over)
    return p


def test_token格式化脏值出破折号而不炸():
    assert _fmt_tokens("脏") == "—"
    assert _fmt_tokens([1]) == "—"
    assert _fmt_tokens(float("inf")) == "—"
    # 干净口径不变：缺字段仍按 0（bucket 里没这项 = 该项没消耗），量级缩写照旧
    assert _fmt_tokens(None) == "0"
    assert _fmt_tokens(1500) == "1.5K"


def test_token明细非对象形态整段降级():
    assert _render_token_details(["脏"], 10, None) == ""
    html = _render_token_details({"opus": "脏"}, "脏", None)
    assert "opus" in html and "—" in html


def test_窗口天数脏值退回兜底天数():
    assert _fmt_window({"since_date": "a", "until_date": "b",
                        "lookback_days": "脏"}, 30) == "近 30 天"
    assert _fmt_window({"status": "first", "lookback_days": "脏"}, 30) == "首次基线 · 近 30 天"


def test_同比脏delta不出箭头():
    assert _fmt_delta({"arrow": "↑", "delta": "脏"}) == ""
    assert _fmt_delta({"arrow": "↑", "delta": [1]}) == ""
    assert "↓" in _fmt_delta({"arrow": "↓", "delta": -14})     # 干净口径不变


def test_漂移文案脏比率出破折号而不炸():
    txt = _drift_flag_text({"kind": "drop", "signal": "edit",
                            "older_rate": "脏", "newer_rate": None})
    assert "老版本段 —" in txt and "新版本段 0%" in txt        # 缺键仍按 0 的既有口径


def test_全脏metrics仍渲染出完整报告():
    """最坏输入：每个数值字段都是非数字串 —— 报告必须照出，且脏处显示「—」。"""
    脏 = "脏"
    metrics = {k: 脏 for k in (
        "session_count", "human_input_count", "active_days", "tool_breadth",
        "commit_count", "landed_count", "edit_count", "landed_ratio", "turn_p90",
        "decision_point_count", "duration_p90_min", "git_landed_count",
        "git_commit_total", "dropped_count", "subagent_sessions", "workflow_sessions",
        "mcp_sessions", "thinking_block_count", "background_task_count",
        "max_parallel_agents", "parallel_agent_turns", "plan_mode_sessions",
        "token_total", "option_pick_count")}
    metrics["token_usage"] = {"opus": 脏}
    metrics["daily"] = [{"date": "2026-06-13", "session_count": 脏}]
    metrics["trend"] = {"first_half": 脏, "second_half": {"sessions": 脏}}
    metrics["tool_session_counts"] = {"Bash": 脏}
    metrics["skill_total_counts"] = 脏
    metrics["customization_signals"] = 脏
    metrics["parse_health"] = {
        "version_span": {"older": "1.0", "newer": "2.0"},
        "drift_flags": [{"signal": "edit", "kind": "drop",
                         "older_rate": 脏, "newer_rate": 脏}],
        "unknown_types": {}}
    meta = {"generated_at": "2026-06-09T00:00:00Z", "lookback_days": 脏,
            "session_count": 脏, "included_projects": [],
            "window": {"mode": "all", "since_date": "a", "until_date": "b",
                       "lookback_days": 脏}}
    diff = {"edit_count": {"now": 脏, "prev": 脏, "delta": 脏, "arrow": "↑"}}
    html = render_profile_report(_profile(), meta, metrics, diff)
    assert html.startswith("<!doctype html>")
    assert "—" in html


def test_metrics整体不是对象也能渲染():
    html = render_profile_report(_profile(), {"generated_at": "2026-06-09T00:00:00Z"},
                                 ["脏"], None)
    assert html.startswith("<!doctype html>")


# ---- 逐字段脏化的整体闸门 ----
# 单点防御容易补漏一处：这里把一份完整入参的每个叶子逐个换成各种脏值全跑一遍，
# 任何一处让渲染抛异常都算回归——用户可见的最坏失败模式是「一份报告都拿不到」。

_满配 = {
    "profile": {
        "breadth": {"headline": "工具广度跨 8 类", "points": ["点一 —— 展开"]},
        "depth": {"headline": "多轮打磨", "points": ["深度点"]},
        "outcome": {"headline": "落地稳健", "landed": 37, "total": 46},
        "frictions": [{"observation": "观察 —— 展开", "suggestion": "建议",
                       "pointers": [{"pointer": "a.jsonl#u1"}]}],
        "highlights": [{"pointer": "a.jsonl#u1", "behavior": "高光"}],
        "evidence": [{"pointer": "a.jsonl#u1", "behavior": "证据"}],
    },
    "meta": {"generated_at": "2026-06-09T00:00:00Z", "lookback_days": 30,
             "session_count": 107, "included_projects": ["/r/A"],
             "window": {"mode": "all", "since_date": "2026-05-10",
                        "until_date": "2026-06-09", "lookback_days": 30,
                        "truncated": True, "data_start": "2026-05-12"},
             "run": {"model": "opus", "started_at": "2026-06-09T00:00:00Z", "agents": 3}},
    "metrics": {
        "session_count": 107, "human_input_count": 588, "active_days": 20,
        "tool_breadth": 14, "commit_count": 46, "landed_count": 37, "edit_count": 886,
        "landed_ratio": 0.8, "turn_p90": 10, "decision_point_count": 100,
        "duration_p90_min": 50.4, "git_landed_count": 8, "git_commit_total": 11,
        "dropped_count": 3, "subagent_sessions": 12, "max_parallel_agents": 3,
        "token_usage": {"opus": {"input": 1000, "output": 2000}}, "token_total": 3064,
        "tool_session_counts": {"Bash": 30, "Agent": 5}, "skill_total_counts": {"save": 3},
        "mcp_server_counts": {"mcp__x": 2},
        "daily": [{"date": "2026-06-13", "session_count": 4}],
        "trend": {"first_half": {"sessions": 10, "commits": 5, "landed_ratio": 0.4,
                                 "override": 30, "error": 9, "short_ratio": 0.2},
                  "second_half": {"sessions": 12, "commits": 8, "landed_ratio": 0.75,
                                  "override": 40, "error": 5, "short_ratio": 0.1}},
        "customization_signals": {"has_custom_skills": True, "claude_md_sessions": 5},
        "parse_health": {"cc_version_span": {"min": "1.0", "max": "2.0", "distinct": 3},
                         "drift_flags": [{"signal": "edit", "kind": "shift",
                                          "older_rate": 0.9, "newer_rate": 0.9,
                                          "older_median": 2, "newer_median": 9,
                                          "median_ratio": 4.5}],
                         "unknown_record_types": ["x"]},
    },
    "diff": {"edit_count": {"now": 886, "prev": 900, "delta": -14, "arrow": "↓"}},
}

_脏值形态 = ["脏", "", None, True, [], {}, [1], {"a": 1},
             float("nan"), float("inf"), -7, 0, 10 ** 400, "<script>x</script>"]


def _叶子路径(o, 前=()):
    if isinstance(o, dict):
        for k, v in o.items():
            yield 前 + (k,)
            yield from _叶子路径(v, 前 + (k,))
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield 前 + (i,)
            yield from _叶子路径(v, 前 + (i,))


def test_任一字段脏化都不炸整张报告():
    import copy
    路径 = [p for p in _叶子路径(_满配) if len(p) > 1]
    assert len(路径) > 80        # 覆盖面自检：结构变小了要发现
    for p in 路径:
        for 脏 in _脏值形态:
            obj = copy.deepcopy(_满配)
            cur = obj
            for x in p[:-1]:
                cur = cur[x]
            cur[p[-1]] = 脏
            html = render_profile_report(obj["profile"], obj["meta"],
                                         obj["metrics"], obj["diff"])
            assert html.startswith("<!doctype html>"), (p, 脏)


def test_四份入参整体形态错误也不炸():
    for 脏 in _脏值形态:
        for k in ("profile", "meta", "metrics", "diff"):
            入参 = {**_满配, k: 脏}
            html = render_profile_report(入参["profile"], 入参["meta"],
                                         入参["metrics"], 入参["diff"])
            assert html.startswith("<!doctype html>")
