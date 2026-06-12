"""scan --emit-batches 接入增量窗口决策的端到端测试。

覆盖三种窗口分支：首次（无快照，cap 45）/ 不足拒绝（N<30，too_soon）/
正常（N=40，lookback==40）。测试聚焦 _window.json 与 manifest 的窗口字段，
不依赖真实会话数（用空 projects 目录即可）。
"""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from ai_coding_insights.cli import main

def _cfg(tmp_path: Path) -> str:
    """写一个最小自洽配置（lookback_days=30 + 一条 include 规则），返回其路径。
    与仓库内 config.example.toml 解耦：测试自带 fixture，不受示例文件改动影响。"""
    p = tmp_path / "c.toml"
    p.write_text('lookback_days = 30\n[[include_remotes]]\nhost = "git.example.com"\n',
                 encoding="utf-8")
    return str(p)


def _write_snapshot(snap_dir: Path, days_ago: int) -> str:
    """在 snap_dir 写一个相对今天 days_ago 天的快照，返回其日期字符串。"""
    snap_dir.mkdir(parents=True, exist_ok=True)
    d = (datetime.now(timezone.utc) - timedelta(days=days_ago)).date().isoformat()
    (snap_dir / f"{d}.json").write_text(
        json.dumps({"generated_at": d + "T00:00:00+00:00",
                    "metrics": {}, "posture_distribution": {}}, ensure_ascii=False),
        encoding="utf-8")
    return d


def _empty_projects(tmp_path: Path) -> Path:
    p = tmp_path / "projects"
    p.mkdir()
    return p


def _run(tmp_path, snap_dir):
    batches = tmp_path / "batches"
    rc = main(["scan", "--config", _cfg(tmp_path),
               "--projects-dir", str(_empty_projects(tmp_path)),
               "--emit-batches", str(batches),
               "--snapshot-dir", str(snap_dir)])
    return rc, batches


def test_first_run_uses_cap_45(tmp_path):
    snap = tmp_path / "snap"      # 不创建/留空 → 首次
    rc, batches = _run(tmp_path, snap)
    assert rc == 0
    win = json.loads((batches / "_window.json").read_text(encoding="utf-8"))
    assert win["status"] == "first"
    assert win["lookback_days"] == 45


def test_too_soon_rejects_and_emits_no_batches(tmp_path, capsys):
    snap = tmp_path / "snap"
    _write_snapshot(snap, days_ago=26)
    rc, batches = _run(tmp_path, snap)
    assert rc == 0
    # stdout 最后一行是 manifest JSON
    last = capsys.readouterr().out.strip().splitlines()[-1]
    manifest = json.loads(last)
    assert manifest["status"] == "too_soon"
    assert manifest["batch_count"] == 0
    assert manifest["message"]
    # _window.json 落盘且 status 一致
    win = json.loads((batches / "_window.json").read_text(encoding="utf-8"))
    assert win["status"] == "too_soon"
    # 拒绝分支不应产出任何 batch 文件
    assert not list(batches.glob("batch-*.json"))


def test_ok_run_uses_min_n_cap(tmp_path):
    snap = tmp_path / "snap"
    _write_snapshot(snap, days_ago=40)
    rc, batches = _run(tmp_path, snap)
    assert rc == 0
    win = json.loads((batches / "_window.json").read_text(encoding="utf-8"))
    assert win["status"] == "ok"
    assert win["lookback_days"] == 40


def _projects_with_session(tmp_path: Path, ts: str) -> Path:
    """造一个 projects 目录，含一个带 timestamp 的 transcript（不在纳入范围的项目即可，
    detect_data_start 只看时间不看归属）。"""
    p = tmp_path / "projects" / "slug"
    p.mkdir(parents=True)
    (p / "slug.jsonl").write_text(json.dumps(
        {"type": "user", "sessionId": "slug", "timestamp": ts,
         "message": {"content": "hi"}}))
    return tmp_path / "projects"


