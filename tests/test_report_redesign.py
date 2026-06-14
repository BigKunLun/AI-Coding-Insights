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
