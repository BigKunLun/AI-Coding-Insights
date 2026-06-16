from ai_coding_insights.snapshot import (save_snapshot, load_latest, diff_metrics,
                                         CURRENT_POSTURE_RUBRIC)


def test_save_and_load_roundtrip(tmp_path):
    p = save_snapshot(
        {"session_count": 3},
        {"L3": 0.6},
        {"landed": 34, "total": 43},
        "2026-06-09T10:00:00+00:00",
        {"days": 30},
        dir=tmp_path,
    )
    assert p.exists()
    assert p.name == "2026-06-09.json"

    data = load_latest(dir=tmp_path)
    assert data is not None
    assert data["metrics"]["session_count"] == 3
    assert data["posture_distribution"]["L3"] == 0.6
    assert data["outcome"]["landed"] == 34
    assert data["generated_at"] == "2026-06-09T10:00:00+00:00"
    assert data["window"] == {"days": 30}


def test_load_latest_picks_newest_and_before(tmp_path):
    save_snapshot({"session_count": 1}, {"L1": 1.0}, {"landed": 1, "total": 1},
                  "2026-06-01T00:00:00+00:00", {"days": 7}, dir=tmp_path)
    save_snapshot({"session_count": 9}, {"L3": 1.0}, {"landed": 9, "total": 9},
                  "2026-06-09T00:00:00+00:00", {"days": 7}, dir=tmp_path)

    latest = load_latest(dir=tmp_path)
    assert latest["metrics"]["session_count"] == 9

    before = load_latest(before="2026-06-09", dir=tmp_path)
    assert before["metrics"]["session_count"] == 1

    assert load_latest(before="2026-06-01", dir=tmp_path) is None


def test_load_latest_empty_dir_returns_none(tmp_path):
    assert load_latest(dir=tmp_path / "none") is None


def test_load_latest_corrupt_snapshot_degrades_to_none(tmp_path):
    """最新快照损坏（写一半）→ 按无基线降级，不抛异常。"""
    (tmp_path / "2026-06-01.json").write_text('{"generated_at": "2026-06-01T0', encoding="utf-8")
    assert load_latest(dir=tmp_path) is None


def test_load_latest_ignores_non_date_stem_files(tmp_path):
    """杂散 json（非 YYYY-MM-DD 文件名）不得参与最新快照排序。"""
    save_snapshot({"session_count": 1}, {}, {}, "2026-06-01T00:00:00+00:00", {}, dir=tmp_path)
    (tmp_path / "zzz-backup.json").write_text("{}", encoding="utf-8")
    loaded = load_latest(dir=tmp_path)
    assert loaded is not None and loaded["metrics"]["session_count"] == 1


def test_save_snapshot_leaves_no_tmp_file(tmp_path):
    save_snapshot({"a": 1}, {}, {}, "2026-06-02T00:00:00+00:00", {}, dir=tmp_path)
    assert not list(tmp_path.glob("*.tmp"))
    assert (tmp_path / "2026-06-02.json").exists()


def test_snapshot_posture_rubric_is_3(tmp_path):
    """双轴口径起，posture_rubric 推进到 3，拦截跨口径同比。"""
    save_snapshot(
        {},
        {"L1": 1.0, "L2": 0.0, "L3": 0.0, "L4": 0.0},
        {},
        "2026-06-15T00:00:00Z",
        {},
        dir=tmp_path,
    )
    snap = load_latest(dir=tmp_path)
    assert snap["posture_rubric"] == 3


def test_diff_suppresses_caliber_keys_on_rubric_change():
    """跨口径（prev rubric != 当前）：受口径影响的 git 指标不出 delta/箭头，
    按「无可比基线」处理——升级边界第一次 diff 不得拿旧口径 prev 算伪涨跌。"""
    prev = {"landed_ratio": 0.3, "git_landed_count": 2, "git_commit_total": 8,
            "session_count": 4}
    now = {"landed_ratio": 0.6, "git_landed_count": 6, "git_commit_total": 10,
           "session_count": 9}
    diff = diff_metrics(now, prev, prev_rubric=CURRENT_POSTURE_RUBRIC - 1)
    for k in ("landed_ratio", "git_landed_count", "git_commit_total"):
        assert diff[k]["no_base"] is True
        assert diff[k]["arrow"] is None
        assert diff[k]["delta"] is None
    # 不受口径影响的 key 照常同比
    assert diff["session_count"]["arrow"] == "↑"
    assert diff["session_count"]["delta"] == 5


def test_diff_same_rubric_compares_caliber_keys():
    """口径一致时受口径影响的 key 正常同比（默认行为不变）。"""
    prev = {"landed_ratio": 0.3, "git_landed_count": 2, "git_commit_total": 8}
    now = {"landed_ratio": 0.6, "git_landed_count": 6, "git_commit_total": 10}
    diff = diff_metrics(now, prev, prev_rubric=CURRENT_POSTURE_RUBRIC)
    assert diff["git_landed_count"]["arrow"] == "↑"
    assert diff["git_landed_count"]["delta"] == 4
