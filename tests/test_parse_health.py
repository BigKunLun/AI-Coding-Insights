from ai_coding_insights.models import ParsedSession
from ai_coding_insights.parse_health import compute_parse_health, _version_key


def _ps(version, types=None, thinking=0, commits=None, plan=0,
        turns=("hi",), models=("m",)):
    from ai_coding_insights.models import UserTurn
    return ParsedSession(
        file_path="f", session_id="s", cwd="/r", git_branch="main",
        user_turns=[UserTurn(uuid="u", text=t, timestamp="2025-01-01T00:00:00Z") for t in turns],
        tools_used=["Bash"], models_used=list(models),
        first_ts="2025-01-01T00:00:00Z", last_ts="2025-01-01T00:00:00Z",
        thinking_block_count=thinking, commits=commits or [], plan_mode_count=plan,
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


def test_signal_presence_rates():
    sessions = [_ps("2.1.150", thinking=5), _ps("2.1.150", thinking=0)]
    h = compute_parse_health(sessions)
    assert h["signal_presence"]["thinking"] == 0.5
    assert h["signal_presence"]["humanturn"] == 1.0


def test_drift_cliff_flags_drop_only():
    # 老段（≤158）plan 普遍存在，新段（≥169）全掉零 → 报漂移
    old = [_ps("2.1.150", plan=1) for _ in range(12)]
    new = [_ps("2.1.172", plan=0) for _ in range(12)]
    h = compute_parse_health(old + new)
    flags = {f["signal"] for f in h["drift_flags"]}
    assert "plan" in flags


def test_drift_cliff_does_not_flag_growth():
    # gitop 老段无、新段有（新增特性）→ 不报（只报"掉"不报"涨"）
    old = [_ps("2.1.150", commits=[]) for _ in range(12)]
    new = [_ps("2.1.172", commits=[object()]) for _ in range(12)]
    h = compute_parse_health(old + new)
    assert all(f["signal"] != "gitop" for f in h["drift_flags"])


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
    h = compute_parse_health([_ps("2.1.150")])
    for sig in ("optionpick", "skill", "mcp", "background", "parallel"):
        assert sig in h["signal_presence"], sig
