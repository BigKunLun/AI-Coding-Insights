"""测试 _compute_daily() 和 compute_concurrency()。"""
from datetime import datetime, timezone, timedelta
from ai_coding_insights.signals import _compute_daily, compute_concurrency
from ai_coding_insights.models import ParsedSession, UserTurn, SessionStats, OutcomeStats


def _session(ts_strs, first_ts=None, last_ts=None, turn_count=3):
    """构造最小 ParsedSession 用于信号测试。"""
    turns = [UserTurn(uuid=f"u{i}", text=f"msg {i}", timestamp=ts_strs[i] if i < len(ts_strs) else ts_strs[0])
             for i in range(1)]  # 至少 1 个 turn
    return ParsedSession(
        file_path="/f.jsonl", session_id="s1", cwd="/repo", git_branch="main",
        user_turns=[UserTurn(uuid="u0", text="hello", timestamp=ts_strs[0])],
        tools_used=[], models_used=[], first_ts=first_ts or ts_strs[0],
        last_ts=last_ts or ts_strs[-1], plan_mode_count=0,
        skill_names=[], mcp_servers=[],
    )


def _stats(turn_count=3, short_turn_count=0):
    return SessionStats(session_id="s1", cwd="/repo", turn_count=turn_count,
                        short_turn_ratio=short_turn_count / turn_count if turn_count else 0.0,
                        duration_seconds=300, tools_used=[], models_used=[],
                        short_turn_count=short_turn_count)


def _outcome(commits=0, landed=0, edits=0):
    return OutcomeStats(session_id="s1", cwd="/repo", commit_count=commits,
                        landed_count=landed, edit_count=edits)


# ---- _compute_daily ----

def test_daily_empty():
    assert _compute_daily([], [], []) == []


def test_daily_single_day():
    ts1 = "2025-06-01T10:00:00Z"
    ts2 = "2025-06-01T14:00:00Z"
    sessions = [_session([ts1]), _session([ts2])]
    stats = [_stats(3), _stats(5)]
    outcomes = [_outcome(1, 1, 10), _outcome(2, 1, 20)]
    result = _compute_daily(sessions, stats, outcomes)
    assert len(result) == 1
    d = result[0]
    assert d["date"] == "2025-06-01"
    assert d["session_count"] == 2
    assert d["human_input_count"] == 8
    assert d["commit_count"] == 3
    assert d["landed_count"] == 2
    assert d["edit_count"] == 30


def test_daily_multi_day_sorted():
    sessions = [
        _session(["2025-06-03T10:00:00Z"]),
        _session(["2025-06-01T10:00:00Z"]),
        _session(["2025-06-01T14:00:00Z"]),
        _session(["2025-06-02T09:00:00Z"]),
    ]
    stats = [_stats(2) for _ in range(4)]
    outcomes = [_outcome(0, 0, 0) for _ in range(4)]
    result = _compute_daily(sessions, stats, outcomes)
    assert len(result) == 3
    assert [d["date"] for d in result] == ["2025-06-01", "2025-06-02", "2025-06-03"]
    assert result[0]["session_count"] == 2
    assert result[1]["session_count"] == 1
    assert result[2]["session_count"] == 1


def test_daily_missing_timestamp():
    sessions = [
        _session(["2025-06-01T10:00:00Z"]),
        ParsedSession(file_path="/f2.jsonl", session_id="s2", cwd="/repo",
                      git_branch="main", user_turns=[], tools_used=[],
                      models_used=[], first_ts=None, last_ts=None),  # 无时间戳
    ]
    stats = [_stats(3), _stats(5)]
    outcomes = [_outcome(1, 1, 5), _outcome(0, 0, 0)]
    result = _compute_daily(sessions, stats, outcomes)
    assert len(result) == 1  # 只有第一个参与


# ---- compute_concurrency ----

def _make_ts(iso):
    return iso

def test_concurrency_empty():
    assert compute_concurrency([]) == (1, 0)


