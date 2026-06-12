import json, subprocess
from datetime import datetime, timezone, date
from pathlib import Path
from ai_coding_insights.discovery import (
    discover_sessions, detect_data_start, is_window_truncated,
)
from ai_coding_insights.models import RemoteRule

RULES = [RemoteRule("git.example.com")]

def _session_file(projects: Path, slug: str, cwd: str, ts: str):
    d = projects / slug
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{slug}.jsonl"
    f.write_text(json.dumps({"type":"user","sessionId":slug,"cwd":cwd,
        "timestamp":ts,"message":{"content":"hi"}}))
    return f

def test_discovery_filters_by_remote_and_lookback(tmp_path):
    projects = tmp_path / "projects"
    included = tmp_path / "included"; included.mkdir()
    subprocess.run(["git","init","-q"], cwd=included, check=True)
    subprocess.run(["git","remote","add","origin","git@git.example.com:team-x/x.git"],
                   cwd=included, check=True)
    personal = tmp_path / "personal"; personal.mkdir()
    subprocess.run(["git","init","-q"], cwd=personal, check=True)

    _session_file(projects, "co-recent", str(included), "2026-06-01T00:00:00Z")
    _session_file(projects, "co-old",    str(included), "2026-01-01T00:00:00Z")  # 过期
    _session_file(projects, "personal",  str(personal), "2026-06-01T00:00:00Z") # 不在纳入范围

    now = datetime(2026, 6, 9, tzinfo=timezone.utc)
    found = discover_sessions(projects, RULES, lookback_days=30, now=now)
    assert {s.session_id for s in found} == {"co-recent"}

def test_discovery_since_excludes_earlier_sessions(tmp_path):
    projects = tmp_path / "projects"
    included = tmp_path / "included"; included.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=included, check=True)
    subprocess.run(["git", "remote", "add", "origin", "git@git.example.com:team-x/x.git"],
                   cwd=included, check=True)
    _session_file(projects, "before-since", str(included), "2026-06-03T00:00:00Z")  # 早于 since
    _session_file(projects, "after-since",  str(included), "2026-06-07T00:00:00Z")  # 晚于 since
    now = datetime(2026, 6, 9, tzinfo=timezone.utc)
    since = datetime(2026, 6, 5, tzinfo=timezone.utc)
    found = discover_sessions(projects, RULES, lookback_days=30, now=now, since=since)
    assert {s.session_id for s in found} == {"after-since"}


def test_discovery_since_none_is_backward_compatible(tmp_path):
    projects = tmp_path / "projects"
    included = tmp_path / "included"; included.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=included, check=True)
    subprocess.run(["git", "remote", "add", "origin", "git@git.example.com:team-x/x.git"],
                   cwd=included, check=True)
    _session_file(projects, "a", str(included), "2026-06-03T00:00:00Z")
    _session_file(projects, "b", str(included), "2026-06-07T00:00:00Z")
    now = datetime(2026, 6, 9, tzinfo=timezone.utc)
    found = discover_sessions(projects, RULES, lookback_days=30, now=now, since=None)
    assert {s.session_id for s in found} == {"a", "b"}


def test_discovery_multiple_sessions_same_cwd(tmp_path):
    projects = tmp_path / "projects"
    included = tmp_path / "included"; included.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=included, check=True)
    subprocess.run(["git", "remote", "add", "origin", "git@git.example.com:team-x/x.git"],
                   cwd=included, check=True)
    _session_file(projects, "s1", str(included), "2026-06-01T00:00:00Z")
    _session_file(projects, "s2", str(included), "2026-06-02T00:00:00Z")
    now = datetime(2026, 6, 9, tzinfo=timezone.utc)
    found = discover_sessions(projects, RULES, lookback_days=30, now=now)
    assert {s.session_id for s in found} == {"s1", "s2"}


# ---- detect_data_start：全局最早可读 transcript 时间 ----

def test_detect_data_start_returns_global_earliest(tmp_path):
    projects = tmp_path / "projects"
    _session_file(projects, "a", "/x", "2026-05-12T08:00:00Z")
    _session_file(projects, "b", "/y", "2026-04-26T03:00:00Z")  # 全局最早
    _session_file(projects, "c", "/z", "2026-06-01T00:00:00Z")
    start = detect_data_start(projects)
    assert start is not None
    assert start.startswith("2026-04-26")


def test_detect_data_start_skips_bad_and_missing_timestamp(tmp_path):
    projects = tmp_path / "projects"
    good = projects / "good"; good.mkdir(parents=True)
    (good / "good.jsonl").write_text(
        json.dumps({"type": "user", "timestamp": "2026-05-10T00:00:00Z"}))
    bad = projects / "bad"; bad.mkdir(parents=True)
    # 第一行坏 JSON，第二行无 timestamp，应被静默跳过
    (bad / "bad.jsonl").write_text(
        "{not json\n" + json.dumps({"type": "user"}) + "\n")
    start = detect_data_start(projects)
    assert start is not None
    assert start.startswith("2026-05-10")


