import argparse, json, os, sys
from datetime import datetime, timezone, date, timedelta
from pathlib import Path
from .config import load_config, resolve_config_path, ConfigError
from .window import decide_window
from .discovery import discover_sessions, detect_data_start, is_window_truncated
from .signals import compute_stats, aggregate_metrics
from .outcome import compute_outcome
from .git_outcome import repo_outcome, repo_root
from .timeutil import parse_timestamp
from .profile_input import build_session_input
from .batch import make_batches
from .customization import scan_custom_skills, detect_hook_config, compute_customization_signals
from .snapshot import save_snapshot, load_latest, diff_metrics, DEFAULT_SNAPSHOT_DIR, _CORE_KEYS
from .profile_schema import validate_profile
from .obs_check import check_obs_coverage, check_posture_counts, sum_posture_counts
from .evidence_check import extract_turn_uuids, flag_missing_pointers
from .run_model import detect_run_model
from .stage import assemble_posture
from .report import render_count_report, render_profile_report
from .models import InsightsReport


def _make_pointer_checker():
    """证据指针的 IO 核验器（带 per-file 缓存）：文件存在 + （带 uuid 时）该 turn
    uuid 真在文件里。每个会话文件只读一遍、一次性提取全部 uuid——evidence+highlights
    常 ~10 条指针且集中指向少数大 transcript，逐指针重扫全文件是数量级浪费。
    """
    cache: dict[str, set[str] | None] = {}   # None = 文件不存在/不可读
    def check(path: str, uuid: str | None) -> bool:
        if path not in cache:
            p = Path(path)
            if not p.is_file():
                cache[path] = None
            else:
                try:
                    with p.open(encoding="utf-8") as f:
                        cache[path] = extract_turn_uuids(f)
                except OSError:
                    cache[path] = None
        uuids = cache[path]
        if uuids is None:
            return False
        return True if uuid is None else uuid in uuids
    return check


def _metrics_dict(metrics) -> dict:
    # AggregateMetrics 的 property（landed_ratio/dropped_count）vars() 不含，手动补。
    return {**vars(metrics), "landed_ratio": metrics.landed_ratio,
            "dropped_count": metrics.dropped_count}


def _cmd_scan(args) -> int:
    cfg = load_config(resolve_config_path(args.config, args.plugin_root))
    days = args.days or cfg.lookback_days
    now = datetime.now(timezone.utc)
    since = None
    if getattr(args, "since", None):
        since = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    if getattr(args, "emit_batches", None):
        return _emit_batches(args, cfg, now, since)

    sessions = discover_sessions(Path(args.projects_dir), cfg.discovery_rules,
                                 days, now, since=since)
    stats = [compute_stats(s, cfg.short_turn_max_chars) for s in sessions]
    rep = InsightsReport(generated_at=now.isoformat(), lookback_days=days, sessions=stats,
                         included_projects=sorted({s.cwd for s in stats}),
                         completeness={"session_count": len(stats)})
    if args.profile_input:
        # outcome 校验逐 commit 跑 git 子进程，只有本分支消费，不在其他输出路径白付成本
        outcomes = [compute_outcome(s) for s in sessions]
        payload = {
            "generated_at": rep.generated_at, "lookback_days": days,
            "session_count": len(stats), "included_projects": rep.included_projects,
            "sessions_input": [build_session_input(se, st, oc)
                               for se, st, oc in zip(sessions, stats, outcomes)],
        }
        print(json.dumps(payload, ensure_ascii=False))
    elif args.json:
        print(json.dumps({"included_projects": rep.included_projects, "session_count": len(stats),
                          "sessions": [vars(s) for s in stats]}, ensure_ascii=False))
    else:
        html = render_count_report(rep)
        if args.out:
            Path(args.out).write_text(html, encoding="utf-8"); print(args.out)
        else:
            print(html)
    return 0


