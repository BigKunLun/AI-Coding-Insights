import argparse, json, os, shutil, sys
from datetime import datetime, timezone, date, timedelta
from pathlib import Path
from .config import load_config, resolve_config_path, ConfigError
from .window import decide_window
from .discovery import discover_sessions, detect_data_start, is_window_truncated
from .signals import compute_stats, aggregate_metrics
from .outcome import compute_outcome
from .git_outcome import repo_outcome, repo_root
from .profile_input import build_session_input
from .batch import make_batches
from .customization import (compute_customization_signals, count_project_md_sessions,
                            detect_hook_config, scan_custom_skills)
from .snapshot import (save_snapshot, load_latest, load_all, diff_metrics,
                       DEFAULT_SNAPSHOT_DIR, _CORE_KEYS)
from .calibrate import (REPLAY_WINDOW_DAYS, calibrate,
                        format_report as format_calibration,
                        replay_snapshot, replay_windows, window_indices)
from .profile_schema import validate_profile
from .obs_check import check_obs_coverage, check_posture_counts, sum_posture_counts
from .evidence_check import POINTER_UUID_SOURCES, flag_missing_pointers, turn_uuids_for
from .run_model import detect_run_model
from .rolling_log import append_rolling_log
from .parse_health import compute_parse_health
from .stage import assemble_posture
from .report import render_count_report, render_profile_report
from .view_model import build_view
from .models import InsightsReport
from .sources import (CLAUDE_CODE, SOURCE_NAMES, UnknownSourceError, detect_source,
                      get_source, resolve_root)
from .timeutil import parse_timestamp


def _resolve_source(args):
    """本次取数用哪家 harness 的会话。

    显式 `--source` 优先，否则**看触发环境**（在哪个 harness 里被调起就分析哪家，
    见 `sources.detect_source`）——刻意不去 PATH 上探测用户装了什么：装了不等于在用。
    """
    name = getattr(args, "source", None) or detect_source()
    try:
        return get_source(name)
    except UnknownSourceError as exc:
        # 复用 ConfigError 的出口：main 已统一兜它并打中文提示 + 退出码 2
        raise ConfigError(str(exc)) from exc


def _resolve_projects_dir(args, src) -> Path:
    """会话数据根：显式 `--projects-dir` 优先，否则取该来源默认根。

    默认值不能写死在 argparse 里——写死就等于把 `~/.claude/projects` 强加给
    Codex/opencode 用户，扫出 0 个会话还不报错。
    """
    return resolve_root(src, getattr(args, "projects_dir", None))


def _make_pointer_checker(source_name: str = CLAUDE_CODE):
    """证据指针的 IO 核验器（带 per-file 缓存）：文件存在 + （带 uuid 时）该 turn
    uuid 真在文件里。每个会话文件只读一遍、一次性提取全部 uuid——evidence+highlights
    常 ~10 条指针且集中指向少数大 transcript，逐指针重扫全文件是数量级浪费。

    uuid 的取法按来源分派（`evidence_check.turn_uuids_for`）。该来源**不支持** uuid
    回看时（返回 None），退化为只核文件存在性——绝不因此把每条指针都判成未命中，
    那会产出一份「证据全对不上」的假警报报告。
    """
    _MISSING = object()                      # 文件不存在/不可读
    _NO_UUID_CHECK = object()                # 本来源不支持 uuid 回看，只核文件存在
    cache: dict[str, object] = {}

    def check(path: str, uuid: str | None) -> bool:
        if path not in cache:
            p = Path(path)
            if not p.is_file():
                cache[path] = _MISSING
            else:
                uuids = turn_uuids_for(p, source_name)
                cache[path] = _NO_UUID_CHECK if uuids is None else uuids
        entry = cache[path]
        if entry is _MISSING:
            return False
        if uuid is None or entry is _NO_UUID_CHECK:
            return True
        return uuid in entry
    return check




def _metrics_dict(metrics) -> dict:
    # AggregateMetrics 的 property（landed_ratio/dropped_count）vars() 不含，手动补。
    return {**vars(metrics), "landed_ratio": metrics.landed_ratio,
            "dropped_count": metrics.dropped_count}


def _write_html(out: str, html: str) -> str:
    """落 HTML 到 out：展开 ~、按需建父目录，返回解析后的绝对/相对路径字符串。
    避免 `--out ~/x.html` 写出字面量 '~' 目录，或父目录缺失抛裸 traceback（main 只兜 ConfigError）。"""
    p = Path(out).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(html, encoding="utf-8")
    return str(p)


