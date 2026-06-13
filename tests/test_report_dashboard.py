import copy

from ai_coding_insights.report import render_profile_report, _fmt_delta

PROFILE = {
    "posture_distribution": {"L1": 0.18, "L2": 0.07, "L3": 0.57, "L4": 0.18},
    "breadth": {"summary": "工具广度高，跨多类工具", "tools": ["Bash", "Edit", "Grep"]},
    "depth": {"summary": "多轮打磨为主，反复约束"},
    "outcome": {"summary": "落地稳健", "landed": 37, "total": 46},
    "evidence": [
        {"pointer": "/abs/s.jsonl#u1", "behavior": "推翻一处实现方案并给约束"},
        {"pointer": "/abs/s.jsonl#u2", "behavior": "<script>alert(1)</script>"},
    ],
}
META = {
    "generated_at": "2026-06-09T00:00:00Z",
    "lookback_days": 30,
    "session_count": 107,
    "included_projects": ["/r/Healio", "/r/Other"],
}
METRICS = {
    "session_count": 107,
    "human_input_count": 588,
    "active_days": 20,
    "avg_turns": 5.5,
    "tool_breadth": 14,
    "commit_count": 46,
    "landed_count": 37,
    "edit_count": 886,
    "landed_ratio": 0.8,
}
DIFF_YOY = {
    "landed_ratio": {"now": 0.8, "prev": 0.75, "delta": 0.05, "arrow": "↑"},
    "commit_count": {"now": 46, "prev": 40, "delta": 6, "arrow": "↑"},
    "landed_count": {"now": 37, "prev": 37, "delta": 0, "arrow": "→"},
    "edit_count": {"now": 886, "prev": 900, "delta": -14, "arrow": "↓"},
    "session_count": {"now": 107, "prev": 100, "delta": 7, "arrow": "↑"},
    "human_input_count": {"now": 588, "prev": 500, "delta": 88, "arrow": "↑"},
    "tool_breadth": {"now": 14, "prev": 14, "delta": 0, "arrow": "→"},
    "active_days": {"now": 20, "prev": 18, "delta": 2, "arrow": "↑"},
}


def test_dashboard_full_render():
    html = render_profile_report(PROFILE, META, METRICS, DIFF_YOY)
    assert html.lstrip().startswith("<!doctype html>")
    # 指标卡数值（会话数）
    assert "107" in html
    # 进步/同比箭头
    assert "↑" in html
    # SVG 雷达
    assert "<svg" in html
    assert "<polygon" in html
    # 折叠附录：证据链 details
    assert html.count("<details") >= 1
    # 证据行为级文本
    assert "推翻一处实现方案并给约束" in html
    # XSS：转义后的 script，且不含裸 <script>alert
    assert "&lt;script&gt;" in html
    assert "<script>alert" not in html


def test_dashboard_compat_no_metrics_no_diff():
    html = render_profile_report(PROFILE, META)
    assert html.lstrip().startswith("<!doctype html>")
    assert len(html) > 0
    # 兜底：metrics 缺失时用 outcome 的 landed/total，不报错
    assert "37" in html and "46" in html


def test_dashboard_baseline_label():
    html = render_profile_report(PROFILE, META, METRICS, {"baseline": True})
    assert "首次基线" in html


def test_fmt_delta_ratio_two_decimals():
    # 比率类小数 delta（0.046）必须显示成两位小数 0.05，而不是误导的整数 0
    out = _fmt_delta({"now": 0.80, "prev": 0.754, "delta": 0.046, "arrow": "↑"})
    assert "0.05" in out
    assert "↑0<" not in out  # 不能渲染成误导的 ↑0
    assert ">↑0.05<" in out or "↑0.05" in out


def test_fmt_delta_integer_no_decimals():
    # 整数 delta（6 或 6.0）显示成整数 6，不带小数
    assert "6" in _fmt_delta({"now": 46, "prev": 40, "delta": 6, "arrow": "↑"})
    assert "6.00" not in _fmt_delta({"now": 46, "prev": 40, "delta": 6, "arrow": "↑"})
    assert "6.00" not in _fmt_delta({"now": 46.0, "prev": 40.0, "delta": 6.0, "arrow": "↑"})
    assert "6" in _fmt_delta({"now": 46.0, "prev": 40.0, "delta": 6.0, "arrow": "↑"})


def test_dashboard_ratio_delta_shows_two_decimals_not_zero():
    diff = {
        "landed_ratio": {"now": 0.80, "prev": 0.754, "delta": 0.046, "arrow": "↑"},
        "commit_count": {"now": 46, "prev": 40, "delta": 6, "arrow": "↑"},
    }
    html = render_profile_report(PROFILE, META, METRICS, diff)
    # 比率两位小数出现，整数项整数出现
    assert "0.05" in html
    assert "↑6" in html
    # 不能把落地率渲染成误导的 ↑0
    assert "↑0<" not in html


