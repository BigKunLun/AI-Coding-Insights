from ai_coding_insights.models import ParsedSession
from ai_coding_insights.parse_health import compute_parse_health, _version_key


def _ps(version, types=None, thinking=0, commits=None, plan=0,
        turns=("hi",), models=("m",), edits=0, tools=("Bash",)):
    from ai_coding_insights.models import UserTurn
    return ParsedSession(
        file_path="f", session_id="s", cwd="/r", git_branch="main",
        user_turns=[UserTurn(uuid="u", text=t, timestamp="2025-01-01T00:00:00Z") for t in turns],
        tools_used=list(tools), models_used=list(models),
        first_ts="2025-01-01T00:00:00Z", last_ts="2025-01-01T00:00:00Z",
        thinking_block_count=thinking, commits=commits or [], plan_mode_count=plan,
        edit_count=edits,
        cc_versions=[version], record_type_counts=(types or {"user": 1}))


def test_version_key_sorts_numerically():
    assert _version_key("2.1.99") < _version_key("2.1.100")   # 数值序非字典序


def test_version_span():
    h = compute_parse_health([_ps("2.1.142"), _ps("2.1.175"), _ps("2.1.158")])
    assert h["cc_version_span"]["min"] == "2.1.142"
    assert h["cc_version_span"]["max"] == "2.1.175"
    assert h["cc_version_span"]["distinct"] == 3


def test_unknown_record_types_surfaced():
    h = compute_parse_health([_ps("2.1.150", types={"user": 1, "brand-new-type": 2})])
    assert "brand-new-type" in h["unknown_record_types"]
    assert "user" not in h["unknown_record_types"]   # 已知类型不报


def test_drift_cliff_flags_drop_only():
    # 老段（≤158）plan 普遍存在，新段（≥169）全掉零 → 报漂移
    old = [_ps("2.1.150", plan=1) for _ in range(12)]
    new = [_ps("2.1.172", plan=0) for _ in range(12)]
    h = compute_parse_health(old + new)
    flags = {f["signal"] for f in h["drift_flags"]}
    assert "plan" in flags


def test_drift_cliff_flags_growth_as_surge():
    # 【口径变更】旧实现只报"掉"不报"涨"；但版本升级同样会让信号虚高（如一条记录被拆成
    # 两条），硬指标口径被污染却无感知。极端突增（老段绝迹 → 新段普遍）现在报 surge。
    old = [_ps("2.1.150", commits=[]) for _ in range(12)]
    new = [_ps("2.1.172", commits=[object()]) for _ in range(12)]
    h = compute_parse_health(old + new)
    hit = [f for f in h["drift_flags"] if f["signal"] == "gitop"]
    assert len(hit) == 1
    assert hit[0]["kind"] == "surge"
    assert hit[0]["older_rate"] == 0.0
    assert hit[0]["newer_rate"] == 1.0


def test_drift_cliff_skips_thin_buckets():
    # 每段会话数 < min_bucket → 不判断崖（防薄数据误报）
    old = [_ps("2.1.150", plan=1) for _ in range(3)]
    new = [_ps("2.1.172", plan=0) for _ in range(3)]
    h = compute_parse_health(old + new)
    assert h["drift_flags"] == []


def test_same_version_does_not_false_flag_drift():
    # 30 个会话全在同一版本，但前 15 有 plan、后 15 无——旧的「会话序中点」切分会把
    # 同一版本内的采样差异误报成版本漂移。按版本边界切分：单一版本不可能漂移。
    sessions = ([_ps("2.1.158", plan=1) for _ in range(15)]
                + [_ps("2.1.158", plan=0) for _ in range(15)])
    h = compute_parse_health(sessions)
    assert h["drift_flags"] == []


def test_new_fragile_signals_watched():
    # 雷达必须监视新增的易碎信号（最依赖内部嵌套形态、最易随版本静默失效）
    from ai_coding_insights.parse_health import _SIGNAL_PREDS
    for sig in ("optionpick", "skill", "mcp", "background", "parallel"):
        assert sig in _SIGNAL_PREDS, sig


# ---------- drop 条目的既有契约（下游 SKILL.md 依赖，不得破坏）----------

def test_drop_entry_keeps_legacy_fields_and_gains_kind():
    old = [_ps("2.1.150", plan=1) for _ in range(12)]
    new = [_ps("2.1.172", plan=0) for _ in range(12)]
    h = compute_parse_health(old + new)
    hit = [f for f in h["drift_flags"] if f["signal"] == "plan"][0]
    assert hit["older_rate"] == 1.0        # 既有字段名与语义不变
    assert hit["newer_rate"] == 0.0
    assert hit["kind"] == "drop"           # 新增字段只做增量


def test_every_drift_flag_has_signal_and_kind():
    # 下游要按 signal 列名、按 kind 分文案；任一条目缺字段即契约破裂
    old = [_ps("2.1.150", plan=1, commits=[], edits=2) for _ in range(12)]
    new = [_ps("2.1.172", plan=0, commits=[object()], edits=9) for _ in range(12)]
    h = compute_parse_health(old + new)
    assert h["drift_flags"]
    for f in h["drift_flags"]:
        assert f["signal"]
        assert f["kind"] in ("drop", "surge", "shift")
        # 统一核心字段：三类条目都带存在率，渲染层无需分支即可出数
        assert isinstance(f["older_rate"], float)
        assert isinstance(f["newer_rate"], float)


