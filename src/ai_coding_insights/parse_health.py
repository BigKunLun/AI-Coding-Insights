"""提取健康度金丝雀（无 IO 纯函数）。

CC transcript 是非稳定内部格式、跨版本会漂移。此模块从已解析的 ParsedSession
列表派生「提取健康度」：CC 版本跨度、parser 不认识的新记录类型，
以及按版本段的漂移检测——把「静默漏数 / 静默虚高」转为「可见可诊断」。

漂移检测三类（drift_flags 每条带 kind 字段）：
- drop  ：存在率断崖下跌（以前有、现在掉零）——提取规则疑似失效，漏数。
- surge ：存在率断崖上涨（以前绝迹、现在普遍）——与 drop 对称。新版把一条记录
          拆成两条、或字段改名后被别的分支重复命中，都会让信号虚高。
- shift ：数值量级偏移（每会话中位数老/新段比值越界）——存在率没变，但量级变了。
          edit_count / commits 这类与奖惩挂钩的硬指标最怕这种无感知污染。

全部信号从 ParsedSession 既有字段派生，不重扫文件。产出无业务语义。
"""

# parser 已编目（处理或明知跳过）的记录类型；不在此集 = 新类型，需关注
KNOWN_RECORD_TYPES = {
    "user", "assistant", "summary", "system", "attachment", "queue-operation",
    "last-prompt", "mode", "permission-mode", "file-history-snapshot",
    "ai-title", "worktree-state", "agent-name",
}

# 信号存在性谓词：从 ParsedSession 字段派生。
# 雷达必须覆盖**最新、最依赖内部嵌套形态**的提取（option_pick 的 answers dict、
# Skill.input.skill、mcp__server__tool 命名、run_in_background、并行 Agent）——它们正是
# CC 版本一变最易静默失效的；只盯老的稳定信号 = 在最该报警的维度上失明。
_SIGNAL_PREDS = {
    "humanturn": lambda s: bool(s.user_turns),
    "model": lambda s: bool(s.models_used),
    "tooluse": lambda s: bool(s.tools_used),
    "thinking": lambda s: s.thinking_block_count > 0,
    "token": lambda s: bool(s.token_usage),
    "gitop": lambda s: bool(s.commits),
    "edit": lambda s: s.edit_count > 0,
    "plan": lambda s: s.plan_mode_count > 0,
    "optionpick": lambda s: s.option_pick_count > 0,
    "skill": lambda s: bool(s.skill_names),
    "mcp": lambda s: bool(s.mcp_servers),
    "background": lambda s: s.background_task_count > 0,
    "parallel": lambda s: s.parallel_agent_turns > 0,
}

# 数值提取器：每会话的「量级」（非 bool）。存在性谓词只能发现信号有无，发现不了
# 「还在但数字变了」——而与奖惩挂钩的硬指标（edit / gitop）恰恰是被量级污染才致命。
# 只收整数计数型字段：字典/浮点类（token_usage）随用量天然大幅波动，做金丝雀会天天叫。
_SIGNAL_NUMS = {
    "turn": lambda s: len(s.user_turns),
    "edit": lambda s: s.edit_count,
    "gitop": lambda s: len(s.commits),
    "tool": lambda s: len(s.tools_used),
    "thinking": lambda s: s.thinking_block_count,
    "optionpick": lambda s: s.option_pick_count,
    "plan": lambda s: s.plan_mode_count,
}


def _median(values: list):
    """中位数。用中位数不用均值：单个超长会话能把均值拉飞，中位数不动。空列表返回 None。"""
    xs = sorted(values)
    n = len(xs)
    if n == 0:
        return None
    mid = n // 2
    return xs[mid] if n % 2 else (xs[mid - 1] + xs[mid]) / 2


def _version_key(v: str) -> tuple:
    """'2.1.158' -> (2,1,158)，数值序（非字典序，避免 99 排在 100 后）。坏段记 0。"""
    parts = []
    for p in (v or "").split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def _session_version(s) -> str | None:
    """会话归属版本 = 其记录里最大的 version（它实际运行所在版本）。"""
    return max(s.cc_versions, key=_version_key) if s.cc_versions else None


