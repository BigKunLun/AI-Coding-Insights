from ai_coding_insights.repo_identity import parse_remote_url, matches, classify, remote_identities
from ai_coding_insights.models import RemoteRule, RemoteIdentity


def test_parse_ssh():
    assert parse_remote_url("git@git.example.com:team-x/foo.git") == RemoteIdentity("git.example.com", "team-x")


def test_parse_https_with_port_and_user():
    assert parse_remote_url("https://u@github.com:443/example-org/bar.git") == RemoteIdentity("github.com", "example-org")


def test_parse_lowercases_host():
    assert parse_remote_url("git@Git.example.com:team-x/foo.git") == RemoteIdentity("git.example.com", "team-x")


def test_parse_garbage_returns_none():
    assert parse_remote_url("not a url") is None
    assert parse_remote_url("") is None


def test_matches_gitea_host_only():
    rules = [RemoteRule("git.example.com")]
    assert matches(RemoteIdentity("git.example.com", "anything"), rules)


def test_matches_github_requires_org():
    rules = [RemoteRule("github.com", "example-org")]
    assert matches(RemoteIdentity("github.com", "example-org"), rules)
    assert not matches(RemoteIdentity("github.com", "someone-else"), rules)  # 私人 repo 不命中


RULES = [RemoteRule("git.example.com"), RemoteRule("github.com", "example-org")]


def test_classify_included_gitea(git_repo):
    assert classify(git_repo("git@git.example.com:team-x/x.git"), RULES) is True


def test_classify_private_github_excluded(git_repo):
    assert classify(git_repo("https://github.com/someone/personal.git"), RULES) is False


def test_classify_no_remote(git_repo):
    assert classify(git_repo(None), RULES) is False


def test_remote_identities_lists_parsed_remotes(git_repo):
    d = git_repo("git@git.example.com:team-x/x.git")
    assert remote_identities(d) == [RemoteIdentity("git.example.com", "team-x")]


def test_remote_identities_non_git_dir_empty(tmp_path):
    assert remote_identities(str(tmp_path)) == []
