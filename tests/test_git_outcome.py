import subprocess as sp
from datetime import datetime, timedelta, timezone

import pytest

from ai_coding_insights.git_outcome import (attribute_commits, repo_outcome,
                                            repo_root, window_commit_times)
from ai_coding_insights.models import RepoOutcome

_T0 = datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)


def _t(minutes):
    return _T0 + timedelta(minutes=minutes)


def test_attribute_in_span_and_outside():
    spans = [(_t(0), _t(60))]
    r = attribute_commits([_t(30), _t(300)], spans)
    assert r == RepoOutcome(landed_count=1, outside_count=1)


def test_attribute_grace_boundary():
    spans = [(_t(0), _t(60))]
    # 默认宽限 ±30min：first-30 与 last+30 含端点命中，再远 1 分钟即窗外
    r = attribute_commits([_t(-30), _t(90), _t(-31), _t(91)], spans)
    assert r == RepoOutcome(landed_count=2, outside_count=2)


def test_attribute_empty():
    assert attribute_commits([], [(_t(0), _t(60))]) == RepoOutcome(0, 0)
    assert attribute_commits([_t(10)], []) == RepoOutcome(0, 1)


def _git(repo, *args):
    sp.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    sp.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    _git(tmp_path, "config", "user.email", "me@example.com")
    _git(tmp_path, "config", "user.name", "Me")
    _git(tmp_path, "commit", "--allow-empty", "-q", "-m", "c1")
    return tmp_path


def test_window_commit_times_filters_author(repo):
    _git(repo, "commit", "--allow-empty", "-q", "-m", "c2",
         "--author", "Other <other@example.com>")
    since = datetime(2000, 1, 1, tzinfo=timezone.utc)
    times = window_commit_times(str(repo), since)
    assert len(times) == 1                      # 只算 me@example.com 的 c1
    assert times[0].tzinfo is not None


def test_window_commit_times_failsafe(tmp_path):
    since = datetime(2000, 1, 1, tzinfo=timezone.utc)
    assert window_commit_times(str(tmp_path), since) == []      # 非 git 目录


def test_repo_root_subdir_and_failsafe(repo, tmp_path):
    sub = repo / "sub"
    sub.mkdir()
    assert repo_root(str(sub)) == str(repo)
    assert repo_root(str(tmp_path / "nope")) is None


def test_repo_outcome_end_to_end(repo):
    since = datetime(2000, 1, 1, tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    r = repo_outcome(str(repo), [(now - timedelta(hours=1), now)], since)
    assert r == RepoOutcome(landed_count=1, outside_count=0)
    r2 = repo_outcome(str(repo), [], since)
    assert r2 == RepoOutcome(landed_count=0, outside_count=1)