def _cmd_scan(args) -> int:
    cfg = load_config(resolve_config_path(args.config, args.plugin_root))
    days = args.days or cfg.lookback_days
    now = datetime.now(timezone.utc)
    since = None
    if getattr(args, "since", None):
        try:
            since = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError as exc:
            # 格式错的 --since 不应抛裸 traceback（main 只兜 ConfigError）
            raise ConfigError(f"--since 需为 YYYY-MM-DD 格式：{args.since!r}") from exc

    src = _resolve_source(args)
    projects_dir = _resolve_projects_dir(args, src)
    if getattr(args, "emit_batches", None):
        return _emit_batches(args, cfg, now, since, src, projects_dir)

    sessions = discover_sessions(projects_dir, cfg.discovery_rules,
                                 days, now, since=since, source=src)
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
            print(_write_html(args.out, html))
        else:
            print(html)
    return 0


# 规则层 ↔ LLM 层文件契约的版本号。**改中间产物的结构就要 +1**（加可选字段不用）。
# 为什么需要它：playbook 装在用户机器上、规则层由 uvx 每次拉最新，两边会各自漂移。
# 没有版本号时，旧 playbook 配新规则层的表现是「读到的键不存在 → 当空处理 → 安静产出
# 错报告」；有了它，playbook 第 1 步就能对不上号并要求用户重装——把静默失配换成响亮失败。
# 真相源在此，playbook 里抄了一份，`tests/test_skill_contract.py` 比对两侧。
MANIFEST_SCHEMA_VERSION = 2


