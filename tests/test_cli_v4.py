import json
from pathlib import Path

from ai_coding_insights.cli import main

_PROFILE = {
    "l4_share": 0.4,
    "breadth": {"summary": "广度摘要", "tools": ["Edit", "Bash"]},
    "depth": {"summary": "深度摘要"},
    "outcome": {"summary": "成果摘要", "landed": 3, "total": 5},
    "evidence": [{"pointer": "slug#u1", "behavior": "纠错并重构"}],
}


def _write_profile(tmp_path):
    p = tmp_path / "profile.json"
    p.write_text(json.dumps(_PROFILE, ensure_ascii=False), encoding="utf-8")
    return p


def _write_metrics(tmp_path, name="metrics.json", **overrides):
    m = {
        "landed_ratio": 0.6,
        "commit_count": 5,
        "landed_count": 3,
        "edit_count": 12,
        "session_count": 4,
        "human_input_count": 40,
        "tool_breadth": 7,
        "active_days": 3,
    }
    m.update(overrides)
    p = tmp_path / name
    p.write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    return p


def test_render_profile_assembles_posture_from_hard_signals(tmp_path, capsys):
    prof = _write_profile(tmp_path)
    mf = _write_metrics(tmp_path, decision_point_count=100,
                        short_turn_count=10, option_pick_count=5)
    out = tmp_path / "r.html"
    rc = main(["render-profile", "--profile", str(prof), "--metrics", str(mf),
               "--out", str(out), "--no-snapshot",
               "--snapshot-dir", str(tmp_path / "snaps")])
    assert rc == 0
    html = out.read_text()
    # 组装：L1=10/100、L2=5/100，剩余 0.85 按 l4_share=0.4 切 → L4 34%、L3 51%
    assert "L1 跟随 10%" in html
    assert "L2 选择 5%" in html
    assert "L4 主导 34%" in html
    stdout = capsys.readouterr().out
    assert "姿势分布" in stdout            # 渲染命令向编排者回报组装结果


def test_render_profile_snapshot_stores_assembled(tmp_path):
    prof = _write_profile(tmp_path)
    mf = _write_metrics(tmp_path, decision_point_count=100,
                        short_turn_count=10, option_pick_count=5)
    snap_dir = tmp_path / "snaps"
    rc = main(["render-profile", "--profile", str(prof), "--metrics", str(mf),
               "--out", str(tmp_path / "r.html"), "--snapshot-dir", str(snap_dir)])
    assert rc == 0
    snap = json.loads(next(snap_dir.glob("*.json")).read_text())
    assert snap["posture_distribution"]["L1"] == 0.1
    assert snap["posture_distribution"]["L2"] == 0.05


def test_render_profile_with_metrics_and_snapshot(tmp_path):
    prof = _write_profile(tmp_path)
    metrics = _write_metrics(tmp_path)
    out = tmp_path / "r.html"
    snap = tmp_path / "snap"
    rc = main(["render-profile", "--profile", str(prof), "--out", str(out),
               "--metrics", str(metrics), "--snapshot-dir", str(snap)])
    assert rc == 0
    html = out.read_text(encoding="utf-8")
    assert html.lstrip().startswith("<!doctype html>")
    # 指标卡数值出现在 HTML 里
    assert "12" in html          # edit_count
    assert "60%" in html         # landed_ratio 0.6
    # 首跑落地快照
    snaps = list(snap.glob("*.json"))
    assert len(snaps) == 1
    saved = json.loads(snaps[0].read_text(encoding="utf-8"))
    assert saved["metrics"]["edit_count"] == 12


def test_render_profile_second_run_shows_arrow(tmp_path):
    prof = _write_profile(tmp_path)
    snap = tmp_path / "snap"
    # 第一次：建立基线快照（小值）
    m1 = _write_metrics(tmp_path, "m1.json", edit_count=12, commit_count=5, generated_at_marker=1)
    out1 = tmp_path / "r1.html"
    rc1 = main(["render-profile", "--profile", str(prof), "--out", str(out1),
                "--metrics", str(m1), "--snapshot-dir", str(snap)])
    assert rc1 == 0
    # 第一次应是基线
    assert "首次基线" in out1.read_text(encoding="utf-8")

    # 第二次：metrics 改大，应出现同比上升箭头 ↑
    m2 = _write_metrics(tmp_path, "m2.json", edit_count=99, commit_count=20)
    out2 = tmp_path / "r2.html"
    rc2 = main(["render-profile", "--profile", str(prof), "--out", str(out2),
                "--metrics", str(m2), "--snapshot-dir", str(snap)])
    assert rc2 == 0
    assert "↑" in out2.read_text(encoding="utf-8")


