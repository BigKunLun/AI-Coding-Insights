from ai_coding_insights.models import InsightsReport, SessionStats
from ai_coding_insights.report import render_count_report


def test_render_contains_key_facts():
    rep = InsightsReport(
        generated_at="2026-06-09T00:00:00Z", lookback_days=30,
        sessions=[SessionStats("sess1234", "/repo", 10, 0.3, 600.0, ["Bash"], ["claude-opus-4-8"])],
        included_projects=["/repo"], completeness={"session_count": 1})
    html = render_count_report(rep)
    assert html.lstrip().startswith("<!doctype html>")
    assert "/repo" in html and "30" in html and "sess1234"[:8] in html


def test_render_profile_report():
    from ai_coding_insights.report import render_profile_report
    profile = {"posture_distribution": {"L1": 0.1, "L2": 0.05, "L3": 0.6, "L4": 0.25},
               "breadth": {"summary": "工具广度高"}, "depth": {"summary": "多轮打磨为主"},
               "outcome": {"summary": "落地稳", "landed": 18, "total": 23},
               "evidence": [{"pointer": "/p/s.jsonl#u1", "behavior": "推翻一处实现方案并给约束"}]}
    meta = {"generated_at": "2026-06-09T00:00:00Z", "lookback_days": 30,
            "session_count": 105, "included_projects": ["/r/Healio"]}
    html = render_profile_report(profile, meta)
    assert html.lstrip().startswith("<!doctype html>")
    assert "L3" in html and "60%" in html              # 姿势分布
    assert "18" in html and "23" in html               # 成果
    assert "推翻一处实现方案并给约束" in html             # 证据行为级
    assert "/p/s.jsonl#u1" in html                     # 指针
    # XSS 防护：注入 < 被转义
    p2 = dict(profile); p2["evidence"] = [{"pointer": "x", "behavior": "<script>x</script>"}]
    assert "<script>x" not in render_profile_report(p2, meta)


def test_render_profile_percent_form_posture_normalized():
    """校验层容忍百分数形态（和≈100）；渲染必须归一，不得出现 1800% 之类。"""
    from ai_coding_insights.report import render_profile_report
    profile = {"posture_distribution": {"L1": 18, "L2": 7, "L3": 57, "L4": 18},
               "breadth": {"summary": "b"}, "depth": {"summary": "d"},
               "outcome": {"summary": "o", "landed": 1, "total": 2},
               "evidence": [{"pointer": "f#u", "behavior": "纠错"}]}
    meta = {"generated_at": "2026-06-09T00:00:00Z", "lookback_days": 30,
            "session_count": 1, "included_projects": []}
    html = render_profile_report(profile, meta, metrics={"tool_breadth": 5})
    assert "1800%" not in html and "5700%" not in html
    assert "L4 18%" in html or "18%" in html


def test_render_profile_outcome_prefers_hard_metrics():
    """落地/提交与奖励挂钩：有硬指标时 KPI 卡与成果详述必须用 metrics，不用 LLM 转抄值。"""
    from ai_coding_insights.report import render_profile_report
    profile = {"posture_distribution": {"L1": 0.1, "L2": 0.1, "L3": 0.6, "L4": 0.2},
               "breadth": {"summary": "b"}, "depth": {"summary": "d"},
               "outcome": {"summary": "o", "landed": 1, "total": 2},   # LLM 抄错的值
               "evidence": [{"pointer": "f#u", "behavior": "纠错"}]}
    meta = {"generated_at": "2026-06-09T00:00:00Z", "lookback_days": 30,
            "session_count": 1, "included_projects": []}
    metrics = {"commit_count": 40, "landed_count": 30, "landed_ratio": 0.75}
    html = render_profile_report(profile, meta, metrics=metrics)
    assert "落地 30 / 共 40" in html
    assert "落地 1 / 共 2" not in html


def test_format_run_meta_full():
    """模型 + 起始时间 + 编排规模齐全：三段都出现，耗时按分钟取整。

    model 的唯一写入方是 cli 的确定性识别（读会话 transcript），可信，应渲染。
    """
    from ai_coding_insights.report import format_run_meta
    line = format_run_meta(
        {"model": "claude-fable-5", "started_at": "2026-06-11T08:00:00+00:00", "agents": 8},
        "2026-06-11T08:15:10+00:00")
    assert "由 claude-fable-5 生成" in line
    assert "运行约 15 分钟" in line
    assert "编排 8 个 agent" in line


def test_format_run_meta_partial_and_degraded():
    """各字段独立降级：缺哪段省哪段，时间不可解析/倒挂只丢耗时不丢整行。"""
    from ai_coding_insights.report import format_run_meta
    # 起始时间不可解析 → 丢耗时
    line = format_run_meta({"started_at": "not-a-time", "agents": 3},
                           "2026-06-11T08:15:00+00:00")
    assert "运行约" not in line and "编排 3 个 agent" in line
    # 时间倒挂（started 晚于 generated）→ 丢耗时
    line = format_run_meta({"started_at": "2026-06-11T09:00:00+00:00"},
                           "2026-06-11T08:00:00+00:00")
    assert "运行约" not in line
    # 不足半分钟 → 至少报 1 分钟，不出现 0 分钟
    line = format_run_meta({"started_at": "2026-06-11T08:00:00+00:00"},
                           "2026-06-11T08:00:10+00:00")
    assert "运行约 1 分钟" in line
    # agents 为 0 / 缺省 → 不出该段
    assert "agent" not in format_run_meta({"agents": 0}, "x")
    # 只有 model → 只出模型段
    assert format_run_meta({"model": "m"}, "2026-06-11T08:15:00+00:00") == "本报告由 m 生成"
    # 全空 → 空串
    assert format_run_meta(None, "2026-06-11T08:00:00+00:00") == ""
    assert format_run_meta({}, "2026-06-11T08:00:00+00:00") == ""


def test_render_profile_run_meta_footer():
    """meta.run 存在时报告末尾出运行信息行；缺省时整行不出现。"""
    from ai_coding_insights.report import render_profile_report
    profile = {"posture_distribution": {"L1": 0.1, "L2": 0.1, "L3": 0.6, "L4": 0.2},
               "breadth": {"summary": "b"}, "depth": {"summary": "d"},
               "outcome": {"summary": "o", "landed": 1, "total": 2},
               "evidence": [{"pointer": "f#u", "behavior": "纠错"}]}
    meta = {"generated_at": "2026-06-11T08:15:00+00:00", "lookback_days": 30,
            "session_count": 1, "included_projects": [],
            "run": {"started_at": "2026-06-11T08:00:00+00:00", "agents": 8}}
    html = render_profile_report(profile, meta)
    assert "本报告运行约 15 分钟" in html and "编排 8 个 agent" in html
    # 无 run → 整行不出现
    meta2 = {k: v for k, v in meta.items() if k != "run"}
    assert "本报告运行约" not in render_profile_report(profile, meta2)