def _emit_batches(args, cfg, now, since, src, projects_dir) -> int:
    snap_dir = Path(getattr(args, "snapshot_dir", None) or DEFAULT_SNAPSHOT_DIR)
    prev = load_latest(dir=snap_dir)
    prev_generated = (prev or {}).get("generated_at")  # 旧格式快照缺键时按无基线降级，不崩
    last_date = date.fromisoformat(prev_generated[:10]) if prev_generated else None
    decision = decide_window(last_date, now.date())

    # 展开 ~：落点已是 home 路径（~/.ai-coding-insights/run），字面 ~ 未经 shell 展开时
    # 会在 cwd 下造出名为 '~' 的脏目录（同 _write_html 的防护）。
    out_dir = Path(args.emit_batches).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    # 清掉上一轮残留：批数变少时旧 batch 会被 verify-obs 误读成覆盖缺口；
    # 旧 obs/profile 不清，批次划分一变，专家会静默读到张冠李戴的数据。
    # 清理收在规则层，不留给 LLM 层跑 rm（会被权限分类器拦截，且违反双层分工）。
    # expert-*：编排者曾擅自把专家产出落盘（2026-06-11 实测），SKILL 已禁止，此处兜底
    for stale in (*out_dir.glob("batch-*.json"), *out_dir.glob("obs-*.json"),
                  *out_dir.glob("expert-*.json")):
        stale.unlink(missing_ok=True)   # glob 后文件被并发/外部删除时不抛 FileNotFoundError
    (out_dir / "_aggregate.json").unlink(missing_ok=True)
    (out_dir / "profile.json").unlink(missing_ok=True)

    if decision.status == "too_soon":
        # 提前返回：无窗口可言，不做数据起点检测（省 IO）。data_start/truncated 缺省。
        too_soon_window = decision.to_dict()
        (out_dir / "_window.json").write_text(
            json.dumps(too_soon_window, ensure_ascii=False), encoding="utf-8")
        # schema_version 在 too_soon 分支也要给：编排端第一件事就是对版本，
        # 若这条分支不带版本号，它会在「最该早停」的时候反而对不上号。
        print(json.dumps({"status": "too_soon", "batch_count": 0,
                          "message": decision.message,
                          "days_since_last": decision.days_since_last,
                          "schema_version": MANIFEST_SCHEMA_VERSION,
                          "window": too_soon_window}, ensure_ascii=False))
        return 0

    # --days 仅调试覆盖；覆盖时窗口标注（lookback/since_date/truncated）须随实际取数对齐，
    # 否则报告头与截断胶囊描述的是另一个窗口。
    days = args.days or decision.lookback_days
    since_date = (now.date() - timedelta(days=args.days)) if args.days else decision.since_date

    # 正常路径：检测实际数据起点，与 since_date 比对标注窗口是否被本机清理截断（隐患 E）。
    data_start = detect_data_start(projects_dir, source=src)
    window_dict = decision.to_dict()
    window_dict["lookback_days"] = days
    window_dict["since_date"] = since_date.isoformat() if since_date else None
    window_dict["data_start"] = data_start
    window_dict["truncated"] = is_window_truncated(since_date, data_start)
    window_dict["mode"] = cfg.mode  # 取数范围进报告标注：防 mode=all 误跑时静默混入私人项目
    window_dict["source"] = src.name  # 数据来自哪家 harness：跨来源不可直接比，须进报告标注
    (out_dir / "_window.json").write_text(
        json.dumps(window_dict, ensure_ascii=False), encoding="utf-8")

    sessions = discover_sessions(projects_dir, cfg.discovery_rules,
                                 days, now, since=since, source=src)
    stats = [compute_stats(s, cfg.short_turn_max_chars) for s in sessions]
    outcomes = [compute_outcome(s) for s in sessions]
    sessions_input = [build_session_input(se, st, oc)
                      for se, st, oc in zip(sessions, stats, outcomes)]
    # git 主锚采集：按仓库根归并会话编辑文件集（同仓多 cwd 防双计），提交按文件重叠归属，
    # 窗口起点对齐取数窗口。文件名只在本机内求交，不进任何中间产物。
    since_dt = (datetime.combine(since_date, datetime.min.time(), tzinfo=timezone.utc)
                if since_date else now - timedelta(days=days))
    roots: dict = {}
    edited_by_root: dict = {}
    for s in sessions:
        if s.cwd not in roots:
            roots[s.cwd] = repo_root(s.cwd)
        root = roots[s.cwd]
        if root:
            edited_by_root.setdefault(root, set()).update(s.edited_paths)
    repo_outcomes = {root: repo_outcome(root, edited, since_dt)
                     for root, edited in edited_by_root.items()}

    # -- customization 信号扫描（按来源分派：各家的自建扩展目录与项目约定文件名不同）--
    custom_skills = scan_custom_skills(source=src.name)
    custom_skill_count = len(custom_skills)
    claude_md_sessions = count_project_md_sessions(sessions, since_dt, source=src.name)

    metrics = aggregate_metrics(sessions, stats, outcomes, repo_outcomes=repo_outcomes,
                                 custom_skill_count=custom_skill_count,
                                 claude_md_sessions=claude_md_sessions,
                                 source=src)
    # 定制化信号聚合（供报告能力盲区使用）
    # 必须带 source：hook 探测读的是 CC 的 ~/.claude/settings.json，不传来源就会把
    # CC 的 hook 配置算到 Codex/opencode 头上——实测踩过：Codex 报告里冒出「已接线
    # 6 类 hook 事件」，而 Codex 压根没有 hook 机制。
    hook_config = detect_hook_config(source=src.name)
    customization_signals = compute_customization_signals(custom_skills,
                                                          claude_md_sessions,
                                                          hook_config)
    batches = make_batches(sessions_input)

    # project_breakdown 以 cwd 绝对路径（含项目名）做键，LLM 层与渲染均不消费；
    # 与快照同口径剥离，业务目录名不进入 LLM 上下文与中间产物。
    agg = {k: v for k, v in _metrics_dict(metrics).items() if k != "project_breakdown"}
    # 附上定制化信号（不进快照，仅进入 _aggregate.json → 报告渲染）
    agg["customization_signals"] = customization_signals
    # 提取健康度金丝雀（版本漂移雷达）：进 _aggregate.json → 报告渲染；
    # 纯结构事实（版本/类型/存在率），不进快照、无业务语义。
    agg["parse_health"] = compute_parse_health(sessions)
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
        # 已展开的绝对中间产物目录，回填给 SKILL：LLM 据此 Write obs/profile、glob obs。
        # SKILL 不能写死 ~ / ${HOME}（Write 工具不展开，会造出字面 ~ 目录）。
        "batches_dir": str(out_dir.resolve()),
        "window": window_dict,
        "aggregate": agg,
        "mode": cfg.mode,
        # 来源名：编排端据此挑对应的 playbook 分支（有无子代理 / 该不该提 CC 专属能力），
        # 也据此把「本报告分析的是哪家会话」写进小结。
        "source": src.name,
        # 本来源**能测到**什么（正面声明；反面是 aggregate.unmeasured）。
        "capabilities": sorted(src.capabilities),
        "schema_version": MANIFEST_SCHEMA_VERSION,
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
    # 来源先读出来：指针核验方式按来源分派（各家 turn uuid 形状不同）
    metrics = None
    if args.metrics:
        metrics = json.loads(Path(args.metrics).read_text(encoding="utf-8"))
    src_name = str((metrics or {}).get("source") or CLAUDE_CODE)
    profile, ptr_misses = flag_missing_pointers(profile, _make_pointer_checker(src_name))
    for ptr in ptr_misses:
        print(f"警告：证据指针未命中（已在报告中标注）：{ptr}", file=sys.stderr)
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
    if src_name not in POINTER_UUID_SOURCES:
        # 少了一道核验就得说出来。不说的话，报告里没有 ⚠ 会被读成「指针都核过、都对」，
        # 而实际是压根没核 —— 这正是「不报错的错报告」。
        print(f"警告：来源 {src_name} 暂不支持证据指针的 turn 级回看，本次只核了文件存在性",
              file=sys.stderr)
    profile["posture_distribution"] = assembled
    snap_dir = Path(args.snapshot_dir)
    prev = load_latest(dir=snap_dir)
    prev_metrics = (prev or {}).get("metrics")  # 旧格式快照缺 metrics 时按无基线降级
    prev_rubric = (prev or {}).get("posture_rubric")  # 跨口径边界 → diff 对受影响 key 不出同比
    diff = (diff_metrics(metrics, prev_metrics, prev_rubric=prev_rubric)
            if metrics is not None else None)
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
    # 只对 claude-code 来源做：提取逻辑读的是 CC transcript 的 message.model，
    # 别家格式不同，硬套只会拿到 None 或错值。来源以 metrics 为准（那是本次取数
    # 的真相源），metrics 缺席时退到触发环境。
    _src_name = str((metrics or {}).get("source") or "") or detect_source()
    if _src_name == CLAUDE_CODE:
        model = detect_run_model(os.environ.get("CLAUDE_CODE_SESSION_ID"),
                                 _resolve_projects_dir(args, get_source(CLAUDE_CODE)))
        if model:
            run["model"] = model
    if run:
        meta["run"] = run
    # stdout 接缝：posture_state / stage_name 取自 build_view——它是渲染同款判定的
    # 唯一真相源且为纯函数，cli 与报告各调一次必得同值（不再靠出参把值捞回来）。
    view = build_view(profile, meta, metrics, diff)
    html = render_profile_report(profile, meta, metrics, diff)
    out = args.out or str(Path.cwd() / f"aci-report-{datetime.now().date().isoformat()}.html")
    out = _write_html(out, html)
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
    # 接缝：姿态健康态 / 成熟度档位（值由 view_model 算定，规则层单一真相源）。
    # metrics 缺省时无判定，打「样本不足」/「—」占位，仍保证两行恒在（SKILL 据字段名取值）。
    print("姿态健康态: " + (view["posture_state"] or "样本不足"))
    print("成熟度档位: " + (view["stage_name"] or "—"))
    print(out)
    return 0