def test_render_profile_snapshot_strips_project_breakdown(tmp_path):
    # metrics 里故意带 project_breakdown（含 cwd 绝对路径/项目名），落盘快照必须剔除脱敏。
    prof = _write_profile(tmp_path)
    metrics = _write_metrics(
        tmp_path,
        project_breakdown={"/Users/x/Healio": {"session_count": 4, "edit_count": 12}},
    )
    out = tmp_path / "r.html"
    snap = tmp_path / "snap"
    rc = main(["render-profile", "--profile", str(prof), "--out", str(out),
               "--metrics", str(metrics), "--snapshot-dir", str(snap)])
    assert rc == 0
    snaps = list(snap.glob("*.json"))
    assert len(snaps) == 1
    raw = snaps[0].read_text(encoding="utf-8")
    saved = json.loads(raw)
    # 快照里不应有 project_breakdown 键，也不应泄露项目名
    assert "project_breakdown" not in saved["metrics"]
    assert "Healio" not in raw
    # 核心标量指标仍保留
    assert saved["metrics"]["edit_count"] == 12


def test_render_profile_no_snapshot(tmp_path):
    prof = _write_profile(tmp_path)
    metrics = _write_metrics(tmp_path)
    out = tmp_path / "r.html"
    snap = tmp_path / "snap_skip"
    rc = main(["render-profile", "--profile", str(prof), "--out", str(out),
               "--metrics", str(metrics), "--snapshot-dir", str(snap),
               "--no-snapshot"])
    assert rc == 0
    # --no-snapshot：不应生成快照文件
    assert not snap.exists() or not list(snap.glob("*.json"))


def test_render_profile_run_meta_flags(tmp_path, monkeypatch):
    """--run-started/--run-agents 透传到报告末尾运行信息行。"""
    from datetime import datetime, timezone, timedelta
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    prof = _write_profile(tmp_path)
    out = tmp_path / "r.html"
    started = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    rc = main(["render-profile", "--profile", str(prof), "--out", str(out),
               "--snapshot-dir", str(tmp_path / "snap"), "--no-snapshot",
               "--run-started", started,
               "--run-agents", "8"])
    assert rc == 0
    html = out.read_text(encoding="utf-8")
    assert "本报告运行约" in html
    assert "编排 8 个 agent" in html