def test_window_json_has_data_start_and_truncated_keys(tmp_path):
    """正常路径：_window.json 透传 data_start 与 truncated 两键。"""
    snap = tmp_path / "snap"      # 首次基线 → since 为 today-45
    batches = tmp_path / "batches"
    # data_start 设在很早（远早于 since），应判 truncated=True
    projects = _projects_with_session(tmp_path, "2020-01-01T00:00:00Z")
    rc = main(["scan", "--config", _cfg(tmp_path), "--projects-dir", str(projects),
               "--emit-batches", str(batches), "--snapshot-dir", str(snap)])
    assert rc == 0
    win = json.loads((batches / "_window.json").read_text(encoding="utf-8"))
    assert "data_start" in win and "truncated" in win
    assert win["data_start"].startswith("2020-01-01")
    assert win["truncated"] is False  # 2020 远早于 since → 数据起点更早，未截断


def test_window_truncated_true_when_data_start_after_since(tmp_path):
    snap = tmp_path / "snap"      # 首次基线 → since 为 today-45
    batches = tmp_path / "batches"
    # data_start = 今天（远晚于 since=today-45）→ 截断
    today_iso = datetime.now(timezone.utc).date().isoformat() + "T00:00:00Z"
    projects = _projects_with_session(tmp_path, today_iso)
    rc = main(["scan", "--config", _cfg(tmp_path), "--projects-dir", str(projects),
               "--emit-batches", str(batches), "--snapshot-dir", str(snap)])
    assert rc == 0
    win = json.loads((batches / "_window.json").read_text(encoding="utf-8"))
    assert win["truncated"] is True


def test_too_soon_window_has_no_truncated_data(tmp_path):
    """too_soon 提前返回路径不做检测，data_start/truncated 不应为真值。"""
    snap = tmp_path / "snap"
    _write_snapshot(snap, days_ago=26)
    projects = _projects_with_session(tmp_path, "2020-01-01T00:00:00Z")
    batches = tmp_path / "batches"
    rc = main(["scan", "--config", _cfg(tmp_path), "--projects-dir", str(projects),
               "--emit-batches", str(batches), "--snapshot-dir", str(snap)])
    assert rc == 0
    win = json.loads((batches / "_window.json").read_text(encoding="utf-8"))
    assert win["status"] == "too_soon"
    assert not win.get("truncated")
    assert win.get("data_start") is None


# ---- render-profile --window 接入 ----

_PROFILE = {
    "breadth": {"summary": "广度摘要", "tools": ["Edit", "Bash"]},
    "depth": {"summary": "深度摘要"},
    "outcome": {"summary": "成果摘要", "landed": 3, "total": 5},
    "evidence": [{"pointer": "slug#u1", "behavior": "纠错并重构"}],
}

_WINDOW = {
    "status": "ok",
    "since_date": "2026-05-01",
    "until_date": "2026-06-10",
    "last_check_date": "2026-05-01",
    "days_since_last": 40,
    "lookback_days": 40,
    "message": None,
}

_METRICS = {
    "landed_ratio": 0.6, "commit_count": 5, "landed_count": 3, "edit_count": 12,
    "session_count": 4, "human_input_count": 40, "tool_breadth": 7, "active_days": 3,
}


def _write_json(path: Path, obj) -> Path:
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return path


def test_render_profile_window_in_header(tmp_path):
    prof = _write_json(tmp_path / "profile.json", _PROFILE)
    met = _write_json(tmp_path / "metrics.json", _METRICS)
    win = _write_json(tmp_path / "_window.json", _WINDOW)
    out = tmp_path / "r.html"
    rc = main(["render-profile", "--profile", str(prof), "--out", str(out),
               "--metrics", str(met), "--window", str(win),
               "--no-snapshot", "--config", _cfg(tmp_path)])
    assert rc == 0
    html = out.read_text(encoding="utf-8")
    assert "取数 2026-05-01 → 2026-06-10" in html


def test_render_profile_without_window_still_renders(tmp_path):
    prof = _write_json(tmp_path / "profile.json", _PROFILE)
    met = _write_json(tmp_path / "metrics.json", _METRICS)
    out = tmp_path / "r.html"
    rc = main(["render-profile", "--profile", str(prof), "--out", str(out),
               "--metrics", str(met), "--no-snapshot", "--config", _cfg(tmp_path)])
    assert rc == 0
    html = out.read_text(encoding="utf-8")
    assert "取数" not in html
    assert "近" in html


