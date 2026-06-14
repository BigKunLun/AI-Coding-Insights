"""reset 子命令：清空本机可再生产物 + 接管当日 auto-scan 锁，让用户干净重测。

承重边界：只删 --state-dir 下的已知产物白名单；config.toml（在 ~/.claude 那棵树下）
与任何未登记文件永不进入目标集——靠纯函数 reset_targets 写死，不可配。
关键机制：reset 把今日写进 .auto-scan.lock（而非删它），使 SessionEnd 的 auto-scan
当天跳过、不抢先写今日快照重新武装 30 天闸门——这是「reset 后重跑仍 too_soon」的根因修复。
"""
from datetime import datetime, timezone
from pathlib import Path

from ai_coding_insights.cli import reset_targets, main
from ai_coding_insights.snapshot import DEFAULT_SNAPSHOT_DIR


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _populate(state_dir):
    (state_dir / "snapshots").mkdir()
    (state_dir / "snapshots" / "2026-06-14.json").write_text("{}", encoding="utf-8")
    (state_dir / "reports").mkdir()
    (state_dir / "reports" / "aci-auto-2026-06-14.html").write_text("x", encoding="utf-8")
    (state_dir / "run").mkdir()
    (state_dir / "run" / "_window.json").write_text("{}", encoding="utf-8")
    # 锁写成明显的旧日期，好区分「reset 置成今日」与「原样没动」
    (state_dir / ".auto-scan.lock").write_text("2000-01-01", encoding="utf-8")
    (state_dir / "auto-scan.log").write_text("log line\n", encoding="utf-8")


def test_reset_targets_is_删除白名单(tmp_path):
    # 删除白名单是 4 个产物；.auto-scan.lock 不在其列——它是「置今日」而非删除
    targets = reset_targets(tmp_path)
    assert {t.name for t in targets} == {
        "snapshots", "reports", "run", "auto-scan.log"}
    assert ".auto-scan.lock" not in {t.name for t in targets}
    for t in targets:
        assert t.parent == tmp_path   # 全部直挂 state_dir，不越界


def test_reset_snapshots_entry_tracks_default_snapshot_dir():
    # 快照那条引用真相源 DEFAULT_SNAPSHOT_DIR；常量改名时此断言立刻变红，
    # 挡住「reset 漏删快照→30 天闸门仍生效」的静默漂移。
    names = {t.name for t in reset_targets(Path("/x"))}
    assert DEFAULT_SNAPSHOT_DIR.name in names


def test_reset_removes_products_but_takes_over_lock(tmp_path):
    _populate(tmp_path)
    rc = main(["reset", "--state-dir", str(tmp_path)])
    assert rc == 0
    # 4 个产物清空
    for name in ("snapshots", "reports", "run", "auto-scan.log"):
        assert not (tmp_path / name).exists()
    # 锁不删，而是被置成今日——auto-scan 当天据此跳过
    assert (tmp_path / ".auto-scan.lock").read_text(encoding="utf-8").strip() == _today()


def test_reset_writes_today_into_lock_even_when_clean(tmp_path):
    # 即便本已干净（无产物可删），reset 也要置今日锁，保证接下来的手动重跑不被抢占
    rc = main(["reset", "--state-dir", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / ".auto-scan.lock").read_text(encoding="utf-8").strip() == _today()


def test_autoscan_skips_same_day_after_reset(tmp_path):
    # 端到端复现根因修复：reset 后同日 auto-scan 必须在锁检查处就返回，
    # 不写任何日志/快照——否则它会重新武装 30 天闸门。
    state = tmp_path / "state"
    state.mkdir()
    main(["reset", "--state-dir", str(state)])
    rc = main(["auto-scan", "--out-dir", str(tmp_path / "out"),
               "--state-dir", str(state), "--snapshot-dir", str(tmp_path / "snaps"),
               "--projects-dir", str(tmp_path / "noproj")])
    assert rc == 0
    # auto-scan 的 "start" 日志在锁检查之后才打；锁命中今日则提前返回，日志不该存在
    assert not (state / "auto-scan.log").exists()
    assert not (tmp_path / "snaps").exists()   # 更没写新快照


def test_reset_spares_unregistered_files(tmp_path):
    # 白名单之外的东西（含任何未来误放的配置）必须毫发无伤，证明不是整目录 rmtree
    _populate(tmp_path)
    mystery = tmp_path / "config.toml"
    mystery.write_text("keep me", encoding="utf-8")
    main(["reset", "--state-dir", str(tmp_path)])
    assert mystery.read_text(encoding="utf-8") == "keep me"


def test_reset_prints_manifest_with_deletions_and_lock(tmp_path, capsys):
    _populate(tmp_path)
    main(["reset", "--state-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert "已删" in out and "snapshots" in out and "reports" in out
    assert ".auto-scan.lock" in out   # 锁接管动作也要回显


def test_reset_dry_run_changes_nothing_but_lists(tmp_path, capsys):
    _populate(tmp_path)
    rc = main(["reset", "--state-dir", str(tmp_path), "--dry-run"])
    assert rc == 0
    # 五个产物一个都不能少、锁也不能被改——dry-run 必须真正零副作用
    for name in ("snapshots", "reports", "run", ".auto-scan.lock", "auto-scan.log"):
        assert (tmp_path / name).exists()
    assert (tmp_path / ".auto-scan.lock").read_text(encoding="utf-8").strip() == "2000-01-01"
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
