"""提取健康度金丝雀（无 IO 纯函数）。

CC transcript 是非稳定内部格式、跨版本会漂移。此模块从已解析的 ParsedSession
列表派生「提取健康度」：CC 版本跨度、parser 不认识的新记录类型、各信号存在率，
以及按版本段的「掉零」断崖检测——把「静默漏数」转为「可见可诊断」。

全部信号存在性从 ParsedSession 既有字段派生，不重扫文件。产出无业务语义。
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
                         absent_thresh: float = 0.02) -> dict:
    """从 ParsedSession 列表派生提取健康度 dict。空输入返回各项空骨架。"""
    n = len(sessions)
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

    # -- 信号存在率（全窗）--
    presence = {sig: (sum(1 for s in sessions if pred(s)) / n if n else 0.0)
                for sig, pred in _SIGNAL_PREDS.items()}

    # -- 断崖检测：按「版本边界」切分老/新两段，只报「掉」--
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
            for sig, pred in _SIGNAL_PREDS.items():
                old_rate = sum(1 for s in older if pred(s)) / len(older)
                new_rate = sum(1 for s in newer if pred(s)) / len(newer)
                if old_rate >= present_thresh and new_rate <= absent_thresh:
                    drift_flags.append({
                        "signal": sig,
                        "older_rate": round(old_rate, 3),
                        "newer_rate": round(new_rate, 3),
                    })

    return {
        "cc_version_span": span,
        "unknown_record_types": unknown,
        "signal_presence": {k: round(v, 3) for k, v in presence.items()},
        "drift_flags": drift_flags,
    }