def _cmd_init(args) -> int:
    from .config import DEFAULT_USER_CONFIG
    from .init_wizard import (aggregate_sources, build_config_toml, collect_sources,
                              parse_selection, render_menu)
    src = _resolve_source(args)
    idents, counts = collect_sources(_resolve_projects_dir(args, src), source=src)
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


# auto-scan 省掉的 git 主锚测量：既进 unmeasured（渲染打「未测量」），也从快照剔除
# （防下次 0→真值的假上涨）。两处同一份常量，改一处不会漏另一处。
_UNMEASURED_GIT = ("git_landed_count", "git_commit_total", "landed_ratio")


def _cmd_auto_scan(args) -> int:
    """后台自动扫描（由 SessionEnd hook 触发）。

    Lock file 防重入：同一天只执行一次；失败静默退出不打扰用户。
    """

    # state 目录（lock + 滚动日志）默认在 home，--state-dir 可注入（测试 hermetic、
    # 也便于团队改放统一位置）。
    state_dir = (Path(args.state_dir) if getattr(args, "state_dir", None)
                 else Path.home() / ".ai-coding-insights")
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_file = state_dir / ".auto-scan.lock"
    log_file = state_dir / "auto-scan.log"
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
        append_rolling_log(log_file, f"{now.isoformat()} start day={today_str}")
        cfg = load_config(resolve_config_path(args.config, args.plugin_root))
        src = _resolve_source(args)
        projects_dir = _resolve_projects_dir(args, src)

        # 读上次快照决定窗口（防御：快照缺 generated_at 或格式异常时安全降级）
        prev_snapshot = load_latest(dir=Path(args.snapshot_dir))
        prev_generated = (prev_snapshot or {}).get("generated_at")
        window_decision = decide_window(
            date.fromisoformat(prev_generated[:10]) if prev_generated else None,
            now.date())
        if window_decision.status == "too_soon":
            append_rolling_log(log_file, f"{now.isoformat()} skip too_soon")
            return 0
        # 取数天数对齐窗口决策（首次=floor，增量=min(N,cap)），不再用 cfg.lookback_days——
        # 否则快照/报告标 cfg 天数却按 decision 的 since 取数，两者割裂（与 emit-batches 同口径）。
        days = args.days or window_decision.lookback_days

        since_str = prev_generated[:10] if prev_generated else ""
        if since_str:
            try:
                since = datetime.strptime(since_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                since = now - timedelta(days=days)
        else:
            since = now - timedelta(days=days)

        sessions = discover_sessions(projects_dir, cfg.discovery_rules,
                                     days, now, since=since, source=src)
        if not sessions:
            append_rolling_log(log_file, f"{now.isoformat()} skip empty-scan (游标不推进)")
            return 0

        stats = [compute_stats(s, cfg.short_turn_max_chars) for s in sessions]
        outcomes = [compute_outcome(s) for s in sessions]
        custom_skills = scan_custom_skills(source=src.name)
        custom_skill_count = len(custom_skills)
        claude_md_sessions = count_project_md_sessions(sessions, since, source=src.name)

        # auto_scan 不做昂贵的 git log，repo_outcomes 按空字典。这三个 git 字段因此是
        # 「本次没测」而非真值 0，显式声明进 unmeasured——否则报告会把 0 当真值渲染出
        # 「落地为零」这种错误结论（下面 snap_metrics 的剔除只挡住了快照假趋势那一侧）。
        metrics = aggregate_metrics(sessions, stats, outcomes,
                                     repo_outcomes={},
                                     custom_skill_count=custom_skill_count,
                                     claude_md_sessions=claude_md_sessions,
                                     source=src,
                                     extra_unmeasured=_UNMEASURED_GIT)
        metrics_dict = _metrics_dict(metrics)
        # 定制化信号（供报告能力盲区使用）
        hook_config = detect_hook_config(source=src.name)
        metrics_dict["customization_signals"] = compute_customization_signals(
            custom_skills, claude_md_sessions, hook_config)
        # 提取健康度金丝雀（版本漂移雷达）：它存在的意义正是守护这条无人值守路径，
        # 必须随报告渲染。sessions 已在手、计算廉价，与交互式 scan 同口径写入。
        metrics_dict["parse_health"] = compute_parse_health(sessions)

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
        # auto-scan 传 repo_outcomes={} 省成本，git 指标实为「本次未测量」而非真值 0。
        # 不把它们以 0 写进快照——否则下次真实报告会把 0→真值当成假上涨（diff 的 None
        # 守卫只对 None 生效，0 不算无基线）。这三个 key 不进 snap_metrics → 下次 prev 缺失 → 不出箭头。
        snap_metrics = {k: v for k, v in metrics_dict.items()
                        if k in _CORE_KEYS and k not in _UNMEASURED_GIT}
        # 窗口标注与 emit-batches 同口径：透传 decision 全字段，并补 data_start/truncated
        # 检测（不再硬编码 truncated=False），让快照/报告如实反映本机清理截断。
        data_start = detect_data_start(projects_dir, source=src)
        window_dict = window_decision.to_dict()
        window_dict["lookback_days"] = days
        window_dict["data_start"] = data_start
        window_dict["truncated"] = is_window_truncated(window_decision.since_date, data_start)
        window_dict["mode"] = cfg.mode
        window_dict["source"] = src.name
        save_snapshot(snap_metrics, posture,
                      {"landed": outcome.get("landed"), "total": outcome.get("total")},
                      meta["generated_at"], window_dict,
                      dir=Path(args.snapshot_dir))
        append_rolling_log(log_file,
                           f"{now.isoformat()} ok sessions={len(sessions)} -> {out_file}")
    except Exception as exc:
        # 对用户仍静默退出（不打扰主流程），但真实异常类型+消息落滚动日志，
        # 让「auto-scan 静默失效」这类问题从此可诊断（上次正是因无日志而长期不可见）。
        append_rolling_log(log_file,
                           f"{datetime.now(timezone.utc).isoformat()} "
                           f"ERROR {type(exc).__name__}: {exc}")
        print(f"auto-scan 执行失败，跳过本次自动评估", file=sys.stderr)
        return 0

    return 0


# 删除白名单（相对 state_dir 的名字）。承重：清单写死在此，绝不含 config.toml（在
# ~/.claude 那棵树下）与 sessions 原文——reset 只清自己产出的东西。这 4 个 basename 横跨
# 3 处真相源（见 CLAUDE.md 接缝一节）：snapshots 引用 DEFAULT_SNAPSHOT_DIR 随动；reports /
# run 的真相源在 bash hook / SKILL.md，无 Python 常量可引，改它们须手动同步此处。
# 注意 .auto-scan.lock 故意不在删除集——它由 reset「置今日」而非删除（见 _cmd_reset）。
_RESET_PRODUCTS = (DEFAULT_SNAPSHOT_DIR.name, "reports", "run", "auto-scan.log")


def reset_targets(state_dir: Path) -> list[Path]:
    """纯函数：返回该删的产物路径（删除白名单，不含 lock，不做存在性判断/不碰 IO）。"""
    return [state_dir / name for name in _RESET_PRODUCTS]


def _cmd_reset(args) -> int:
    """清空本机可再生产物 + 接管当日 auto-scan 锁，让用户干净重测。

    删 --state-dir 下的白名单产物；并把今日写进 .auto-scan.lock——SessionEnd 的
    auto-scan 见今日锁即整天跳过，不会抢先写今日快照重新武装 30 天闸门（删锁反而
    解除抑制，正是「reset 后重跑仍 too_soon」的根因）。--dry-run 只列不动。
    """
    state_dir = Path(args.state_dir).expanduser()
    dry = args.dry_run
    removed = []
    for target in reset_targets(state_dir):
        # 符号链接必须先判：悬空链接 exists()==False 会被漏清；指向目录的链接交给
        # rmtree 会抛 OSError 且可能跟随删到链外——一律只摘链接自身，绝不跟随。
        if target.is_symlink():
            if not dry:
                target.unlink()
        elif not target.exists():
            continue
        elif not dry:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink(missing_ok=True)
        removed.append(target)

    # 接管当日锁：把今日写进 lock，压住 SessionEnd 的 auto-scan 抢占刚清空的游标。
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lock = state_dir / ".auto-scan.lock"
    if not dry:
        state_dir.mkdir(parents=True, exist_ok=True)
        lock.write_text(today, encoding="utf-8")

    verb = "将删" if dry else "已删"
    for t in removed:
        print(f"{verb}: {t.name}")
    if not removed:
        print(f"（无产物可删，{state_dir} 本已干净）")
    print(f"{'将置' if dry else '已置'}: {lock.name} ← {today}（今日 auto-scan 不再抢占）")
    return 0


def _cmd_install(args) -> int:
    """把 playbook 装到当前 harness 该放的位置（统一安装器）。

    「装到哪一家」同样**跟触发环境走**——在哪个 harness 里跑 install 就装哪家，
    `--source` 可显式覆盖。`--print` 只预演不落盘（用户该有机会先看清要写哪个文件）。
    """
    # 惰性 import：installers 会去查各家 config 布局，装载失败不该拖垮别的子命令
    from .installers import (ADAPTERS, InstallError, detect_entry, do_install,
                             invocation_hint, plan_install, with_entry)
    from .playbook import PlaybookNotFound, load_playbook

    src = _resolve_source(args)
    adapter = ADAPTERS.get(src.name)
    if adapter is None:
        raise ConfigError(f"来源 {src.name} 还没有安装适配器（可选：{', '.join(ADAPTERS)}）")
    # 命令前缀跟**本次 install 自己是怎么装的**走：从 git / 仓库目录装出来的 playbook
    # 若还写着 `uvx ai-coding-insights`，会去 PyPI 找一个不存在的包。`--entry` 可覆盖。
    adapter = with_entry(adapter, getattr(args, "entry", None) or detect_entry())
    try:
        text = load_playbook(getattr(args, "playbook", None))
    except PlaybookNotFound as exc:
        raise ConfigError(str(exc)) from exc

    plan = plan_install(text, adapter)
    if args.print:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    try:
        written = do_install(text, adapter, force=args.force)
    except InstallError as exc:
        # 目标已存在等冲突：报错中止而不是覆盖。用户可能改过自己的 playbook。
        print(f"安装失败：{exc}", file=sys.stderr)
        return 2
    print(f"已安装 {adapter.label} playbook：{written}")
    if plan.get("degraded"):
        print("注意：该 harness 无子代理能力，已装的是降级编排版"
              "（单轮顺序执行，分析深度低于并行版；报告会标注）")
    print(f"触发方式：在 {adapter.label} 里调用 {invocation_hint(adapter, written)}")
    return 0


def _cmd_calibrate(args) -> int:
    """阈值校准：读本机历史快照，给出各指标分布与当前阈值的分位定位。

    手动调试命令，不进 SKILL.md 编排、不产 HTML。只读 snapshots/ 下已脱敏的标量
    快照（复用 snapshot.load_all 的读取规则），不碰会话原文/batch/obs/git。
    样本不足时逐层挂 caveat 明确出声，不静默给出看起来很确定的数字。
    """
    if getattr(args, "replay", False):
        snapshots, replay_meta = _replay_snapshots(args)
    else:
        snapshots, replay_meta = load_all(Path(args.snapshot_dir).expanduser()), None
    result = calibrate(snapshots)
    if replay_meta:
        result["replay"] = replay_meta
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_calibration(result), end="")
    return 0


