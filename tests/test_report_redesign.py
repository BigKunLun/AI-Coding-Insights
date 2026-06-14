from ai_coding_insights.report import _hl_nums


def test_hl_nums_wraps_plain_integer():
    out = _hl_nums("后台委托 292 次", "#4f46e5")
    assert '<span class="n" style="color:#4f46e5">292</span>' in out
    assert "后台委托" in out and "次" in out


def test_hl_nums_wraps_percent_and_decimal():
    assert '<span class="n" style="color:#0d9488">92%</span>' in _hl_nums("落地率 92%", "#0d9488")
    assert '<span class="n" style="color:#0d9488">0.918</span>' in _hl_nums("存活率 0.918", "#0d9488")


def test_hl_nums_skips_version_date_identifier():
    # 版本号/日期/带连字标识符内的数字不得高亮
    assert "<span" not in _hl_nums("CC 2.1.141", "#7c3aed")
    assert "<span" not in _hl_nums("2026-06-14", "#7c3aed")
    assert "<span" not in _hl_nums("claude-opus-4-8", "#7c3aed")


def test_hl_nums_escapes_html():
    assert "&lt;" in _hl_nums("a<b 3 次", "#4f46e5")


def test_hl_nums_thousands_separator():
    out = _hl_nums("token 1,234 个", "#4f46e5")
    assert '<span class="n" style="color:#4f46e5">1,234</span>' in out


def test_hl_nums_no_trailing_comma_swallow():
    out = _hl_nums("委托 5, 落地 3", "#4f46e5")
    # 句中逗号不得被吞进 span
    assert '5,</span>' not in out
    assert '<span class="n" style="color:#4f46e5">5</span>' in out
    assert '<span class="n" style="color:#4f46e5">3</span>' in out


def test_hl_nums_plain_four_digits():
    out = _hl_nums("耗时 1440 分钟", "#4f46e5")
    assert '<span class="n" style="color:#4f46e5">1440</span>' in out


def test_hl_nums_empty_and_none():
    assert _hl_nums("", "#4f46e5") == ""
    assert _hl_nums(None, "#4f46e5") == ""


from ai_coding_insights.report import _dim_points_rows


def test_dim_points_no_bold_wall_and_highlights_number():
    html = _dim_points_rows(["后台委托 292 次 —— 分布在 52 个会话"], "#4f46e5")
    # 导语数字高亮
    assert '<span class="n" style="color:#4f46e5">292</span>' in html
    # 不再有整段加粗的 pt-title（旧实现走 font-weight:700 的 .pt-title）
    assert 'pt-title' not in html
    # 展开段走淡灰 class
    assert 'dim2' in html


def test_dim_points_qualitative_no_number_ok():
    html = _dim_points_rows(["质疑成主导姿态，对幻觉有稳定警觉"], "#7c3aed")
    assert "质疑成主导姿态" in html


def test_radar_dim_desc_no_double_escape():
    from ai_coding_insights.report import render_profile_report
    prof = {
        "posture_distribution": {"L1": 0.4, "L2": 0.2, "L3": 0.3, "L4": 0.1},
        "breadth": {"headline": "广度 & 深度 <对比>", "points": []},
        "depth": {"headline": "h", "points": []},
        "outcome": {"headline": "h", "points": [], "landed": 1, "total": 2},
        "evidence": [{"pointer": "/a.jsonl#u", "behavior": "x"}],
    }
    meta = {"generated_at": "2026-06-14T09:29:00", "included_projects": [], "run": None}
    html = render_profile_report(prof, meta, metrics=None)
    assert "&amp;amp;" not in html          # 不得双重转义
    assert "&amp;" in html                   # 单次转义后的 & 仍在


def _min_profile():
    return {
        "posture_distribution": {"L1": 0.4, "L2": 0.2, "L3": 0.29, "L4": 0.11},
        "breadth": {"headline": "h", "points": ["工具广度 43 种 —— 高频 Bash 372"]},
        "depth": {"headline": "h", "points": ["override 393 次 —— 远超 error 173"]},
        "outcome": {"headline": "h", "points": ["落地 416 —— 后半窗起量"], "landed": 416, "total": 453},
        "evidence": [{"pointer": "/a.jsonl#u", "behavior": "纠正方法论"}],
    }


def _min_meta():
    return {"generated_at": "2026-06-14T09:29:00", "included_projects": [], "run": None}


def test_section_order():
    from ai_coding_insights.report import render_profile_report
    prof = _min_profile()
    prof["highlights"] = [{"pointer": "/a.jsonl#u", "behavior": "凭框架推翻方案 3 次"}]
    html = render_profile_report(prof, _min_meta(), metrics=None)
    # 锚定渲染出的章节标题（sec-title span），避免被 CSS 注释里的同名字符串干扰
    def pos(s): return html.find(f'<span class="sec-title">{s}')
    # 姿势 < 四维 < 高光（高光已下沉到四维之后）
    assert -1 < pos("姿势分布") < pos("四维画像") < pos("高光时刻")


def test_posture_fullwidth_no_two_col_grid():
    from ai_coding_insights.report import render_profile_report
    html = render_profile_report(_min_profile(), _min_meta(), metrics=None)
    assert "posture-grid" not in html
    assert "posture-full" in html


def test_posture_bseg_white_text_on_l4_regardless_of_position():
    from ai_coding_insights.report import _POSTURE_COLORS, render_profile_report
    prof = _min_profile()
    prof["posture_distribution"] = {"L1": 0.0, "L2": 0.0, "L3": 0.6, "L4": 0.4}
    html = render_profile_report(prof, _min_meta(), metrics=None)
    # L4 段即便落在首位也必须白字（按身份着色，非 nth-child 位置）
    assert f"background:{_POSTURE_COLORS['L4']};color:#ffffff" in html