def compute_parse_health(sessions, min_bucket: int = 10,
                         present_thresh: float = 0.30,
                         absent_thresh: float = 0.02,
                         surge_absent_thresh: float = 0.01,
                         surge_present_thresh: float = 0.50,
                         shift_ratio_thresh: float = 2.5) -> dict:
    """从 ParsedSession 列表派生提取健康度 dict。空输入返回各项空骨架。

    阈值默认值一律取保守值——金丝雀天天叫等于没有金丝雀，宁可漏报不可误报：

    - surge_absent_thresh=0.01 / surge_present_thresh=0.50：比 drop 方向更严
      （drop 是 0.30 → 0.02）。因为「涨」有一个 drop 没有的良性解释：用户真的开始用
      某个新特性了。所以要求老段近乎绝迹（≤1%）且新段已成多数（≥50%）才报。
    - shift_ratio_thresh=2.5：典型的提取事故是「一条拆两条」= 2 倍，得报；而跨版本
      采样波动把中位数拉到 1.5~2 倍是常事，不能报。2.5 卡在两者之间偏保守一侧。
    """
    # -- 版本跨度 --
    all_versions = sorted({v for s in sessions for v in s.cc_versions},
                          key=_version_key)
    span = ({"min": all_versions[0], "max": all_versions[-1],
             "distinct": len(all_versions)} if all_versions else
            {"min": None, "max": None, "distinct": 0})

    # -- 未知记录类型 --
    seen_types: set = set()
    for s in sessions:
        seen_types.update(s.record_type_counts or {})
    unknown = sorted(seen_types - KNOWN_RECORD_TYPES)

    # -- 漂移检测：按「版本边界」切分老/新两段，报 drop / surge / shift --
    drift_flags: list = []
    stamped = [(v, s) for s in sessions if (v := _session_version(s))]
    distinct = sorted({v for v, _ in stamped}, key=_version_key)
    if len(stamped) >= 2 * min_bucket and len(distinct) >= 2:
        # 切点必须落在版本边界、而非会话序中点：否则当某主版本会话数过半时，它会同时
        # 横跨前后两段，把同一版本内的采样波动误报成「版本漂移」。按 distinct 版本列表
        # 中点划分，保证同一版本只属于一段。
        vmid = len(distinct) // 2
        older_vers = set(distinct[:vmid])
        older = [s for v, s in stamped if v in older_vers]
        newer = [s for v, s in stamped if v not in older_vers]
        if len(older) >= min_bucket and len(newer) >= min_bucket:
            # 存在性两个方向：掉零(drop) 与 突增(surge)
            for sig, pred in _SIGNAL_PREDS.items():
                old_rate = sum(1 for s in older if pred(s)) / len(older)
                new_rate = sum(1 for s in newer if pred(s)) / len(newer)
                kind = None
                if old_rate >= present_thresh and new_rate <= absent_thresh:
                    kind = "drop"
                elif (old_rate <= surge_absent_thresh
                      and new_rate >= surge_present_thresh):
                    kind = "surge"
                if kind:
                    drift_flags.append({
                        "signal": sig,
                        "kind": kind,
                        "older_rate": round(old_rate, 3),
                        "newer_rate": round(new_rate, 3),
                    })

            # 数值量级偏移(shift)：存在率没变但每会话数字变了
            flagged = {f["signal"] for f in drift_flags}
            for sig, num in _SIGNAL_NUMS.items():
                if sig in flagged:
                    continue   # 同一信号已按存在性报过，不重复计数（下游按条数说“N 个信号”）
                # 只在「信号确实出现」的会话上取中位数：把 0 算进去时，只要信号存在率
                # 低于半数中位数恒为 0，比值无从谈起，整个维度等于失明。
                old_vals = [v for s in older if (v := num(s)) > 0]
                new_vals = [v for s in newer if (v := num(s)) > 0]
                if len(old_vals) < min_bucket or len(new_vals) < min_bucket:
                    continue   # 非零样本太薄，中位数不可信
                om, nm = _median(old_vals), _median(new_vals)
                if not om or not nm:
                    continue
                ratio = nm / om
                if ratio >= shift_ratio_thresh or ratio <= 1 / shift_ratio_thresh:
                    drift_flags.append({
                        "signal": sig,
                        "kind": "shift",
                        # 存在率与 drop/surge 同名同义，但 shift 的两端几乎相等、印出来
                        # 毫无信息量：结论文案必须按 kind 分支、改用下面的中位数出数
                        # （渲染层见 report._drift_flag_text，SKILL.md 第 5 步同口径）
                        "older_rate": round(len(old_vals) / len(older), 3),
                        "newer_rate": round(len(new_vals) / len(newer), 3),
                        "older_median": om,
                        "newer_median": nm,
                        "median_ratio": round(ratio, 3),
                    })

    return {
        "cc_version_span": span,
        "unknown_record_types": unknown,
        "drift_flags": drift_flags,
    }
