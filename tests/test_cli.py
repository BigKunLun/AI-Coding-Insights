import json, subprocess
from pathlib import Path
from ai_coding_insights.cli import main


def test_init_writes_include_config(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"; repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "remote", "add", "origin", "git@git.example.com:team-x/x.git"],
                   cwd=repo, check=True)
    projects = tmp_path / "projects"; d = projects / "s1"; d.mkdir(parents=True)
    (d / "s1.jsonl").write_text(json.dumps(
        {"type": "user", "sessionId": "s1", "cwd": str(repo),
         "timestamp": "2026-06-01T00:00:00Z", "message": {"content": "hi"}}))
    out_cfg = tmp_path / "out" / "config.toml"
    monkeypatch.setattr("builtins.input", lambda *a: "1")
    rc = main(["init", "--projects-dir", str(projects), "--out", str(out_cfg)])
    assert rc == 0
    text = out_cfg.read_text()
    assert 'mode = "include"' in text and 'host = "git.example.com"' in text
    # 隐私：交互输出不得出现项目路径
    assert str(repo) not in capsys.readouterr().out


def test_init_empty_selection_writes_mode_all(tmp_path, monkeypatch):
    projects = tmp_path / "projects"; d = projects / "s1"; d.mkdir(parents=True)
    plain = tmp_path / "plain"; plain.mkdir()
    (d / "s1.jsonl").write_text(json.dumps(
        {"type": "user", "sessionId": "s1", "cwd": str(plain),
         "timestamp": "2026-06-01T00:00:00Z", "message": {"content": "hi"}}))
    out_cfg = tmp_path / "out" / "config.toml"
    monkeypatch.setattr("builtins.input", lambda *a: "")
    rc = main(["init", "--projects-dir", str(projects), "--out", str(out_cfg)])
    assert rc == 0
    assert out_cfg.read_text() == 'mode = "all"\n'


def test_init_overwrite_eof_exits_cleanly(tmp_path, monkeypatch, capsys):
    projects = tmp_path / "projects"; d = projects / "s1"; d.mkdir(parents=True)
    plain = tmp_path / "plain"; plain.mkdir()
    (d / "s1.jsonl").write_text(json.dumps(
        {"type": "user", "sessionId": "s1", "cwd": str(plain),
         "timestamp": "2026-06-01T00:00:00Z", "message": {"content": "hi"}}))
    out_cfg = tmp_path / "config.toml"; out_cfg.write_text('mode = "all"\n')
    answers = iter([""])  # 第一问回答空选择；第二问（覆盖确认）EOF
    def fake_input(*a):
        try:
            return next(answers)
        except StopIteration:
            raise EOFError
    monkeypatch.setattr("builtins.input", fake_input)
    rc = main(["init", "--projects-dir", str(projects), "--out", str(out_cfg)])
    assert rc == 1
    assert out_cfg.read_text() == 'mode = "all"\n'  # 原文件未被改动


def test_init_overwrite_declined_keeps_file(tmp_path, monkeypatch):
    projects = tmp_path / "projects"; d = projects / "s1"; d.mkdir(parents=True)
    plain = tmp_path / "plain"; plain.mkdir()
    (d / "s1.jsonl").write_text(json.dumps(
        {"type": "user", "sessionId": "s1", "cwd": str(plain),
         "timestamp": "2026-06-01T00:00:00Z", "message": {"content": "hi"}}))
    out_cfg = tmp_path / "config.toml"; out_cfg.write_text("# 原内容\n")
    answers = iter(["", "n"])
    monkeypatch.setattr("builtins.input", lambda *a: next(answers))
    rc = main(["init", "--projects-dir", str(projects), "--out", str(out_cfg)])
    assert rc == 1
    assert out_cfg.read_text() == "# 原内容\n"


