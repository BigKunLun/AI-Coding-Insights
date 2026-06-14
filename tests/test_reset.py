"""reset 子命令：清掉本机可再生产物，让用户干净重测。

承重边界：只删 --state-dir 下的已知产物白名单；config.toml（在 ~/.claude 那棵树下）
与任何未登记文件永不进入目标集——靠纯函数 reset_targets 写死，不可配。
"""
from ai_coding_insights.cli import reset_targets, main
from ai_coding_insights.snapshot import DEFAULT_SNAPSHOT_DIR
from pathlib import Path


def _populate(state_dir):
    (state_dir / "snapshots").mkdir()
    (state_dir / "snapshots" / "2026-06-14.json").write_text("{}", encoding="utf-8")
    (state_dir / "reports").mkdir()
    (state_dir / "reports" / "aci-auto-2026-06-14.html").write_text("x", encoding="utf-8")
    (state_dir / "run").mkdir()
    (state_dir / "run" / "_window.json").write_text("{}", encoding="utf-8")
    (state_dir / ".auto-scan.lock").write_text("2026-06-14", encoding="utf-8")
    (state_dir / "auto-scan.log").write_text("log line\n", encoding="utf-8")


def test_reset_targets_is_known_product_allowlist(tmp_path):
    targets = reset_targets(tmp_path)
    assert {t.name for t in targets} == {
        "snapshots", "reports", "run", ".auto-scan.lock", "auto-scan.log"}
    # 全部直挂 state_dir，不会越界到父目录或别处
    for t in targets:
        assert t.parent == tmp_path


def test_reset_snapshots_entry_tracks_default_snapshot_dir():
    # 快照那条不是字面量重复，而是引用真相源 DEFAULT_SNAPSHOT_DIR；常量改名时此断言
    # 立刻变红，挡住「reset 漏删快照→30 天闸门仍生效」的静默漂移。
    names = {t.name for t in reset_targets(Path("/x"))}
    assert DEFAULT_SNAPSHOT_DIR.name in names


def test_reset_removes_all_products(tmp_path):
    _populate(tmp_path)
    rc = main(["reset", "--state-dir", str(tmp_path)])
    assert rc == 0
    for name in ("snapshots", "reports", "run", ".auto-scan.lock", "auto-scan.log"):
        assert not (tmp_path / name).exists()


def test_reset_spares_unregistered_files(tmp_path):
    # 白名单之外的东西（含任何未来误放的配置）必须毫发无伤，证明不是整目录 rmtree
    _populate(tmp_path)
    mystery = tmp_path / "config.toml"
    mystery.write_text("keep me", encoding="utf-8")
    main(["reset", "--state-dir", str(tmp_path)])
    assert mystery.read_text(encoding="utf-8") == "keep me"


def test_reset_prints_removed_manifest(tmp_path, capsys):
    _populate(tmp_path)
    main(["reset", "--state-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert "已删" in out          # 真删用「已删」动词
    assert "snapshots" in out and "reports" in out


def test_reset_dry_run_deletes_nothing_but_lists(tmp_path, capsys):
    _populate(tmp_path)
    rc = main(["reset", "--state-dir", str(tmp_path), "--dry-run"])
    assert rc == 0
    # 五个产物一个都不能少——dry-run 必须真正零删除
    for name in ("snapshots", "reports", "run", ".auto-scan.lock", "auto-scan.log"):
        assert (tmp_path / name).exists()
    out = capsys.readouterr().out
    assert "将删" in out and "snapshots" in out   # 用「将删」动词，绝不冒充「已删」


def test_reset_unlinks_symlink_target_without_following(tmp_path):
    # snapshots 是指向链外真实目录的符号链接：reset 只摘链接本身，绝不跟随删掉链外内容
    external = tmp_path / "external_store"
    external.mkdir()
    (external / "precious.json").write_text("keep", encoding="utf-8")
    state = tmp_path / "state"
    state.mkdir()
    link = state / "snapshots"
    link.symlink_to(external)
    rc = main(["reset", "--state-dir", str(state)])
    assert rc == 0
    assert not link.is_symlink()                                   # 链接已摘
    assert (external / "precious.json").read_text(encoding="utf-8") == "keep"  # 链外毫发无伤


def test_reset_removes_dangling_symlink(tmp_path):
    # 悬空符号链接 exists()==False，旧实现会漏清；reset 应连悬空链接一并摘掉
    state = tmp_path / "state"
    state.mkdir()
    link = state / "auto-scan.log"
    link.symlink_to(state / "nonexistent-target")
    assert link.is_symlink() and not link.exists()
    rc = main(["reset", "--state-dir", str(state)])
    assert rc == 0
    assert not link.is_symlink()


def test_reset_empty_is_graceful_and_idempotent(tmp_path):
    rc = main(["reset", "--state-dir", str(tmp_path)])
    assert rc == 0
    # 连跑两次不炸
    assert main(["reset", "--state-dir", str(tmp_path)]) == 0