def _replay_snapshots(args) -> tuple:
    """把历史会话按等长窗口切片重放成伪快照，绕开「攒够快照」的等待。

    动因：窗口闸门是「距上次检查不足 30 天即 too_soon」，快照最快 30 天落一个，
    攒到 20 个要 1.6 年——靠等快照校准阈值这条路走不通。而档位闸门用的全是**规则层
    硬指标**（不需要 LLM），完全可以拿本机既有会话按同口径窗口重算出来。

    只解析一次全部会话再在纯函数层归片（`window_indices`），不是每片重扫一遍：
    jsonl 解析是这条路径的大头，重扫 N 遍会把几秒变成几分钟。
    git 与姿态指标不测（见 `REPLAY_UNMEASURED`），如实报无样本而不是填 0。
    """
    cfg = load_config(resolve_config_path(args.config, args.plugin_root))
    src = _resolve_source(args)
    projects_dir = _resolve_projects_dir(args, src)
    now = datetime.now(timezone.utc)
    win_days = max(1, int(args.replay_window or REPLAY_WINDOW_DAYS))
    step = args.replay_step if args.replay_step else win_days
    # 回看范围要足够覆盖本机全部 transcript：CC 默认只留 30 天，但用户可能调大过
    # cleanupPeriodDays，故按实际最早数据点定，取不到就退回一个宽口径上限。
    data_start = detect_data_start(projects_dir, source=src)
    first_dt = parse_timestamp(data_start) if data_start else None
    lookback = max(win_days, (now - first_dt).days + 1 if first_dt else 365)
    sessions = discover_sessions(projects_dir, cfg.discovery_rules,
                                 lookback, now, source=src)
    last_days = [d.date() if (d := parse_timestamp(s.last_ts)) else None
                 for s in sessions]
    known = [d for d in last_days if d is not None]
    if not known:
        return [], {"windows": 0, "window_days": win_days, "step_days": step,
                    "overlapping": step < win_days, "reason": "本机没有可解析时间戳的会话"}
    windows = replay_windows(min(known), max(known), win_days, step)
    stats = [compute_stats(s, cfg.short_turn_max_chars) for s in sessions]
    outcomes = [compute_outcome(s) for s in sessions]
    snaps = []
    for (since, until), idx in zip(windows, window_indices(last_days, windows)):
        if not idx:
            continue    # 空窗口不产样本：0 会话的「量级 0」是空窗不是低用量，混进分布即掺假
        # repo_outcomes / custom_skill_count / claude_md_sessions 这里**故意不给**：
        # 前者要跑 git log，后两者一个是文件系统当下状态、一个不进快照白名单。
        # 它们在这条路径上是「未测量」，靠 REPLAY_UNMEASURED 在伪快照里整键剔除，
        # 而不是让 aggregate 的默认 0 冒充观测（见 calibrate.REPLAY_UNMEASURED 注释）。
        m = aggregate_metrics([sessions[i] for i in idx], [stats[i] for i in idx],
                              [outcomes[i] for i in idx], repo_outcomes={}, source=src)
        snaps.append(replay_snapshot(until, _metrics_dict(m)))
    return snaps, {"windows": len(snaps), "window_days": win_days, "step_days": step,
                   "overlapping": step < win_days,
                   "span": {"first": min(known).isoformat(), "last": max(known).isoformat()},
                   "empty_windows": len(windows) - len(snaps)}


