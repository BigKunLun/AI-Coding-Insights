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


def test_posture_fullwidth_no_two_col_grid():
    from ai_coding_insights.report import render_profile_report
    html = render_profile_report(_min_profile(), _min_meta(), metrics=None)
    assert "posture-grid" not in html
    assert "posture-full" in html
