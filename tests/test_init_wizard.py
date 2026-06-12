import json
import tomllib

import pytest

from ai_coding_insights.config import parse_config
from ai_coding_insights.init_wizard import (
    SourceGroup, aggregate_sources, build_config_toml, collect_sources,
    parse_selection, render_menu,
)
from ai_coding_insights.models import RemoteIdentity


def _groups():
    return aggregate_sources(
        cwd_identities={
            "/w/a": [RemoteIdentity("git.corp.example", "team-x")],
            "/w/b": [RemoteIdentity("git.corp.example", "team-y")],   # 自建域：org 归并
            "/w/c": [RemoteIdentity("github.com", "example-org")],     # 公共域：按 org 细分
            "/w/d": [RemoteIdentity("github.com", "personal")],
            "/w/e": [],                                                # 无 remote
        },
        cwd_session_counts={"/w/a": 10, "/w/b": 5, "/w/c": 3, "/w/d": 2, "/w/e": 1},
    )


def test_aggregate_merges_selfhost_by_host_and_splits_public_by_org():
    labels = {g.label: (g.project_count, g.session_count) for g in _groups()}
    assert labels["git.corp.example"] == (2, 15)
    assert labels["github.com/example-org"] == (1, 3)
    assert labels["github.com/personal"] == (1, 2)
    assert labels["（无 git remote）"] == (1, 1)


def test_aggregate_sorts_by_sessions_desc_no_remote_last():
    groups = _groups()
    assert groups[0].label == "git.corp.example"
    assert groups[-1].host is None


def test_aggregate_multi_remote_cwd_counts_in_each_group():
    groups = aggregate_sources(
        cwd_identities={"/w/a": [RemoteIdentity("git.corp.example", None),
                                 RemoteIdentity("github.com", "example-org")]},
        cwd_session_counts={"/w/a": 4})
    assert {g.label for g in groups} == {"git.corp.example", "github.com/example-org"}
    assert all(g.session_count == 4 for g in groups)


def test_menu_shows_only_host_org_and_counts():
    # 隐私约束：菜单不得出现项目路径
    menu = render_menu(_groups())
    assert "/w/a" not in menu
    assert "git.corp.example" in menu and "15 会话" in menu


def test_parse_selection_empty_means_personal():
    assert parse_selection("", _groups()) == []


def test_parse_selection_picks_and_dedups():
    groups = _groups()
    picked = parse_selection("1, 2, 1", groups)
    assert picked == [groups[0], groups[1]]


def test_parse_selection_rejects_bad_index_and_no_remote():
    groups = _groups()
    with pytest.raises(ValueError):
        parse_selection("0", groups)
    with pytest.raises(ValueError):
        parse_selection("99", groups)
    with pytest.raises(ValueError):
        parse_selection("abc", groups)
    no_remote_idx = next(i for i, g in enumerate(groups, 1) if g.host is None)
    with pytest.raises(ValueError):
        parse_selection(str(no_remote_idx), groups)


def test_public_host_without_org_is_not_selectable():
    # 公共托管域 org 解析不出来的组若可选，会生成 host-only 规则
    # 把整个公共域（含私人项目）卷入——过度纳入比漏更糟
    groups = aggregate_sources(
        cwd_identities={"/w/x": [RemoteIdentity("github.com", None)]},
        cwd_session_counts={"/w/x": 1})
    assert "不可选" in render_menu(groups)
    with pytest.raises(ValueError):
        parse_selection("1", groups)


def test_build_config_empty_is_mode_all():
    cfg = parse_config(tomllib.loads(build_config_toml([])))
    assert cfg.mode == "all" and cfg.include_remotes == []


def test_build_config_escapes_toml_special_chars():
    # host/org 来自 git remote URL 解析，理论上可含引号/反斜杠；
    # 不转义会写出 tomllib 拒收的配置文件
    g = SourceGroup(host='h"x', org="a\\b", project_count=1, session_count=1)
    cfg = tomllib.loads(build_config_toml([g]))
    assert cfg["include_remotes"][0] == {"host": 'h"x', "org": "a\\b"}


def test_build_config_roundtrips_through_parse_config():
    # 生成物必须能被自家 parse_config 原样吃下（文件契约自洽）
    selected = [g for g in _groups() if g.label in ("git.corp.example", "github.com/example-org")]
    cfg = parse_config(tomllib.loads(build_config_toml(selected)))
    assert cfg.mode == "include"
    assert {(r.host, r.org) for r in cfg.include_remotes} == {
        ("git.corp.example", None), ("github.com", "example-org")}


def test_collect_sources_counts_sessions_and_identities(tmp_path, git_repo):
    repo = git_repo("git@git.example.com:team-x/x.git")
    projects = tmp_path / "projects"
    for slug in ("s1", "s2"):
        d = projects / slug; d.mkdir(parents=True)
        # 首行是无 cwd 的 summary 元记录：cwd 提取必须跳过它读到后面的 user 行
        (d / f"{slug}.jsonl").write_text("\n".join([
            json.dumps({"type": "summary", "summary": "x"}),
            json.dumps({"type": "user", "sessionId": slug, "cwd": repo,
                        "timestamp": "2026-06-01T00:00:00Z", "message": {"content": "hi"}}),
        ]))
    idents, counts = collect_sources(projects)
    assert counts == {repo: 2}
    assert idents[repo] == [RemoteIdentity("git.example.com", "team-x")]