# ---- v5 仪表盘 ----

PROFILE_V5 = {
    "posture_distribution": {"L1": 0.18, "L2": 0.07, "L3": 0.57, "L4": 0.18},
    "breadth": {
        "headline": "工具广度跨 8 类，覆盖检索/编辑/编排",
        "points": ["高频用 Grep 检索", "并行 SubAgent 编排"],
        "metrics": [{"label": "工具数", "value": "28"}, {"label": "MCP", "value": "3"}],
        "tools": ["Bash", "Edit", "Grep"],
    },
    "depth": {
        "headline": "多轮打磨为主，反复约束直到落地",
        "points": ["平均 5.5 轮", "推翻并重构方案"],
        "metrics": [{"label": "中位轮次", "value": "6"}],
    },
    "outcome": {
        "headline": "落地稳健，提交率高",
        "points": ["落地率 80%"],
        "summary": "落地稳健",
        "landed": 37,
        "total": 46,
    },
    "frictions": [
        {"observation": "极短会话占比偏高，存在试探性提问",
         "suggestion": "先一次性给足上下文再发起"},
        {"observation": "<script>alert(2)</script>",
         "suggestion": "<b>建议</b>注入"},
    ],
    "evidence": [
        {"pointer": "/abs/s.jsonl#u1", "behavior": "推翻一处实现方案并给约束"},
        {"pointer": "/abs/s.jsonl#u2", "behavior": "<script>alert(1)</script>"},
    ],
}
META_V5 = {
    "generated_at": "2026-06-09T16:30:00+00:00",
    "lookback_days": 40,
    "session_count": 107,
    "included_projects": ["/r/Healio", "/r/Other"],
    "window": {
        "status": "ok",
        "since_date": "2026-05-01",
        "until_date": "2026-06-10",
        "lookback_days": 40,
    },
}
METRICS_V5 = {
    "session_count": 107,
    "human_input_count": 588,
    "active_days": 20,
    "avg_turns": 5.5,
    "tool_breadth": 28,
    "commit_count": 46,
    "landed_count": 37,
    "edit_count": 886,
    "landed_ratio": 0.8,
    "subagent_sessions": 12,
    "workflow_sessions": 5,
    "mcp_sessions": 8,
    "duration_median_min": 50.4,
}
DIFF_V5 = {
    # 正常同比键：出箭头
    "commit_count": {"now": 46, "prev": 40, "delta": 6, "arrow": "↑"},
    # no_base 键：不出箭头，且不能崩在 abs(None)
    "session_count": {"arrow": None, "delta": None, "no_base": True},
}


def test_dashboard_metric_families():
    html = render_profile_report(PROFILE_V5, META_V5, METRICS_V5, DIFF_V5)
    assert "产出落地" in html
    assert "协作编排" in html
    assert "高阶行为" in html
    assert "节奏投入" in html
    # 指标明细一卡四族（fam 行）：新增「高阶行为」
    assert html.count('class="fam-head"') == 4


def test_dashboard_advanced_signals_render_values():
    """三个高阶维度信号在「高阶行为」族里按硬指标确定性渲染。"""
    metrics = dict(METRICS_V5,
                   thinking_block_count=6072, thinking_sessions=198,
                   background_task_count=149, background_sessions=22,
                   max_parallel_agents=1, parallel_agent_turns=0)
    html = render_profile_report(PROFILE_V5, META_V5, metrics, DIFF_V5)
    assert "深度推理" in html and "6072" in html
    assert "后台委托" in html and "149" in html
    assert "真并行峰值" in html
    assert "真并行轮次" in html


def test_dashboard_v5_window_and_local_time():
    import re
    html = render_profile_report(PROFILE_V5, META_V5, METRICS_V5, DIFF_V5)
    # 取数起止（横幅 kicker）
    assert "取数 2026-05-01 → 2026-06-10" in html
    # 本地时间形态：页脚 aci-report · %Y-%m-%d %H:%M（与本机时区无关，仅校验格式存在）
    assert re.search(r"aci-report · \d{4}-\d{2}-\d{2} \d{2}:\d{2}", html)
    # 生成行不应残留 ISO 的 T 分隔或 UTC 偏移原样
    assert "2026-06-09T16:30:00+00:00" not in html
    assert "T16:30" not in html