def build_parser():
    """构建顶层 parser，返回 (parser, 子命令 action)。

    单独抽出来是为了让契约测试能内省「实际注册了哪些子命令与参数」，
    与 SKILL.md / CLAUDE.md 里写的调用行做双向差集（见 tests/test_skill_contract.py）。
    只建 parser、不解析、不做任何 IO。
    """
    ap = argparse.ArgumentParser(prog="ai_coding_insights")
    sub = ap.add_subparsers(dest="cmd")

    def add_source_args(p, with_dir=True):
        """来源两参数（多 harness 承重）。

        `--source` 缺省 None 而非 "claude-code"：None 才走 `detect_source()`
        「跟触发环境走」；写死默认值等于把 CC 强加给所有人。
        `--projects-dir` 缺省同理为 None，运行时按来源解析——argparse 里写死
        `~/.claude/projects` 会让 Codex/opencode 扫出 0 个会话还不报错。
        """
        p.add_argument("--source", default=None, choices=SOURCE_NAMES)
        if with_dir:
            p.add_argument("--projects-dir", default=None)

    sc = sub.add_parser("scan")
    add_source_args(sc)
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
    add_source_args(it)
    it.add_argument("--out", default=None)
    rp = sub.add_parser("render-profile")
    rp.add_argument("--profile", required=True)
    # 缺省时落当前目录 aci-report-<日期>.html——日期由规则层算，不让 LLM 填
    rp.add_argument("--out", default=None)
    # render-profile 不注册 --source：本次取数的来源以 --metrics 里的 `source` 字段为准
    # （那是取数时写下的事实），再从环境重判一次只会引入两处真相源。
    rp.add_argument("--projects-dir", default=None)
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
    add_source_args(au)
    au.add_argument("--days", type=int, default=None)
    au.add_argument("--snapshot-dir", default=str(DEFAULT_SNAPSHOT_DIR))
    au.add_argument("--state-dir", default=None)   # lock + 滚动日志目录；缺省 ~/.ai-coding-insights
    ca = sub.add_parser("calibrate")   # 手动调试命令：阈值分位定位，不进编排流程
    ca.add_argument("--snapshot-dir", default=str(DEFAULT_SNAPSHOT_DIR))
    ca.add_argument("--json", action="store_true")
    # 回放模式：不等快照累积，直接把历史会话按等长窗口切片当样本（见 _cmd_calibrate）
    ca.add_argument("--replay", action="store_true")
    ca.add_argument("--replay-window", type=int, default=REPLAY_WINDOW_DAYS)
    ca.add_argument("--replay-step", type=int, default=None)
    add_source_args(ca)
    ca.add_argument("--config", default=None)
    ca.add_argument("--plugin-root", default=None)
    rs = sub.add_parser("reset")
    rs.add_argument("--state-dir", default=str(Path.home() / ".ai-coding-insights"))
    rs.add_argument("--dry-run", action="store_true")
    ins = sub.add_parser("install")   # 统一安装器：把 playbook 装到当前 harness 的位置
    add_source_args(ins, with_dir=False)
    ins.add_argument("--print", action="store_true")   # 只预演落点，不写盘
    ins.add_argument("--force", action="store_true")   # 覆盖已存在的 playbook
    ins.add_argument("--playbook", default=None)       # 调试用：指定 playbook 正文文件
    ins.add_argument("--entry", default=None)          # 覆盖命令前缀（默认按安装来源推断）
    return ap, sub


def main(argv=None) -> int:
    ap, sub = build_parser()
    sc = sub.choices["scan"]
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
        if args.cmd == "reset":
            return _cmd_reset(args)
        if args.cmd == "calibrate":
            return _cmd_calibrate(args)
        if args.cmd == "install":
            return _cmd_install(args)
        # 默认 / "scan"：向后兼容（无子命令时按 scan 解析）
        if args.cmd is None:
            args = sc.parse_args(raw)
        return _cmd_scan(args)
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2