def test_cli_json_end_to_end(tmp_path, capsys, monkeypatch):
    included = tmp_path / "co"; included.mkdir()
    subprocess.run(["git","init","-q"], cwd=included, check=True)
    subprocess.run(["git","remote","add","origin","git@git.example.com:team-x/x.git"],
                   cwd=included, check=True)
    projects = tmp_path / "projects" / "slug"; projects.mkdir(parents=True)
    (projects / "slug.jsonl").write_text(json.dumps(
        {"type":"user","sessionId":"slug","cwd":str(included),
         "timestamp":"2026-06-09T00:00:00Z","message":{"content":"继续"}}))
    cfg = tmp_path / "config.toml"
    cfg.write_text('lookback_days=3650\n[[include_remotes]]\nhost="git.example.com"\n')

    main(["--projects-dir", str(tmp_path/"projects"), "--config", str(cfg), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert out["session_count"] == 1
    assert str(included) in out["included_projects"]


def test_scan_profile_input(tmp_path, capsys):
    import json, subprocess
    from ai_coding_insights.cli import main
    co = tmp_path/"co"; co.mkdir()
    subprocess.run(["git","init","-q"],cwd=co,check=True)
    subprocess.run(["git","remote","add","origin","git@git.example.com:team-x/x.git"],cwd=co,check=True)
    proj = tmp_path/"projects"/"slug"; proj.mkdir(parents=True)
    (proj/"slug.jsonl").write_text(json.dumps(
        {"type":"user","sessionId":"slug","cwd":str(co),
         "timestamp":"2026-06-09T00:00:00Z","uuid":"u1","message":{"content":"重构成幂等"}}))
    cfg = tmp_path/"c.toml"; cfg.write_text('lookback_days=3650\n[[include_remotes]]\nhost="git.example.com"\n')
    main(["scan","--projects-dir",str(tmp_path/"projects"),"--config",str(cfg),"--profile-input"])
    out = json.loads(capsys.readouterr().out)
    assert out["session_count"] == 1
    assert "sessions_input" in out and out["sessions_input"][0]["turns"][0]["text"] == "重构成幂等"


def test_scan_mode_all_includes_non_git_project(tmp_path, capsys):
    plain = tmp_path / "plain"; plain.mkdir()
    projects = tmp_path / "projects"; d = projects / "slug"; d.mkdir(parents=True)
    (d / "s.jsonl").write_text(json.dumps(
        {"type": "user", "sessionId": "slug", "cwd": str(plain),
         "timestamp": "2026-06-01T00:00:00Z", "message": {"content": "hi"}}))
    cfg = tmp_path / "c.toml"; cfg.write_text('mode = "all"\nlookback_days = 3650\n')
    rc = main(["scan", "--projects-dir", str(projects), "--config", str(cfg), "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert str(plain) in out["included_projects"]


def test_render_profile_subcommand(tmp_path, capsys):
    import json
    from ai_coding_insights.cli import main
    prof = tmp_path/"p.json"
    prof.write_text(json.dumps({"l4_share":0.4,
        "breadth":{"summary":"b"},"depth":{"summary":"d"},"outcome":{"summary":"o","landed":1,"total":2},
        "evidence":[{"pointer":"f#u","behavior":"纠错"}]}))
    out = tmp_path/"r.html"
    rc = main(["render-profile","--profile",str(prof),"--out",str(out),
               "--snapshot-dir",str(tmp_path/"snap")])
    assert rc == 0 and out.read_text().lstrip().startswith("<!doctype html>")


def test_render_profile_verifies_evidence_pointers(tmp_path, capsys):
    """指针确定性核验：uuid 真在文件里才算命中；拿会话 id 冒充 turn uuid、
    编造路径的标注 ⚠ 并 stderr 出声；会话级指针（无 #uuid）只验文件存在。"""
    import json
    from ai_coding_insights.cli import main
    sess = tmp_path / "abc-session.jsonl"
    sess.write_text(
        json.dumps({"type": "user", "sessionId": "abc-session", "uuid": "turn-real",
                    "message": {"content": "hi"}}) + "\n", encoding="utf-8")
    prof = tmp_path / "p.json"
    prof.write_text(json.dumps({
        "l4_share": 0.4,
        "breadth": {"summary": "b"}, "depth": {"summary": "d"},
        "outcome": {"summary": "o", "landed": 1, "total": 2},
        "evidence": [
            {"pointer": f"{sess}#turn-real", "behavior": "真指针"},
            {"pointer": f"{sess}#abc-session", "behavior": "会话id冒充uuid"},
            {"pointer": f"{tmp_path}/nope.jsonl#u", "behavior": "文件不存在"},
            {"pointer": str(sess), "behavior": "会话级指针"},
        ]}), encoding="utf-8")
    out = tmp_path / "r.html"
    rc = main(["render-profile", "--profile", str(prof), "--out", str(out),
               "--snapshot-dir", str(tmp_path / "snap"), "--no-snapshot"])
    assert rc == 0
    html = out.read_text(encoding="utf-8")
    assert html.count("⚠ 指针未命中") == 2     # 冒充 + 编造路径；真指针与会话级不标
    err = capsys.readouterr().err
    assert "证据指针未命中" in err and "abc-session" in err


def test_snapshot_only_keeps_core_scalar_keys(tmp_path):
    import json
    from ai_coding_insights.cli import main
    from ai_coding_insights.snapshot import _CORE_KEYS
    prof = tmp_path/"p.json"
    prof.write_text(json.dumps({"l4_share":0.4,
        "breadth":{"summary":"b"},"depth":{"summary":"d"},"outcome":{"summary":"o","landed":1,"total":2},
        "evidence":[{"pointer":"f#u","behavior":"纠错"}]}))
    metrics = tmp_path/"m.json"
    metrics.write_text(json.dumps({"session_count":3,"commit_count":5,"token_total":100,
        "token_usage":{"claude-x":{"input":1,"output":2,"cache_read":0,"cache_creation":0}},
        "trend":{"first_half":{},"second_half":{}},
        "project_breakdown":{"/Users/x/secret-project":3}}))
    snap_dir = tmp_path/"snap"
    rc = main(["render-profile","--profile",str(prof),"--metrics",str(metrics),
               "--out",str(tmp_path/"r.html"),"--snapshot-dir",str(snap_dir)])
    assert rc == 0
    saved = json.loads(next(snap_dir.glob("*.json")).read_text())
    extra = set(saved["metrics"]) - set(_CORE_KEYS)
    assert not extra, f"快照混入非白名单键: {extra}"
    assert saved["metrics"]["token_total"] == 100


def test_render_profile_invalid_returns_error(tmp_path, capsys):
    import json
    from ai_coding_insights.cli import main
    prof = tmp_path/"bad.json"; prof.write_text(json.dumps({"posture_distribution":{"L1":1}}))
    rc = main(["render-profile","--profile",str(prof),"--out",str(tmp_path/"r.html")])
    assert rc != 0
    assert "posture_distribution" in capsys.readouterr().err


def test_scan_zero_config_runs_mode_all(tmp_path, capsys):
    # 验收标准 2：无任何配置文件，scan 可跑通且行为为 mode=all
    plain = tmp_path / "plain"; plain.mkdir()
    projects = tmp_path / "projects"; d = projects / "slug"; d.mkdir(parents=True)
    (d / "s.jsonl").write_text(json.dumps(
        {"type": "user", "sessionId": "slug", "cwd": str(plain),
         "timestamp": "2026-06-01T00:00:00Z", "message": {"content": "hi"}}))
    rc = main(["scan", "--projects-dir", str(projects), "--days", "3650", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert str(plain) in out["included_projects"]


def test_scan_explicit_config_missing_exits_2(tmp_path, capsys):
    rc = main(["scan", "--projects-dir", str(tmp_path), "--config",
               str(tmp_path / "nope.toml"), "--json"])
    assert rc == 2
    assert "配置错误" in capsys.readouterr().err


def test_emit_batches_manifest_plugin_root(tmp_path, capsys):
    projects = tmp_path / "projects"; projects.mkdir()
    root = tmp_path / "root"; root.mkdir()
    (root / "config.toml").write_text('mode = "all"\n')
    rc = main(["scan", "--projects-dir", str(projects), "--plugin-root", str(root),
               "--emit-batches", str(tmp_path / "batches"),
               "--snapshot-dir", str(tmp_path / "snaps")])
    assert rc == 0
    manifest = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert manifest["plugin_root"] == str(root)


def test_emit_batches_cleans_stale_obs_and_profile(tmp_path, capsys):
    # 上一轮残留的 obs/profile 不清，批次划分一变专家会静默读到张冠李戴的数据。
    # 清理必须收在规则层：LLM 层跑 rm 会被权限分类器拦截（2026-06-11 实测）
    projects = tmp_path / "projects"; projects.mkdir()
    cfg = tmp_path / "c.toml"; cfg.write_text('mode = "all"\n')
    out = tmp_path / "b"; out.mkdir()
    (out / "obs-99.json").write_text("{}")
    (out / "profile.json").write_text("{}")
    (out / "expert-posture.json").write_text("{}")
    rc = main(["scan", "--projects-dir", str(projects), "--config", str(cfg),
               "--emit-batches", str(out), "--snapshot-dir", str(tmp_path / "s")])
    assert rc == 0
    assert not (out / "obs-99.json").exists()
    assert not (out / "profile.json").exists()
    assert not (out / "expert-posture.json").exists()


def test_emit_batches_window_and_manifest_carry_mode(tmp_path, capsys):
    projects = tmp_path / "projects"; projects.mkdir()
    cfg = tmp_path / "c.toml"; cfg.write_text('mode = "all"\n')
    rc = main(["scan", "--projects-dir", str(projects), "--config", str(cfg),
               "--emit-batches", str(tmp_path / "b"), "--snapshot-dir", str(tmp_path / "s")])
    assert rc == 0
    window = json.loads((tmp_path / "b" / "_window.json").read_text())
    assert window["mode"] == "all"
    manifest = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert manifest["mode"] == "all"