def _emit_batches(args, cfg, now, since) -> int:
    snap_dir = Path(getattr(args, "snapshot_dir", None) or DEFAULT_SNAPSHOT_DIR)
    prev = load_latest(dir=snap_dir)
    prev_generated = (prev or {}).get("generated_at")  # 旧格式快照缺键时按无基线降级，不崩
    last_date = date.fromisoformat(prev_generated[:10]) if prev_generated else None
    decision = decide_window(last_date, now.date())

    out_dir = Path(args.emit_batches)
    out_dir.mkdir(parents=True, exist_ok=True)
    # 清掉上一轮残留：批数变少时旧 batch 会被 verify-obs 误读成覆盖缺口；
    # 旧 obs/profile 不清，批次划分一变，专家会静默读到张冠李戴的数据。
    # 清理收在规则层，不留给 LLM 层跑 rm（会被权限分类器拦截，且违反双层分工）。
    # expert-*：编排者曾擅自把专家产出落盘（2026-06-11 实测），SKILL 已禁止，此处兜底
    for stale in (*out_dir.glob("batch-*.json"), *out_dir.glob("obs-*.json"),
                  *out_dir.glob("expert-*.json")):
        stale.unlink()
    (out_dir / "_aggregate.json").unlink(missing_ok=True)
    (out_dir / "profile.json").unlink(missing_ok=True)

    if decision.status == "too_soon":
        # 提前返回：无窗口可言，不做数据起点检测（省 IO）。data_start/truncated 缺省。
        too_soon_window = decision.to_dict()
        (out_dir / "_window.json").write_text(
            json.dumps(too_soon_window, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({"status": "too_soon", "batch_count": 0,
                          "message": decision.message,
                          "days_since_last": decision.days_since_last,
                          "window": too_soon_window}, ensure_ascii=False))
        return 0

    # --days 仅调试覆盖；覆盖时窗口标注（lookback/since_date/truncated）须随实际取数对齐，
    # 否则报告头与截断胶囊描述的是另一个窗口。
    days = args.days or decision.lookback_days
    since_date = (now.date() - timedelta(days=args.days)) if args.days else decision.since_date

    # 正常路径：检测实际数据起点，与 since_date 比对标注窗口是否被本机清理截断（隐患 E）。
    data_start = detect_data_start(Path(args.projects_dir))
    window_dict = decision.to_dict()
    window_dict["lookback_days"] = days
    window_dict["since_date"] = since_date.isoformat() if since_date else None
    window_dict["data_start"] = data_start
    window_dict["truncated"] = is_window_truncated(since_date, data_start)
    window_dict["mode"] = cfg.mode  # 取数范围进报告标注：防 mode=all 误跑时静默混入私人项目
    (out_dir / "_window.json").write_text(
        json.dumps(window_dict, ensure_ascii=False), encoding="utf-8")

    sessions = discover_sessions(Path(args.projects_dir), cfg.discovery_rules,
                                 days, now, since=since)
    stats = [compute_stats(s, cfg.short_turn_max_chars) for s in sessions]
    outcomes = [compute_outcome(s) for s in sessions]
    sessions_input = [build_session_input(se, st, oc)
                      for se, st, oc in zip(sessions, stats, outcomes)]
    # git 主锚采集：按仓库根归并会话时间窗（同仓多 cwd 防双计），窗口起点对齐取数窗口。
    since_dt = (datetime.combine(since_date, datetime.min.time(), tzinfo=timezone.utc)
                if since_date else now - timedelta(days=days))
    roots: dict = {}
    spans_by_root: dict = {}
    for s in sessions:
        a, b = parse_timestamp(s.first_ts), parse_timestamp(s.last_ts)
        if not (a and b):
            continue
        if s.cwd not in roots:
            roots[s.cwd] = repo_root(s.cwd)
        if roots[s.cwd]:
            spans_by_root.setdefault(roots[s.cwd], []).append((a, b))
    repo_outcomes = {root: repo_outcome(root, spans, since_dt)
                     for root, spans in spans_by_root.items()}

    # -- customization 信号扫描 --
    custom_skills = scan_custom_skills()
    custom_skill_count = len(custom_skills)
    # CLAUDE.md：检查各项目 cwd 下的 CLAUDE.md 在窗口内是否被修改
    claude_md_sessions = 0
    cutoff_mtime = since_dt
    seen_cwd = set()
    for s in sessions:
        if s.cwd in seen_cwd:
            continue
        seen_cwd.add(s.cwd)
        cmdf = Path(s.cwd) / "CLAUDE.md"
        if cmdf.is_file():
            try:
                mtime = datetime.fromtimestamp(cmdf.stat().st_mtime, tz=timezone.utc)
                if mtime >= cutoff_mtime:
                    claude_md_sessions += 1
            except OSError:
                pass

    metrics = aggregate_metrics(sessions, stats, outcomes, repo_outcomes=repo_outcomes,
                                 custom_skill_count=custom_skill_count,
                                 claude_md_sessions=claude_md_sessions)
    # 定制化信号聚合（供报告能力盲区使用）
    hook_config = detect_hook_config()
    customization_signals = compute_customization_signals(custom_skills,
                                                          claude_md_sessions,
                                                          hook_config)
    batches = make_batches(sessions_input)

    # project_breakdown 以 cwd 绝对路径（含项目名）做键，LLM 层与渲染均不消费；
    # 与快照同口径剥离，业务目录名不进入 LLM 上下文与 /tmp 产物。
    agg = {k: v for k, v in _metrics_dict(metrics).items() if k != "project_breakdown"}
    # 附上定制化信号（不进快照，仅进入 _aggregate.json → 报告渲染）
    agg["customization_signals"] = customization_signals
    (out_dir / "_aggregate.json").write_text(
        json.dumps(agg, ensure_ascii=False), encoding="utf-8")

    manifest_batches = []
    for i, batch in enumerate(batches, start=1):
        fname = f"batch-{i:02d}.json"
        fpath = out_dir / fname
        fpath.write_text(json.dumps(batch, ensure_ascii=False), encoding="utf-8")
        manifest_batches.append({"file": str(fpath), "session_count": len(batch)})

    manifest = {
        "status": decision.status,
        "batch_count": len(batches),
        "batches": manifest_batches,
        "included_projects": sorted({s.cwd for s in sessions}),
        "plugin_root": args.plugin_root or str(Path.cwd()),
        "window": window_dict,
        "aggregate": agg,
        "mode": cfg.mode,
    }
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


def _cmd_verify_obs(args) -> int:
    batch_sessions = {}
    batch_turn_counts: dict[str, int] = {}
    sid_to_batch: dict[str, str] = {}
    for f in sorted(Path(args.batches).glob("batch-*.json")):
        batch = json.loads(f.read_text(encoding="utf-8"))
        batch_sessions[str(f)] = [s["session_id"] for s in batch]
        for s in batch:
            batch_turn_counts[s["session_id"]] = len(s.get("turns") or [])
            sid_to_batch[s["session_id"]] = str(f)

    obs_ids: set[str] = set()
    obs_sessions: list = []
    unreadable = []
    import glob as _glob
    for fp in sorted(_glob.glob(args.obs_glob)):
        try:
            obs = json.loads(Path(fp).read_text(encoding="utf-8"))
            for s in obs["sessions"]:
                obs_ids.add(s["session_id"])
                obs_sessions.append(s)
        except (json.JSONDecodeError, KeyError, TypeError):
            unreadable.append(fp)

    result = check_obs_coverage(batch_sessions, obs_ids)
    # 姿势计数完整性（v2 口径地基）：缺/坏 posture_counts 按 batch 文件归类，
    # 编排端据 file 字段只补派受影响的批
    problems = check_posture_counts(batch_turn_counts, obs_sessions)
    result["posture_invalid"] = [{**p, "file": sid_to_batch.get(p["session_id"], "")}
                                 for p in problems]
    result["unreadable"] = unreadable
    if unreadable or result["posture_invalid"]:
        result["status"] = "mismatch"
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "ok" else 1


def _cmd_render_profile(args) -> int:
    profile = json.loads(Path(args.profile).read_text(encoding="utf-8"))
    terms = []
    try:
        terms = load_config(resolve_config_path(args.config, args.plugin_root)).business_terms
    except ConfigError as exc:
        # 配置缺失/不可读不阻断渲染，但兜底网退化为空名单必须出声，不能静默关掉隐私校验。
        # 只收 ConfigError：程序性 bug 须照常炸出，不得被误标成「配置失败」吞掉
        print(f"警告：读取配置失败（{exc}），业务词脱敏兜底按空名单执行", file=sys.stderr)
    errs = validate_profile(profile, business_terms=terms)
    if errs:
        print("画像校验失败：\n- " + "\n- ".join(errs), file=sys.stderr)
        return 2
    # 证据指针确定性核验：LLM 偶发编造路径或拿会话 id 冒充 turn uuid，
    # 指针回看是证据链的可信度承重点。未命中不剔除（行为描述仍可能成立），
    # 报告里明示「⚠ 指针未命中」并在 stderr 出声。
    profile, ptr_misses = flag_missing_pointers(profile, _make_pointer_checker())
    for ptr in ptr_misses:
        print(f"警告：证据指针未命中（已在报告中标注）：{ptr}", file=sys.stderr)
    metrics = None
    if args.metrics:
        metrics = json.loads(Path(args.metrics).read_text(encoding="utf-8"))
    # 四档分布（v2 口径）：读全部 obs 聚合 extractor 的逐 turn 语义分档计数
    # （语义判定收在看得见原文的阶段一），AskUserQuestion 答题按协议硬信号
    # 并入 L2，算术组装收在规则层。obs 缺失/不可读不阻断渲染，但必须 stderr
    # 出声并按全零分布降级（探索期兜底），不得静默装作有数。
    obs_sessions: list = []
    if getattr(args, "obs_glob", None):
        import glob as _glob
        for fp in sorted(_glob.glob(args.obs_glob)):
            try:
                obs = json.loads(Path(fp).read_text(encoding="utf-8"))
                obs_sessions.extend(obs["sessions"])
            except (json.JSONDecodeError, KeyError, TypeError, OSError):
                print(f"警告：obs 不可读，姿势分布按缺失计：{fp}", file=sys.stderr)
    if not obs_sessions:
        print("警告：未读到任何 obs（--obs-glob 缺省或无命中），姿势分布按全零渲染",
              file=sys.stderr)
    mm = metrics or {}
    assembled = assemble_posture(sum_posture_counts(obs_sessions),
                                 mm.get("option_pick_count", 0))
    profile["posture_distribution"] = assembled
    snap_dir = Path(args.snapshot_dir)
    prev = load_latest(dir=snap_dir)
    prev_metrics = (prev or {}).get("metrics")  # 旧格式快照缺 metrics 时按无基线降级
    diff = diff_metrics(metrics, prev_metrics) if metrics is not None else None
    window = None
    if getattr(args, "window", None):
        window = json.loads(Path(args.window).read_text(encoding="utf-8"))
    meta = {"generated_at": datetime.now(timezone.utc).isoformat(),
            "lookback_days": (window or {}).get("lookback_days", args.days or 30),
            "window": window,
            "session_count": args.session_count, "included_projects": args.project or []}
    # 运行元信息（起始时间/编排规模）：编排端自报，全可缺省；缺省时不进 meta，
    # 报告端整行不渲染（向后兼容旧调用）。
    run = {k: v for k, v in {"started_at": args.run_started,
                             "agents": args.run_agents}.items() if v}
    # 模型名不收编排端自报（LLM 自报模型 ID 会编造），由规则层从当前 CC 会话
    # transcript 确定性提取；识别不到就整段省略，宁缺勿假。
    model = detect_run_model(os.environ.get("CLAUDE_CODE_SESSION_ID"),
                             Path(args.projects_dir))
    if model:
        run["model"] = model
    if run:
        meta["run"] = run
    html = render_profile_report(profile, meta, metrics, diff)
    out = args.out or str(Path.cwd() / f"aci-report-{datetime.now().date().isoformat()}.html")
    Path(out).write_text(html, encoding="utf-8")
    if not args.no_snapshot:
        outcome = profile.get("outcome", {}) or {}
        # 快照只用于标量核心指标增量对比，按 _CORE_KEYS 白名单收紧：
        # 黑名单式会让 aggregate 新增的 dict 字段(token_usage/trend)与含项目名的
        # project_breakdown 静默泄入快照。
        snap_metrics = {k: v for k, v in (metrics or {}).items() if k in _CORE_KEYS}
        save_snapshot(snap_metrics, profile.get("posture_distribution", {}),
                      {"landed": outcome.get("landed"), "total": outcome.get("total")},
                      meta["generated_at"], window or {"lookback_days": meta["lookback_days"]},
                      dir=snap_dir)
    print("姿势分布: " + " · ".join(
        f"{t} {assembled[t]:.0%}" for t in ("L1", "L2", "L3", "L4")))
    print(out)
    return 0


def _cmd_init(args) -> int:
    from .config import DEFAULT_USER_CONFIG
    from .init_wizard import (aggregate_sources, build_config_toml, collect_sources,
                              parse_selection, render_menu)
    idents, counts = collect_sources(Path(args.projects_dir))
    groups = aggregate_sources(idents, counts)
    if not groups:
        print('未发现任何会话来源；无需配置，零配置即 mode = "all"。')
        return 0
    print("扫描本机会话 git 来源：")
    print(render_menu(groups))
    try:
        raw = input('选择属于团队的来源（逗号分隔序号，留空 = 个人形态 mode = "all"）: ')
    except EOFError:
        print("非交互环境，未写配置。", file=sys.stderr)
        return 1
    try:
        selected = parse_selection(raw, groups)
    except ValueError as exc:
        print(f"输入无效：{exc}", file=sys.stderr)
        return 2
    out = Path(args.out) if args.out else DEFAULT_USER_CONFIG
    if out.exists():
        try:
            ans = input(f"{out} 已存在，覆盖？[y/N] ")
        except EOFError:
            print("非交互环境，未确认覆盖，未写配置。", file=sys.stderr)
            return 1
        if ans.strip().lower() != "y":
            print("已取消。")
            return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_config_toml(selected), encoding="utf-8")
    print(out)
    return 0