def test_render_profile_run_model_detected_from_transcript(tmp_path, monkeypatch):
    """模型名由规则层从当前 CC 会话 transcript 确定性提取，不收 LLM 自报。"""
    proj = tmp_path / "projects" / "slug"
    proj.mkdir(parents=True)
    (proj / "sess-9.jsonl").write_text(
        json.dumps({"type": "assistant", "uuid": "u",
                    "message": {"model": "claude-fable-5", "content": []}}) + "\n",
        encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-9")
    prof = _write_profile(tmp_path)
    out = tmp_path / "r.html"
    rc = main(["render-profile", "--profile", str(prof), "--out", str(out),
               "--projects-dir", str(tmp_path / "projects"),
               "--snapshot-dir", str(tmp_path / "snap"), "--no-snapshot"])
    assert rc == 0
    assert "由 claude-fable-5 生成" in out.read_text(encoding="utf-8")


def test_render_profile_run_meta_absent_when_undetectable(tmp_path, monkeypatch):
    """无运行元信息参数且识别不到模型（宁缺勿假）→ 报告不出现运行信息行。"""
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    prof = _write_profile(tmp_path)
    out = tmp_path / "r.html"
    rc = main(["render-profile", "--profile", str(prof), "--out", str(out),
               "--snapshot-dir", str(tmp_path / "snap"), "--no-snapshot"])
    assert rc == 0
    html = out.read_text(encoding="utf-8")
    assert "本报告运行约" not in html and "生成 ·" not in html


def test_render_profile_default_out_is_dated(tmp_path, monkeypatch, capsys):
    """--out 缺省 → 规则层落当前目录 aci-report-<YYYY-MM-DD>.html（日期不靠 LLM 填）。"""
    import re
    from pathlib import Path
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.chdir(tmp_path)
    prof = _write_profile(tmp_path)
    rc = main(["render-profile", "--profile", str(prof),
               "--snapshot-dir", str(tmp_path / "snap"), "--no-snapshot"])
    assert rc == 0
    out_path = capsys.readouterr().out.strip().splitlines()[-1]
    assert re.fullmatch(r".+/aci-report-\d{4}-\d{2}-\d{2}\.html", out_path)
    assert Path(out_path).is_absolute() and Path(out_path).parent == tmp_path
    p = Path(out_path)
    assert p.exists()
    p.unlink()


def test_render_profile_invalid_still_errors(tmp_path):
    prof = tmp_path / "bad.json"
    prof.write_text(json.dumps({"posture_distribution": {"L1": 1}}), encoding="utf-8")
    rc = main(["render-profile", "--profile", str(prof), "--out", str(tmp_path / "r.html")])
    assert rc == 2


def test_scan_emit_batches_real_data(tmp_path, capsys):
    # 自带最小配置 + 本机默认 projects-dir 做集成（结构合法即可，include 规则不必命中本机会话）。
    cfg = tmp_path / "c.toml"
    cfg.write_text('lookback_days = 30\n[[include_remotes]]\nhost = "git.example.com"\n',
                   encoding="utf-8")
    emit = tmp_path / "batches"
    # 隔离快照目录（空 → 窗口判定为「首次」，照常出批），避免读到本机真实快照触发 too_soon。
    rc = main(["scan", "--config", str(cfg), "--emit-batches", str(emit),
               "--snapshot-dir", str(tmp_path / "snap")])
    assert rc == 0
    # _aggregate.json 必存在
    agg_path = emit / "_aggregate.json"
    assert agg_path.exists()
    agg = json.loads(agg_path.read_text(encoding="utf-8"))
    assert "landed_ratio" in agg
    # stdout 清单结构合法
    manifest = json.loads(capsys.readouterr().out)
    assert "batch_count" in manifest
    assert manifest["batch_count"] >= 0
    assert "aggregate" in manifest and "landed_ratio" in manifest["aggregate"]
    assert "plugin_root" in manifest
    assert "included_projects" in manifest
    assert "batches" in manifest
    assert len(manifest["batches"]) == manifest["batch_count"]
    # 若有批，逐批文件应存在且 session_count 对得上
    total_sessions = 0
    for b in manifest["batches"]:
        bp = Path(b["file"])
        assert bp.exists()
        data = json.loads(bp.read_text(encoding="utf-8"))
        assert len(data) == b["session_count"]
        total_sessions += b["session_count"]
    assert total_sessions == agg["session_count"]


def test_scan_emit_batches_empty(tmp_path, capsys):
    # 空数据：projects-dir 指向空目录 → batch_count == 0，仍 print 清单，_aggregate.json 存在。
    empty_projects = tmp_path / "empty_projects"
    empty_projects.mkdir()
    cfg = tmp_path / "c.toml"
    cfg.write_text('lookback_days=3650\n[[include_remotes]]\nhost="git.example.com"\n',
                   encoding="utf-8")
    emit = tmp_path / "batches"
    rc = main(["scan", "--config", str(cfg), "--projects-dir", str(empty_projects),
               "--emit-batches", str(emit), "--snapshot-dir", str(tmp_path / "snap")])
    assert rc == 0
    manifest = json.loads(capsys.readouterr().out)
    assert manifest["batch_count"] == 0
    assert manifest["batches"] == []
    assert (emit / "_aggregate.json").exists()
    # 无 batch 文件
    assert not list(emit.glob("batch-*.json"))
    assert manifest["aggregate"]["session_count"] == 0


def test_scan_since_filters(tmp_path, capsys):
    import subprocess
    co = tmp_path / "co"
    co.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=co, check=True)
    subprocess.run(["git", "remote", "add", "origin",
                    "git@git.example.com:team-x/x.git"], cwd=co, check=True)
    proj = tmp_path / "projects" / "slug"
    proj.mkdir(parents=True)
    # 一条旧会话（2025）一条新会话（2026），--since 2026-01-01 应只保留新的
    (proj / "old.jsonl").write_text(json.dumps(
        {"type": "user", "sessionId": "old", "cwd": str(co),
         "timestamp": "2025-01-01T00:00:00Z", "uuid": "uo",
         "message": {"content": "旧会话内容"}}), encoding="utf-8")
    (proj / "new.jsonl").write_text(json.dumps(
        {"type": "user", "sessionId": "new", "cwd": str(co),
         "timestamp": "2026-03-01T00:00:00Z", "uuid": "un",
         "message": {"content": "新会话内容"}}), encoding="utf-8")
    cfg = tmp_path / "c.toml"
    cfg.write_text('lookback_days=3650\n[[include_remotes]]\nhost="git.example.com"\n',
                   encoding="utf-8")
    emit = tmp_path / "batches"
    # --days 3650 调试覆盖窗口回看，确保只测 --since 过滤而非增量窗口截断。
    rc = main(["scan", "--config", str(cfg), "--projects-dir", str(tmp_path / "projects"),
               "--emit-batches", str(emit), "--since", "2026-01-01",
               "--days", "3650", "--snapshot-dir", str(tmp_path / "snap")])
    assert rc == 0
    manifest = json.loads(capsys.readouterr().out)
    assert manifest["aggregate"]["session_count"] == 1