def test_concurrency_single_session():
    sessions = [_session(["2025-06-01T10:00:00Z"], last_ts="2025-06-01T10:30:00Z")]
    assert compute_concurrency(sessions) == (1, 0)


def test_concurrency_no_overlap():
    sessions = [
        _session(["2025-06-01T10:00:00Z"], first_ts="2025-06-01T10:00:00Z",
                 last_ts="2025-06-01T10:30:00Z"),
        _session(["2025-06-01T11:00:00Z"], first_ts="2025-06-01T11:00:00Z",
                 last_ts="2025-06-01T11:30:00Z"),
    ]
    assert compute_concurrency(sessions) == (1, 0)


def test_concurrency_overlap_above_threshold():
    """两个会话重叠 600s ≥ 300s 阈值 → 并发=2。"""
    sessions = [
        _session(["2025-06-01T10:00:00Z"], first_ts="2025-06-01T10:00:00Z",
                 last_ts="2025-06-01T10:20:00Z"),
        _session(["2025-06-01T10:10:00Z"], first_ts="2025-06-01T10:10:00Z",
                 last_ts="2025-06-01T10:30:00Z"),
    ]
    # 变换后：[10:05, 10:20] vs [10:15, 10:30] → 重叠 5 分钟 ≥ 300s
    max_c, days = compute_concurrency(sessions, overlap_threshold_sec=300)
    assert max_c == 2
    assert days == 1


def test_concurrency_overlap_below_threshold():
    """两个会话重叠仅 60s < 300s 阈值 → 不算并发。"""
    sessions = [
        _session(["2025-06-01T10:00:00Z"], first_ts="2025-06-01T10:00:00Z",
                 last_ts="2025-06-01T10:10:00Z"),
        _session(["2025-06-01T10:09:00Z"], first_ts="2025-06-01T10:09:00Z",
                 last_ts="2025-06-01T10:20:00Z"),
    ]
    max_c, days = compute_concurrency(sessions, overlap_threshold_sec=300)
    assert max_c == 1
    assert days == 0


def test_concurrency_three_way():
    """三个会话两两重叠 ≥ 300s。"""
    sessions = [
        _session(["2025-06-01T10:00:00Z"], first_ts="2025-06-01T10:00:00Z",
                 last_ts="2025-06-01T10:20:00Z"),
        _session(["2025-06-01T10:08:00Z"], first_ts="2025-06-01T10:08:00Z",
                 last_ts="2025-06-01T10:30:00Z"),
        _session(["2025-06-01T10:15:00Z"], first_ts="2025-06-01T10:15:00Z",
                 last_ts="2025-06-01T10:40:00Z"),
    ]
    max_c, days = compute_concurrency(sessions, overlap_threshold_sec=300)
    assert max_c == 3
    assert days == 1


def test_concurrency_missing_timestamps():
    """部分会话缺时间戳应被跳过，不崩溃。"""
    sessions = [
        _session(["2025-06-01T10:00:00Z"], first_ts="2025-06-01T10:00:00Z",
                 last_ts="2025-06-01T10:30:00Z"),
        ParsedSession(file_path="/f2.jsonl", session_id="s2", cwd="/repo",
                      git_branch="main", user_turns=[], tools_used=[],
                      models_used=[], first_ts=None, last_ts=None),  # 无时间戳
    ]
    max_c, days = compute_concurrency(sessions)
    assert max_c == 1
    assert days == 0


def test_concurrency_too_short_session():
    """会话 < 300s → 变换后区间无效，不参与扫描线。"""
    sessions = [
        _session(["2025-06-01T10:00:00Z"], first_ts="2025-06-01T10:00:00Z",
                 last_ts="2025-06-01T10:04:00Z"),  # 只有 240s
        _session(["2025-06-01T10:02:00Z"], first_ts="2025-06-01T10:02:00Z",
                 last_ts="2025-06-01T10:30:00Z"),
    ]
    max_c, days = compute_concurrency(sessions, overlap_threshold_sec=300)
    assert max_c == 1  # 短会话被过滤，仅剩一个有效区间
