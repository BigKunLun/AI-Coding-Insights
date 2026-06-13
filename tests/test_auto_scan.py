"""auto-scan 后台路径的工程卫生：滚动日志记录成功与真实异常（不再静默吞）。

auto-scan 目前未接入任何 hook，但这是它第一次有测试覆盖——上次的「静默失效」
正是因为后台 stderr 无人看、且无落盘日志。state 目录（lock + log）经 --state-dir
注入以保证测试 hermetic，不触碰真实 ~/.ai-coding-insights。
"""
import json
from datetime import datetime, timezone, timedelta

from ai_coding_insights.cli import main


def _make_projects(tmp_path):
    """在 tmp 下造一个落在窗口内的最小 transcript；cwd 指向非 git 目录（git 成果 fail-safe）。"""
    proj = tmp_path / "projects" / "proj1"
    proj.mkdir(parents=True)
    work = tmp_path / "work"
    work.mkdir()
    ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    lines = [
        {"type": "user", "sessionId": "s1", "cwd": str(work), "uuid": "u1",
         "timestamp": ts, "message": {"content": "做点事"}},
        {"type": "assistant", "timestamp": ts,
         "message": {"model": "claude-opus-4-8",
                     "content": [{"type": "tool_use", "name": "Bash", "input": {}}]}},
    ]
    (proj / "s1.jsonl").write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    return tmp_path / "projects"


def _run(tmp_path, projects):
    return main(["auto-scan",
                 "--out-dir", str(tmp_path / "out"),
                 "--projects-dir", str(projects),
                 "--snapshot-dir", str(tmp_path / "snap"),
                 "--state-dir", str(tmp_path / "state")])


def test_auto_scan_logs_success(tmp_path):
    projects = _make_projects(tmp_path)
    rc = _run(tmp_path, projects)
    assert rc == 0
    log = (tmp_path / "state" / "auto-scan.log").read_text(encoding="utf-8")
    assert "ok" in log and "sessions=" in log


def test_auto_scan_logs_exception_instead_of_swallowing(tmp_path, monkeypatch):
    import ai_coding_insights.cli as cli
    projects = _make_projects(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(cli, "aggregate_metrics", boom)
    rc = _run(tmp_path, projects)
    assert rc == 0    # 仍对用户静默退出（不打断主流程）
    log = (tmp_path / "state" / "auto-scan.log").read_text(encoding="utf-8")
    # 真实异常类型与消息进日志，silent failure 从此可诊断
    assert "ERROR" in log and "RuntimeError" in log and "kaboom" in log