from ai_coding_insights.report import _render_highlights_section


def test_highlights_highlights_numbers_and_no_blackbold():
    hl = [{"pointer": "/a.jsonl#u", "behavior": "凭框架机制推翻方案，AI 采纳重做，省去 3 轮返工"}]
    html = _render_highlights_section(hl, [], 5)
    assert '<span class="n"' in html  # 数字高亮
    assert "原会话" in html


from ai_coding_insights.report import _timeline_bars


def test_timeline_bars_height_and_bucket():
    daily = [{"date": "2026-06-12", "session_count": 14},
             {"date": "2026-06-13", "session_count": 43},
             {"date": "2026-06-14", "session_count": 1}]
    bars = _timeline_bars(daily)
    assert len(bars) == 3
    peak = max(bars, key=lambda b: b["count"])
    assert peak["date"] == "2026-06-13" and peak["count"] == 43
    assert peak["height_pct"] == 100.0          # 峰值满高
    assert all(0 < b["height_pct"] <= 100 for b in bars)
    assert peak["color"] == "#1a6b5a"           # 21+ 档最深
    assert next(b for b in bars if b["count"] == 1)["color"] == "#c6e7da"  # 1-5 档


def test_timeline_bars_empty():
    assert _timeline_bars([]) == []
    assert _timeline_bars(None) == []


def test_timeline_bars_bucket_boundaries():
    daily = [{"date": f"2026-06-{d:02d}", "session_count": c} for d, c in
             [(1, 5), (2, 6), (3, 12), (4, 13), (5, 20), (6, 21)]]
    by_count = {b["count"]: b["color"] for b in _timeline_bars(daily)}
    assert by_count[5] == "#c6e7da"
    assert by_count[6] == "#6fc9b0"
    assert by_count[12] == "#6fc9b0"
    assert by_count[13] == "#2d9d7e"
    assert by_count[20] == "#2d9d7e"
    assert by_count[21] == "#1a6b5a"


def test_timeline_bars_zero_count_bucket():
    bars = _timeline_bars([{"date": "2026-06-01", "session_count": 0},
                           {"date": "2026-06-02", "session_count": 10}])
    z = next(b for b in bars if b["count"] == 0)
    assert z["color"] == "#ebedf0"
    assert z["height_pct"] == 0.0


def test_timeline_bars_sorts_ascending():
    bars = _timeline_bars([{"date": "2026-06-14", "session_count": 1},
                           {"date": "2026-06-01", "session_count": 2},
                           {"date": "2026-06-07", "session_count": 3}])
    assert [b["date"] for b in bars] == ["2026-06-01", "2026-06-07", "2026-06-14"]


from ai_coding_insights.report import _bar_items, _render_tool_skill_mcp_appendix


def test_bar_items_topn():
    counts = {f"t{i}": 100 - i for i in range(20)}
    items, mx = _bar_items(counts, top_n=10)
    assert len(items) == 10 and mx == 100


def test_tok_row_three_columns_value_outside_bar():
    html = _render_tool_skill_mcp_appendix({"Bash": 372, "Write": 278}, None, None)
    # 数值在独立列、不再嵌在 bar-wrap 里
    assert '<span class="tok-val">372</span>' in html
    assert '<span class="tok-bar-wrap"><span class="tok-bar"' in html  # bar-wrap 只含填充
    assert "Top 10" in html  # 工具标题改 10


from ai_coding_insights.report import _render_health_section


def test_health_card_has_padding_class():
    # cc_version_span 三字段齐全且无 drift_flags → 应含 health-card、新文案、distinct 被高亮、跨度内未见漂移
    html = _render_health_section(
        {"cc_version_span": {"min": "2.1.141", "max": "2.1.177", "distinct": 26}}, 9)
    assert "health-card" in html
    assert html.count("数据健康") == 1        # 章节头已含，卡内不再重复标题
    assert "数据横跨" in html
    assert '<span class="n"' in html        # 版本数 26 被高亮
    assert "未见解析漂移" in html             # 无 flags → 补「未见漂移」文案
    # 版本号 min/max 走 escape，不该当数字高亮
    assert "2.1.141" in html and "2.1.177" in html


def test_tok_bar_fill_block_and_width():
    # 整页审查发现：tok-bar 是行内 span，行内盒子忽略 width/height → 彩条填充不可见。
    # 必须 display:block 才能让 width:N% 渲染出可见填充。
    from ai_coding_insights.report import render_profile_report
    html = render_profile_report(
        _min_profile(), _min_meta(),
        metrics={"tool_session_counts": {"Bash": 372, "Write": 278}})
    assert ".tok-bar{display:block" in html              # 填充块级化（修不可见 bug）
    assert '<span class="tok-bar" style="width:' in html  # 填充带宽度


def test_full_render_timeline_not_heatmap():
    # 整页防回归：活动热力是时间线柱状（tl-bar），旧错位日历（heatmap/h-cell）不得复活。
    from ai_coding_insights.report import render_profile_report
    html = render_profile_report(
        _min_profile(), _min_meta(),
        metrics={"daily": [{"date": "2026-06-12", "session_count": 5},
                           {"date": "2026-06-13", "session_count": 9}],
                 "parse_health": {"cc_version_span": {"min": "2.1.1", "max": "2.1.9", "distinct": 4}}})
    assert 'class="tl-bar' in html and 'class="tl-wrap"' in html  # 时间线柱
    # 旧错位日历 class 不得复活（带点精确匹配，避免 .depth-cell 等子串误伤）
    assert ".heatmap" not in html
    assert ".h-cell" not in html and ".h-grid" not in html
    assert ".health-card{padding" in html                # 数据健康卡有内边距（修贴边）
