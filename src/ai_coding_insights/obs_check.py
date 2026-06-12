"""阶段一产物覆盖校验（编排闸二的决策逻辑，无 IO 纯函数）。

extractor 契约要求每个 batch 会话都必须出现在 obs 的 sessions 里
（无可记录则 notable_turns 为空占位）。本函数比对两侧会话集合：
- missing：batch 里有、obs 里没有 → extractor 漏抽，按 batch 文件归类便于补派；
- orphans：obs 里有、不属于任何 batch → 残留上一轮旧 obs，专家读到即张冠李戴。

v2 口径起，本模块还负责 posture_counts 的完整性校验与聚合（均为无 IO 纯函数）。
"""


def check_obs_coverage(batch_sessions: dict[str, list[str]], obs_session_ids: set[str]) -> dict:
    """比对 batch 会话与 obs 覆盖会话。

    batch_sessions: {batch 文件路径: [session_id, ...]}
    obs_session_ids: 全部 obs 文件里出现的 session_id 集合
    返回 {"status", "missing", "orphans", "batch_session_count", "obs_session_count"}，
    输出全部排序，保证确定性。
    """
    all_batch_ids: set[str] = set()
    missing = []
    for file in sorted(batch_sessions):
        ids = batch_sessions[file]
        all_batch_ids.update(ids)
        lost = sorted(set(ids) - obs_session_ids)
        if lost:
            missing.append({"file": file, "session_ids": lost})

    orphans = sorted(obs_session_ids - all_batch_ids)
    return {
        "status": "ok" if not missing and not orphans else "mismatch",
        "missing": missing,
        "orphans": orphans,
        "batch_session_count": len(all_batch_ids),
        "obs_session_count": len(obs_session_ids),
    }


_POSTURE_KEYS = ("L1", "L2", "L3", "L4")


def check_posture_counts(batch_turn_counts: dict, obs_sessions: list) -> list:
    """校验各会话 posture_counts：键全、非负整数、总和 == 该会话 batch 输入条数。

    batch_turn_counts: {session_id: batch 里该会话 turns 条数}
    obs_sessions: 全部 obs 文件解析出的会话 dict 列表
    返回问题列表 [{"session_id", "reason"}]（按 session_id 排序），空 = 通过。
    不在 batch 里的 obs 会话不报（orphan 由覆盖校验负责，避免双报）。
    """
    problems = []
    for s in obs_sessions or []:
        sid = s.get("session_id")
        if sid not in batch_turn_counts:
            continue
        pc = s.get("posture_counts")
        if not isinstance(pc, dict):
            problems.append({"session_id": sid, "reason": "缺 posture_counts"})
            continue
        bad = [k for k in _POSTURE_KEYS
               if not isinstance(pc.get(k), int) or isinstance(pc.get(k), bool)
               or pc.get(k) < 0]
        if bad:
            problems.append({"session_id": sid,
                             "reason": f"posture_counts 的 {','.join(bad)} 须为非负整数"})
            continue
        total = sum(pc[k] for k in _POSTURE_KEYS)
        expect = batch_turn_counts[sid]
        if total != expect:
            problems.append({"session_id": sid,
                             "reason": f"posture_counts 总和 {total} ≠ 该会话输入数 {expect}"})
    return sorted(problems, key=lambda p: p["session_id"])


def sum_posture_counts(obs_sessions) -> dict:
    """聚合全部会话的 posture_counts → 窗口级四档总计数。

    缺键/非法值按 0 计（verify-obs 已在阶段一闸住，这里只防御不报错）。
    """
    total = {k: 0 for k in _POSTURE_KEYS}
    for s in obs_sessions or []:
        pc = s.get("posture_counts") or {}
        for k in _POSTURE_KEYS:
            v = pc.get(k)
            if isinstance(v, int) and not isinstance(v, bool) and v > 0:
                total[k] += v
    return total