def test_dashboard_v5_structured_dimensions():
    html = render_profile_report(PROFILE_V5, META_V5, METRICS_V5, DIFF_V5)
    # 维度分点（详述卡分点行）
    assert 'class="pt-title"' in html
    assert "高频用 Grep 检索" in html
    # headline 进卡片副题
    assert "工具广度跨 8 类，覆盖检索/编辑/编排" in html
    assert 'class="dim-card-sub"' in html
    # outcome 仍附落地/丢弃（git 主锚口径：旧口径 metrics 缺 git 键退到 transcript 硬证据）
    assert "落地 37 · 观测丢弃 9" in html


def test_dashboard_v5_frictions_block():
    html = render_profile_report(PROFILE_V5, META_V5, METRICS_V5, DIFF_V5)
    assert "摩擦 + 建议" in html
    assert "极短会话占比偏高，存在试探性提问" in html
    assert "先一次性给足上下文再发起" in html
    # 摩擦板块在证据链附录之前
    assert html.index("摩擦 + 建议") < html.index("证据链（")


def test_dashboard_v5_no_base_no_arrow():
    html = render_profile_report(PROFILE_V5, META_V5, METRICS_V5, DIFF_V5)
    # 正常键出现箭头
    assert "↑6" in html
    # no_base 键（session_count）不出箭头：会话数卡 / 摘要里该键不带 ↑↓→
    # 摘要条里「会话」一项不应带任何箭头
    import re
    # 找到「会话 」紧跟的片段，确认无箭头字符
    for m in re.finditer(r"会话[^<]{0,4}", html):
        seg = m.group(0)
        assert "↑" not in seg and "↓" not in seg and "→" not in seg


def test_dashboard_v5_escapes_injection():
    html = render_profile_report(PROFILE_V5, META_V5, METRICS_V5, DIFF_V5)
    # frictions 里的 XSS 被 escape
    assert "<script>alert(2)" not in html
    assert "&lt;script&gt;alert(2)" in html
    assert "<b>建议</b>注入" not in html


def test_dashboard_v5_compat_old_profile():
    # 旧式 profile（维度仅 summary，无 points/metrics）+ meta 无 window，仍正常渲染
    old_profile = {
        "posture_distribution": {"L1": 0.2, "L2": 0.1, "L3": 0.5, "L4": 0.2},
        "breadth": {"summary": "工具广度高", "tools": ["Bash"]},
        "depth": {"summary": "多轮打磨"},
        "outcome": {"summary": "落地稳健", "landed": 10, "total": 12},
        "evidence": [],
    }
    old_meta = {
        "generated_at": "2026-06-09T00:00:00Z",
        "lookback_days": 30,
        "session_count": 50,
        "included_projects": [],
    }
    html = render_profile_report(old_profile, old_meta)
    assert html.lstrip().startswith("<!doctype html>")
    # 无 window 时回退「近 N 天」
    assert "近 30 天" in html
    # 维度退回单段 headline/summary（进卡片副题与代表行描述）
    assert "工具广度高" in html
    # 无 metrics → 横幅不出档位大字、03 节无判据卡
    assert "档位判据" not in html
    # 无 frictions 时不出摩擦板块
    assert "摩擦 + 建议" not in html
    # 落地仍渲染（无 metrics → 兜底 LLM 抄值：landed=10、丢弃=total-landed=2）
    assert "落地 10 · 观测丢弃 2" in html


# ---- Task 7: 六处新板块 ----

def _profile():
    return copy.deepcopy(PROFILE_V5)


def _meta():
    return copy.deepcopy(META_V5)


def _metrics():
    m = copy.deepcopy(METRICS_V5)
    m["tool_session_counts"] = {"Bash": 10, "Edit": 8, "Grep": 5}
    m["landed_ratio"] = 0.8
    return m


def test_report_contains_new_sections():
    profile = _profile()
    profile["highlights"] = [{"pointer": "/p.jsonl#u1", "behavior": "推翻一处方案并给出更优约束"}]
    metrics = _metrics()
    metrics["token_usage"] = {"claude-opus-4-8": {"input": 1200000, "output": 340000,
                                                  "cache_read": 9000000, "cache_creation": 50000}}
    metrics["token_total"] = 10590000
    metrics["trend"] = {"first_half": {"sessions": 10, "commits": 5, "landed": 2, "landed_ratio": 0.4,
                                        "override": 30, "error": 9, "short_ratio": 0.2},
                        "second_half": {"sessions": 12, "commits": 8, "landed": 6, "landed_ratio": 0.75,
                                         "override": 40, "error": 5, "short_ratio": 0.1}}
    html = render_profile_report(profile, _meta(), metrics, None)
    for marker in ("L1", "跟随", "L4", "主导",          # 图例
                   "档位判据",                            # 判据卡
                   "Token", "10.6M",                     # token 附录(总量友好格式)
                   "能力盲区", "高光时刻", "窗口内趋势"):
        assert marker in html, marker


