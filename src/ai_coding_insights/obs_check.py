"""阶段一产物覆盖校验（编排闸二的决策逻辑，无 IO 纯函数）。

extractor 契约要求每个 batch 会话都必须出现在 obs 的 sessions 里
（无可记录则 notable_turns 为空占位）。本函数比对两侧会话集合：
- missing：batch 里有、obs 里没有 → extractor 漏抽，按 batch 文件归类便于补派；
- orphans：obs 里有、不属于任何 batch → 残留上一轮旧 obs，专家读到即张冠李戴。
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