# ---------- surge：对称的「虚高」方向 ----------

def test_surge_ignores_mild_growth_by_default():
    # 老段已有 2/12（16.7%）存在率 → 不是"从无到有"，属正常使用量变化，默认不报
    old = [_ps("2.1.150", commits=[object()] if i < 2 else []) for i in range(12)]
    new = [_ps("2.1.172", commits=[object()]) for _ in range(12)]
    h = compute_parse_health(old + new)
    assert all(f["signal"] != "gitop" for f in h["drift_flags"])


def test_surge_thresholds_are_parameters():
    # 放宽 surge 门槛后同一份数据即被报出 → 阈值确实是可调参数而非硬编码
    old = [_ps("2.1.150", commits=[object()] if i < 2 else []) for i in range(12)]
    new = [_ps("2.1.172", commits=[object()]) for _ in range(12)]
    h = compute_parse_health(old + new, surge_absent_thresh=0.2,
                             surge_present_thresh=0.5)
    hit = [f for f in h["drift_flags"] if f["signal"] == "gitop"]
    assert hit and hit[0]["kind"] == "surge"


def test_surge_skips_thin_buckets():
    old = [_ps("2.1.150", commits=[]) for _ in range(3)]
    new = [_ps("2.1.172", commits=[object()]) for _ in range(3)]
    assert compute_parse_health(old + new)["drift_flags"] == []


# ---------- shift：数值型量级偏移 ----------

def test_shift_flags_median_inflation():
    # edit_count 每会话中位数 2 → 8（4 倍）：典型"一条记录被拆成两条"式虚高
    old = [_ps("2.1.150", edits=2) for _ in range(12)]
    new = [_ps("2.1.172", edits=8) for _ in range(12)]
    h = compute_parse_health(old + new)
    hit = [f for f in h["drift_flags"] if f["signal"] == "edit"][0]
    assert hit["kind"] == "shift"
    assert hit["older_median"] == 2
    assert hit["newer_median"] == 8
    assert hit["median_ratio"] == 4.0


def test_shift_flags_median_deflation():
    # 反方向：量级腰斩同样是口径污染
    old = [_ps("2.1.150", edits=8) for _ in range(12)]
    new = [_ps("2.1.172", edits=2) for _ in range(12)]
    hit = [f for f in compute_parse_health(old + new)["drift_flags"]
           if f["signal"] == "edit"]
    assert hit and hit[0]["kind"] == "shift"


def test_shift_ignores_mild_ratio():
    # 1.5 倍在采样波动范围内 → 默认不报（宁可漏报也不误报）
    old = [_ps("2.1.150", edits=2) for _ in range(12)]
    new = [_ps("2.1.172", edits=3) for _ in range(12)]
    assert compute_parse_health(old + new)["drift_flags"] == []


def test_shift_uses_median_not_mean():
    # 新段一个 500 次编辑的长尾会话把均值拉到 20 倍，但中位数不动 → 不报
    old = [_ps("2.1.150", edits=2) for _ in range(12)]
    new = [_ps("2.1.172", edits=2) for _ in range(11)] + [_ps("2.1.172", edits=500)]
    assert compute_parse_health(old + new)["drift_flags"] == []


def test_shift_skips_thin_nonzero_buckets():
    # 老段只有 5 个会话真的有 edit（非零样本 < min_bucket）→ 中位数不可信，不判
    old = ([_ps("2.1.150", edits=2) for _ in range(5)]
           + [_ps("2.1.150", edits=0) for _ in range(7)])
    new = [_ps("2.1.172", edits=8) for _ in range(12)]
    assert compute_parse_health(old + new)["drift_flags"] == []


def test_shift_ratio_thresh_is_parameter():
    old = [_ps("2.1.150", edits=2) for _ in range(12)]
    new = [_ps("2.1.172", edits=3) for _ in range(12)]
    h = compute_parse_health(old + new, shift_ratio_thresh=1.4)
    assert [f["signal"] for f in h["drift_flags"]] == ["edit"]


def test_shift_not_duplicated_when_already_drop_or_surge():
    # 同一信号既掉零又"数值变化"时只出一条，避免下游把 N 个信号数重
    old = [_ps("2.1.150", edits=6) for _ in range(12)]
    new = [_ps("2.1.172", edits=0) for _ in range(12)]
    sigs = [f["signal"] for f in compute_parse_health(old + new)["drift_flags"]]
    assert sigs.count("edit") == 1


def test_same_version_does_not_false_flag_shift():
    # 版本切分逻辑复用：单一版本内的量级差异不是漂移
    sessions = ([_ps("2.1.158", edits=2) for _ in range(15)]
                + [_ps("2.1.158", edits=20) for _ in range(15)])
    assert compute_parse_health(sessions)["drift_flags"] == []


def test_hard_metric_signals_covered_by_numeric_radar():
    # 与奖惩挂钩的硬指标（编辑量、提交量）必须进数值雷达，否则口径被污染无人知
    from ai_coding_insights.parse_health import _SIGNAL_NUMS
    for sig in ("edit", "gitop", "turn", "thinking"):
        assert sig in _SIGNAL_NUMS, sig