def test_report_skips_sections_when_data_absent():
    html = render_profile_report(_profile(), _meta(), None, None)   # metrics=None
    assert "Token 消耗" not in html and "窗口内趋势" not in html and "档位判据" not in html


def test_report_metrics_present_but_trend_token_absent():
    # metrics 在场但无 trend/token_usage：趋势/Token 单独跳过，判据卡/盲区仍渲染
    metrics = _metrics()                      # 无 trend / token_usage 字段
    assert "trend" not in metrics and "token_usage" not in metrics
    html = render_profile_report(_profile(), _meta(), metrics, None)
    assert "窗口内趋势" not in html
    assert "Token 消耗" not in html
    assert "档位判据" in html
    assert "能力盲区" in html


def test_report_outcome_uses_git_anchor():
    m = {**_metrics(), "git_landed_count": 8, "dropped_count": 3,
         "commit_count": 10, "landed_count": 7, "edit_count": 40,
         "landed_ratio": 8 / 11}
    html = render_profile_report(_profile(), _meta(), m, None)
    assert "落地提交" in html
    assert "观测丢弃" in html
    assert "≈5" in html          # 编辑/落地 = 40/8


def test_trend_hides_commit_rows_when_unobservable():
    trend = {"first_half": {"sessions": 2, "commits": 0, "landed": 0,
                            "landed_ratio": 0.0, "override": 1, "error": 0,
                            "short_ratio": 0.1},
             "second_half": {"sessions": 2, "commits": 0, "landed": 0,
                             "landed_ratio": 0.0, "override": 0, "error": 1,
                             "short_ratio": 0.2}}
    html = render_profile_report(_profile(), _meta(),
                                 {**_metrics(), "trend": trend}, None)
    assert "窗口内趋势" in html              # 趋势板块本身照常渲染
    assert "落地率</td>" not in html         # transcript 不可观测时不出 0% 假行
    assert "提交（次/会话）" not in html


# ---- KPI strip 已整排移除（与下方指标卡纯属重复，速览职责归三组指标卡）----

def _metrics_with_trend_token():
    """满数据：在 _metrics() 基础上补 trend + token_usage + token_total。"""
    m = _metrics()
    m["token_usage"] = {"claude-opus-4-8": {"input": 1200000, "output": 340000,
                                            "cache_read": 9000000, "cache_creation": 50000}}
    m["token_total"] = 10590000
    m["trend"] = {"first_half": {"sessions": 10, "commits": 5, "landed": 2, "landed_ratio": 0.4,
                                 "override": 30, "error": 9, "short_ratio": 0.2},
                  "second_half": {"sessions": 12, "commits": 8, "landed": 6, "landed_ratio": 0.75,
                                  "override": 40, "error": 5, "short_ratio": 0.1}}
    return m


def test_kpi_strip_removed():
    # 满数据也不再渲染 KPI strip：容器、mini 图、「Token 总量」「落地率趋势」全不出现
    html = render_profile_report(_profile(), _meta(), _metrics_with_trend_token(), None)
    assert 'class="kpi-strip"' not in html
    assert "mini-donut" not in html and "mini-bars" not in html
    assert "Token 总量" not in html
    assert "落地率趋势" not in html
    # 速览信息仍在指标明细三族里
    assert "产出落地" in html


def test_trend_counts_normalized_per_session():
    # 计数类指标按「次/会话」密度呈现，会话数基数放表头——前后半段体量悬殊时
    # 原始计数对比全行无脑↑，密度才反映行为变化
    html = render_profile_report(_profile(), _meta(), _metrics_with_trend_token(), None)
    assert "前半段（10 会话）" in html
    assert "后半段（12 会话）" in html
    assert "纠偏锚点（次/会话）" in html
    assert "3.00" in html      # override 前半 30/10
    assert "3.33" in html      # override 后半 40/12
    assert "提交（次/会话）" in html
    assert "0.50" in html      # commits 前半 5/10
    assert "0.67" in html      # commits 后半 8/12
    # 比率类行保持百分数
    assert "40%" in html and "75%" in html


def test_trend_zero_sessions_no_crash():
    metrics = _metrics_with_trend_token()
    metrics["trend"]["first_half"]["sessions"] = 0
    html = render_profile_report(_profile(), _meta(), metrics, None)
    assert "前半段（0 会话）" in html