def _cmd_auto_scan(args) -> int:
    """后台自动扫描（由 SessionEnd hook 触发）。

    Lock file 防重入：同一天只执行一次；失败静默退出不打扰用户。
    """

    lock_dir = Path.home() / ".ai-coding-insights"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_file = lock_dir / ".auto-scan.lock"
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # 检查 lock file
    if lock_file.is_file():
        try:
            prev = lock_file.read_text(encoding="utf-8").strip()
            if prev == today_str:
                return 0  # 今天已执行，静默跳过
        except OSError:
            pass

    # 原子写入 lock（先写 tmp 再 rename）
    tmp = lock_file.with_name(lock_file.name + ".tmp")
    try:
        tmp.write_text(today_str, encoding="utf-8")
        tmp.replace(lock_file)
    except OSError:
        return 0  # 写锁失败不阻塞

    # 执行扫描 + 渲染
    try:
        now = datetime.now(timezone.utc)
        cfg = load_config(resolve_config_path(args.config, args.plugin_root))
        days = args.days or cfg.lookback_days

        # 读上次快照决定 since（防御：快照缺 generated_at 或格式异常时安全降级）
        prev_snapshot = load_latest(dir=Path(args.snapshot_dir))
        prev_generated = (prev_snapshot or {}).get("generated_at")
        since_str = prev_generated[:10] if prev_generated else ""
        if since_str:
            try:
                since = datetime.strptime(since_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                since = now - timedelta(days=days)
        else:
            since = now - timedelta(days=days)

        window_decision = decide_window(
            prev_generated[:10] if prev_generated else None,
            now.date().isoformat())
        if window_decision.status in ("too_soon",):
            return 0

        sessions = discover_sessions(Path(args.projects_dir), cfg.discovery_rules,
                                     days, now, since=since)
        if not sessions:
            return 0

        stats = [compute_stats(s, cfg.short_turn_max_chars) for s in sessions]
        outcomes = [compute_outcome(s) for s in sessions]
        custom_skills = scan_custom_skills()
        custom_skill_count = len(custom_skills)
        claude_md_sessions = 0
        cutoff_mtime = since
        seen_cwd = set()
        for s in sessions:
            if s.cwd in seen_cwd:
                continue
            seen_cwd.add(s.cwd)
            cmdf = Path(s.cwd) / "CLAUDE.md"
            if cmdf.is_file():
                try:
                    mtime = datetime.fromtimestamp(cmdf.stat().st_mtime, tz=timezone.utc)
                    if mtime >= cutoff_mtime:
                        claude_md_sessions += 1
                except OSError:
                    pass

        # auto_scan 不做昂贵的 git log，repo_outcomes 按空字典
        metrics = aggregate_metrics(sessions, stats, outcomes,
                                     repo_outcomes={},
                                     custom_skill_count=custom_skill_count,
                                     claude_md_sessions=claude_md_sessions)
        metrics_dict = _metrics_dict(metrics)
        # 定制化信号（供报告能力盲区使用）
        hook_config = detect_hook_config()
        metrics_dict["customization_signals"] = compute_customization_signals(
            custom_skills, claude_md_sessions, hook_config)

        # 生成简化报告
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"aci-auto-{today_str}.html"

        # 姿势分布（v2 口径）：auto_scan 无 LLM 语义判定，姿势计数按全零；
        # assemble_posture 基于 option_pick_count 做算术组装（AskUserQuestion 答题并入 L2）。
        posture = assemble_posture({"L1": 0, "L2": 0, "L3": 0, "L4": 0},
                                   metrics_dict.get("option_pick_count", 0))
        profile = {"posture_distribution": posture}
        meta = {
            "generated_at": now.isoformat(),
            "session_count": len(sessions),
            "mode": cfg.mode,
        }
        html = render_profile_report(profile, meta, metrics=metrics_dict, diff=None)
        out_file.write_text(html, encoding="utf-8")

        # 保存快照，确保下次 auto-scan 窗口增量推进
        outcome = {}
        snap_metrics = {k: v for k, v in metrics_dict.items() if k in _CORE_KEYS}
        save_snapshot(snap_metrics, posture,
                      {"landed": outcome.get("landed"), "total": outcome.get("total")},
                      meta["generated_at"],
                      {"lookback_days": days, "status": window_decision.status,
                       "truncated": False, "mode": cfg.mode},
                      dir=Path(args.snapshot_dir))
    except Exception:
        print(f"auto-scan 执行失败，跳过本次自动评估", file=sys.stderr)
        return 0  # 异常静默退出（不打扰用户主流程）

    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ai_coding_insights")
    sub = ap.add_subparsers(dest="cmd")
    sc = sub.add_parser("scan")
    sc.add_argument("--projects-dir", default=str(Path.home()/".claude"/"projects"))
    sc.add_argument("--config", default=None)
    sc.add_argument("--plugin-root", default=None)
    sc.add_argument("--days", type=int, default=None)
    sc.add_argument("--json", action="store_true")
    sc.add_argument("--profile-input", action="store_true")
    sc.add_argument("--emit-batches", default=None)
    sc.add_argument("--since", default=None)
    sc.add_argument("--out", default=None)
    sc.add_argument("--snapshot-dir", default=str(DEFAULT_SNAPSHOT_DIR))
    vo = sub.add_parser("verify-obs")
    vo.add_argument("--batches", required=True)
    vo.add_argument("--obs-glob", required=True)
    it = sub.add_parser("init")
    it.add_argument("--projects-dir", default=str(Path.home()/".claude"/"projects"))
    it.add_argument("--out", default=None)
    rp = sub.add_parser("render-profile")
    rp.add_argument("--profile", required=True)
    # 缺省时落当前目录 aci-report-<日期>.html——日期由规则层算，不让 LLM 填
    rp.add_argument("--out", default=None)
    rp.add_argument("--projects-dir", default=str(Path.home()/".claude"/"projects"))
    rp.add_argument("--days", type=int, default=None)
    rp.add_argument("--session-count", type=int, default=0)
    rp.add_argument("--project", action="append")
    rp.add_argument("--metrics", default=None)
    rp.add_argument("--snapshot-dir", default=str(DEFAULT_SNAPSHOT_DIR))
    rp.add_argument("--no-snapshot", action="store_true")
    rp.add_argument("--config", default=None)
    rp.add_argument("--plugin-root", default=None)
    rp.add_argument("--window", default=None)
    rp.add_argument("--run-started", default=None)   # ISO 8601，编排启动时刻
    rp.add_argument("--run-agents", type=int, default=None)
    rp.add_argument("--obs-glob", default=None)   # obs-*.json glob；四档分布的计数来源
    au = sub.add_parser("auto-scan")
    au.add_argument("--out-dir", required=True)
    au.add_argument("--config", default=None)
    au.add_argument("--plugin-root", default=None)
    au.add_argument("--projects-dir", default=str(Path.home()/".claude"/"projects"))
    au.add_argument("--days", type=int, default=None)
    au.add_argument("--snapshot-dir", default=str(DEFAULT_SNAPSHOT_DIR))
    # 向后兼容：Plan 1 的 SKILL.md 调用无子命令（如 `--config X --out Y`）。
    # argparse 子命令模式下，若 argv 首个 token 不是已知子命令，顶层 parse_args 会
    # 把后续 option 的取值误判为子命令选择并直接 SystemExit，根本到不了下面的
    # `if args.cmd is None` 重解析。故在顶层解析前先判定：首个非子命令 → 注入 "scan"。
    raw = list(argv) if argv is not None else sys.argv[1:]
    if raw and raw[0] not in sub.choices:
        raw = ["scan"] + raw
    args = ap.parse_args(raw)
    try:
        if args.cmd == "init":
            return _cmd_init(args)
        if args.cmd == "verify-obs":
            return _cmd_verify_obs(args)
        if args.cmd == "render-profile":
            return _cmd_render_profile(args)
        if args.cmd == "auto-scan":
            return _cmd_auto_scan(args)
        # 默认 / "scan"：向后兼容（无子命令时按 scan 解析）
        if args.cmd is None:
            args = sc.parse_args(raw)
        return _cmd_scan(args)
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2
