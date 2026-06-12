import subprocess
import tempfile

import pytest


@pytest.fixture(autouse=True)
def _isolate_snapshot_dir(tmp_path, monkeypatch):
    """任何测试都不得触碰真实 ~/.ai-coding-insights/snapshots。

    2026-06-10 实测：漏传 --snapshot-dir 的用例曾把真实快照覆盖成 fixture 垃圾，
    导致窗口决策被污染。cli.main 在调用时才读模块级 DEFAULT_SNAPSHOT_DIR，
    故 patch cli 命名空间即可兜住所有未显式传参的 CLI 路径。
    """
    import ai_coding_insights.cli as cli
    monkeypatch.setattr(cli, "DEFAULT_SNAPSHOT_DIR", tmp_path / "_default-snapshots")


@pytest.fixture(autouse=True)
def _isolate_user_config(tmp_path, monkeypatch):
    """零配置用例必须确定性：任何测试都不得读到真实 ~/.claude/ai-coding-insights/config.toml。
    resolve_config_path 在调用时读模块级 DEFAULT_USER_CONFIG，patch 模块属性即可兜住。"""
    import ai_coding_insights.config as config
    monkeypatch.setattr(config, "DEFAULT_USER_CONFIG", tmp_path / "_user-config.toml")


@pytest.fixture
def git_repo(tmp_path):
    def _make(remote_url):
        d = tempfile.mkdtemp(dir=tmp_path)
        subprocess.run(["git", "init", "-q"], cwd=d, check=True)
        if remote_url:
            subprocess.run(["git", "remote", "add", "origin", remote_url], cwd=d, check=True)
        return d
    return _make