def test_stage_card_criteria_table():
    # 判据对照行：左判据文案，右「实际值 ✓/✗」。v2 阈值下 L3+L4=75%、广度 28 满足精通；
    # 压低落地率使其停在第3档，距第4档缺 落地率≥50% 列为 ✗
    metrics = dict(_metrics())
    metrics["landed_ratio"] = 0.3
    html = render_profile_report(_profile(), _meta(), metrics, None)
    assert "档位判据" in html
    assert "75%" in html        # L3+L4 实际
    assert "28 种" in html      # 工具广度实际
    assert 'class="crit-ok"' in html and "✓" in html
    assert "距下一阶段还差" in html
    assert 'class="crit-miss"' in html and "✗" in html
    assert "18%" in html        # 未达标判据也给实际值


def test_token_appendix_open_evidence_collapsed():
    # 附录 A Token 默认展开（details 带 open）；附录 B 证据链保持折叠
    html = render_profile_report(_profile(), _meta(), _metrics_with_trend_token(), None)
    tok_details = html.split("<summary>A · Token 消耗")[0].rsplit("<details", 1)[1]
    assert " open" in tok_details
    ev_details = html.split("<summary>B · 证据链")[0].rsplit("<details", 1)[1]
    assert " open" not in ev_details
    # open 是 HTML 属性不是 CSS 属性，打印样式里写 details{open:true} 是死 CSS
    assert "open:true" not in html


def test_fmt_tokens_billion_tier():
    from ai_coding_insights.report import _fmt_tokens
    assert _fmt_tokens(2745781011) == "2.75B"
    assert _fmt_tokens(10590000) == "10.6M"
    assert _fmt_tokens(999) == "999"


def test_pointer_missing_annotation_renders():
    # 规则层核验未命中的指针：报告中明示 ⚠，不装作可回看；命中的不带警示
    profile = _profile()
    profile["evidence"] = [
        {"pointer": "/abs/ok.jsonl#u1", "behavior": "命中的"},
        {"pointer": "/abs/fake.jsonl#u2", "behavior": "未命中的", "pointer_missing": True},
    ]
    profile["highlights"] = [
        {"pointer": "/abs/fake2.jsonl#u3", "behavior": "高光未命中", "pointer_missing": True},
    ]
    html = render_profile_report(profile, _meta(), _metrics(), None)
    assert html.count("⚠ 指针未命中") == 2
    ok_row = html.split("/abs/ok.jsonl#u1")[1][:80]
    assert "指针未命中" not in ok_row


def test_dashboard_scope_pill_personal_mode():
    meta = copy.deepcopy(META)
    meta["window"] = {"lookback_days": 30, "mode": "all"}
    html = render_profile_report(PROFILE, meta, METRICS, None)
    assert "个人模式" in html


def test_dashboard_scope_pill_team_mode():
    meta = copy.deepcopy(META)
    meta["window"] = {"lookback_days": 30, "mode": "include"}
    html = render_profile_report(PROFILE, meta, METRICS, None)
    assert "团队模式" in html


def test_dashboard_no_mode_renders_no_scope_pill():
    # 旧 _window.json 无 mode 键：整枚胶囊不渲染（向后兼容）
    html = render_profile_report(PROFILE, META, METRICS, None)
    assert "个人模式" not in html and "团队模式" not in html


def test_posture_card_anchoring_footnote():
    html = render_profile_report(_profile(), _meta(), _metrics(), None)
    assert "逐条语义分档" in html                  # v2 口径底注
    assert "AskUserQuestion 选项回答" in html      # L2 图例口径说明
    assert "极短输入" not in html                  # 旧口径文案不得残留


def test_friction_card_renders_pointer_chips():
    prof = dict(_profile())
    prof["frictions"] = [{
        "observation": "报错集中 —— 2/10 会话贡献全部 error 锚点",
        "suggestion": "贴报错前先读 traceback 末三行并写明怀疑点，以同类报错往返轮次下降为验证",
        "pointers": [{"pointer": "/tmp/s1.jsonl#u1"},
                     {"pointer": "/tmp/gone.jsonl#ux", "pointer_missing": True}],
    }]
    html = render_profile_report(prof, _meta(), _metrics(), None)
    assert "fr-ptrs" in html
    assert html.count("ptr-chip") >= 2
    assert "⚠ 指针未命中" in html


def test_friction_card_without_pointers_renders_clean():
    prof = dict(_profile())
    prof["frictions"] = [{"observation": "观察", "suggestion": "建议", "pointers": []}]
    html = render_profile_report(prof, _meta(), _metrics(), None)
    assert '<div class="fr-ptrs">' not in html   # 空指针不出空容器（CSS 规则恒在，只查 HTML 容器）