def test_detect_data_start_only_reads_head_not_whole_file(tmp_path):
    """早时间藏在文件深处（超过读取上限）时不应被读到——证明只读文件头。"""
    projects = tmp_path / "projects"
    d = projects / "big"; d.mkdir(parents=True)
    lines = [json.dumps({"type": "user", "timestamp": "2026-05-20T00:00:00Z"})
             for _ in range(50)]
    lines.append(json.dumps({"type": "user", "timestamp": "2026-01-01T00:00:00Z"}))
    (d / "big.jsonl").write_text("\n".join(lines))
    start = detect_data_start(projects)
    assert start is not None
    assert start.startswith("2026-05-20")  # 深处的 2026-01-01 不被读到


def test_detect_data_start_empty_dir_returns_none(tmp_path):
    projects = tmp_path / "projects"; projects.mkdir()
    assert detect_data_start(projects) is None


def test_detect_data_start_no_parsable_timestamp_returns_none(tmp_path):
    projects = tmp_path / "projects"
    d = projects / "p"; d.mkdir(parents=True)
    (d / "p.jsonl").write_text("garbage\n" + json.dumps({"type": "user"}))
    assert detect_data_start(projects) is None


# ---- is_window_truncated：纯函数边界 ----

def test_is_window_truncated_data_start_later_than_since():
    assert is_window_truncated(date(2026, 4, 26), "2026-05-12T00:00:00Z") is True


def test_is_window_truncated_data_start_earlier_than_since():
    assert is_window_truncated(date(2026, 5, 12), "2026-04-26T00:00:00Z") is False


def test_is_window_truncated_data_start_equals_since():
    assert is_window_truncated(date(2026, 5, 12), "2026-05-12T08:00:00Z") is False


def test_is_window_truncated_none_inputs():
    assert is_window_truncated(None, "2026-05-12T00:00:00Z") is False
    assert is_window_truncated(date(2026, 5, 12), None) is False
    assert is_window_truncated(None, None) is False


def test_discovery_excludes_session_without_parsable_timestamp(tmp_path):
    """无可解析时间戳的会话宁漏勿误：无法证明在窗口内，一律不纳入。"""
    import subprocess
    projects = tmp_path / "projects"
    included = tmp_path / "included"; included.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=included, check=True)
    subprocess.run(["git", "remote", "add", "origin", "git@git.example.com:team-x/x.git"],
                   cwd=included, check=True)
    _session_file(projects, "with-ts", str(included), "2026-06-01T00:00:00Z")
    d = projects / "no-ts"; d.mkdir(parents=True)
    (d / "no-ts.jsonl").write_text(json.dumps(
        {"type": "user", "sessionId": "no-ts", "cwd": str(included),
         "message": {"content": "hi"}}))  # 无 timestamp
    now = datetime(2026, 6, 9, tzinfo=timezone.utc)
    found = discover_sessions(projects, RULES, lookback_days=30, now=now)
    assert {s.session_id for s in found} == {"with-ts"}


def test_rules_none_includes_non_git_project(tmp_path):
    # mode=all：非 git 目录也纳入
    plain = tmp_path / "plain"; plain.mkdir()
    projects = tmp_path / "projects"
    _session_file(projects, "s1", str(plain), "2026-06-01T00:00:00Z")
    now = datetime(2026, 6, 10, tzinfo=timezone.utc)
    found = discover_sessions(projects, None, lookback_days=30, now=now)
    assert [s.session_id for s in found] == ["s1"]


def test_rules_none_skips_classify(tmp_path, monkeypatch):
    # mode=all 不得调用 git 归属判定
    import ai_coding_insights.discovery as discovery
    monkeypatch.setattr(discovery, "classify",
                        lambda *a: (_ for _ in ()).throw(AssertionError("不应调用 classify")))
    plain = tmp_path / "plain"; plain.mkdir()
    projects = tmp_path / "projects"
    _session_file(projects, "s1", str(plain), "2026-06-01T00:00:00Z")
    now = datetime(2026, 6, 10, tzinfo=timezone.utc)
    assert len(discover_sessions(projects, None, lookback_days=30, now=now)) == 1


def test_rules_none_still_respects_window(tmp_path):
    plain = tmp_path / "plain"; plain.mkdir()
    projects = tmp_path / "projects"
    _session_file(projects, "old", str(plain), "2026-01-01T00:00:00Z")
    now = datetime(2026, 6, 10, tzinfo=timezone.utc)
    assert discover_sessions(projects, None, lookback_days=30, now=now) == []


def test_detect_data_start_skips_long_untimestamped_header(tmp_path):
    """文件头若有超过 5 行的无 timestamp 元记录，仍能读到其后的真实时间。"""
    projects = tmp_path / "projects"
    d = projects / "p"; d.mkdir(parents=True)
    header = [json.dumps({"type": "summary"}) for _ in range(10)]
    header.append(json.dumps({"type": "user", "timestamp": "2026-05-01T00:00:00Z"}))
    (d / "p.jsonl").write_text("\n".join(header))
    start = detect_data_start(projects)
    assert start is not None and start.startswith("2026-05-01")
