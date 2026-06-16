import subprocess as sp
from datetime import datetime, timezone

import pytest

from ai_coding_insights.git_outcome import (attribute_by_overlap,
                                            normalize_to_repo_rel, repo_outcome,
                                            repo_root)
from ai_coding_insights.models import RepoOutcome


def test_attribute_by_overlap_basic():
    commits = [{"a.py", "b.py"}, {"c.py"}, {"d.py", "e.py"}]
    assert attribute_by_overlap(commits, {"a.py", "d.py"}) == RepoOutcome(2, 3)


def test_attribute_by_overlap_empty_edited():
    assert attribute_by_overlap([{"a.py"}], set()) == RepoOutcome(0, 1)


def test_attribute_by_overlap_no_commits():
    assert attribute_by_overlap([], {"a.py"}) == RepoOutcome(0, 0)


def test_normalize_to_repo_rel():
    assert normalize_to_repo_rel({"/repo/src/a.py", "/elsewhere/x.py"}, "/repo") == {"src/a.py"}


def _git(repo, *args):
    sp.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    sp.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    _git(tmp_path, "config", "user.email", "me@example.com")
    _git(tmp_path, "config", "user.name", "Me")
    _git(tmp_path, "commit", "--allow-empty", "-q", "-m", "c1")
    return tmp_path


def test_repo_outcome_file_overlap(repo):
    # 规范化仓库根（macOS /var→/private/var symlink），编辑路径与提交文件同口径比对。
    root = repo_root(str(repo))
    (repo / "a.py").write_text("1"); _git(repo, "add", "a.py")
    _git(repo, "-c", "user.email=me@x.com", "-c", "user.name=me", "commit", "-m", "c1")
    (repo / "z.py").write_text("1"); _git(repo, "add", "z.py")
    _git(repo, "-c", "user.email=me@x.com", "-c", "user.name=me", "commit", "-m", "c2")
    _git(repo, "config", "user.email", "me@x.com")
    since = datetime(2000, 1, 1, tzinfo=timezone.utc)
    r = repo_outcome(root, {root + "/a.py"}, since)
    assert r == RepoOutcome(landed_count=1, total_count=2)


def test_repo_outcome_non_git_failsafe(tmp_path):
    r = repo_outcome(str(tmp_path), {"/x/a.py"}, datetime(2000, 1, 1, tzinfo=timezone.utc))
    assert r == RepoOutcome(0, 0)