def test_render_profile_shows_truncation_warning(tmp_path):
    prof = _write_json(tmp_path / "profile.json", _PROFILE)
    met = _write_json(tmp_path / "metrics.json", _METRICS)
    win = _write_json(tmp_path / "_window.json",
                      {**_WINDOW, "data_start": "2026-05-12T08:00:00Z", "truncated": True})
    out = tmp_path / "r.html"
    rc = main(["render-profile", "--profile", str(prof), "--out", str(out),
               "--metrics", str(met), "--window", str(win),
               "--no-snapshot", "--config", _cfg(tmp_path)])
    assert rc == 0
    html = out.read_text(encoding="utf-8")
    assert "实际数据起点 2026-05-12" in html
    assert "更早记录已被本机清理" in html


def test_render_profile_no_truncation_warning_when_false(tmp_path):
    prof = _write_json(tmp_path / "profile.json", _PROFILE)
    met = _write_json(tmp_path / "metrics.json", _METRICS)
    # 旧 _window.json 无 truncated 键 → 完全不出警示
    win = _write_json(tmp_path / "_window.json", _WINDOW)
    out = tmp_path / "r.html"
    rc = main(["render-profile", "--profile", str(prof), "--out", str(out),
               "--metrics", str(met), "--window", str(win),
               "--no-snapshot", "--config", _cfg(tmp_path)])
    assert rc == 0
    html = out.read_text(encoding="utf-8")
    assert "实际数据起点" not in html


def test_render_profile_snapshot_stores_real_window(tmp_path):
    prof = _write_json(tmp_path / "profile.json", _PROFILE)
    met = _write_json(tmp_path / "metrics.json", _METRICS)
    win = _write_json(tmp_path / "_window.json", _WINDOW)
    out = tmp_path / "r.html"
    snap = tmp_path / "snap"
    rc = main(["render-profile", "--profile", str(prof), "--out", str(out),
               "--metrics", str(met), "--window", str(win),
               "--snapshot-dir", str(snap), "--config", _cfg(tmp_path)])
    assert rc == 0
    from ai_coding_insights.snapshot import load_latest
    saved = load_latest(dir=snap)
    assert saved is not None
    assert saved["window"] == _WINDOW


def test_rerun_clears_stale_batch_files(tmp_path):
    """重跑批数变少时，上一轮残留的 batch 文件必须被清掉，否则 verify-obs 误报缺口。"""
    snap = tmp_path / "snap"  # 留空 → 首次窗口
    batches = tmp_path / "batches"
    batches.mkdir()
    (batches / "batch-07.json").write_text("[]", encoding="utf-8")
    (batches / "_aggregate.json").write_text("{}", encoding="utf-8")
    rc = main(["scan", "--config", _cfg(tmp_path),
               "--projects-dir", str(_empty_projects(tmp_path)),
               "--emit-batches", str(batches),
               "--snapshot-dir", str(snap)])
    assert rc == 0
    assert not list(batches.glob("batch-*.json"))  # 空 projects → 0 批，残留须被清


def test_days_override_aligns_window_annotation(tmp_path, capsys):
    """--days 调试覆盖时，_window.json 的 lookback/since_date 必须随实际取数对齐。"""
    snap = tmp_path / "snap"
    batches = tmp_path / "batches"
    rc = main(["scan", "--config", _cfg(tmp_path),
               "--projects-dir", str(_empty_projects(tmp_path)),
               "--emit-batches", str(batches),
               "--snapshot-dir", str(snap), "--days", "5"])
    assert rc == 0
    win = json.loads((batches / "_window.json").read_text(encoding="utf-8"))
    assert win["lookback_days"] == 5
    expect_since = (datetime.now(timezone.utc).date() - timedelta(days=5)).isoformat()
    assert win["since_date"] == expect_since


def test_emit_batches_aggregate_strips_project_breakdown(tmp_path, capsys):
    """_aggregate.json 面向 LLM 层：不得携带以 cwd 路径（含项目名）为键的明细。"""
    snap = tmp_path / "snap"
    batches = tmp_path / "batches"
    rc = main(["scan", "--config", _cfg(tmp_path),
               "--projects-dir", str(_empty_projects(tmp_path)),
               "--emit-batches", str(batches),
               "--snapshot-dir", str(snap)])
    assert rc == 0
    agg = json.loads((batches / "_aggregate.json").read_text(encoding="utf-8"))
    assert "project_breakdown" not in agg
    manifest = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert "project_breakdown" not in manifest["aggregate"]
