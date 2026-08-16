"""证据指针真伪核验（决策为纯函数，文件 IO 由调用方注入）。

pointer 形如「/abs/path.jsonl#<turn-uuid>」；会话级观察允许只有路径、不带 #。
LLM 偶发编造路径或拿会话 id 冒充 turn uuid——指针回看是证据链的可信度承重点，
渲染前必须确定性核验。未命中的条目不剔除（行为描述本身仍可能成立），
打上 pointer_missing 标记由渲染层明示，并由调用方在 stderr 出声。
"""
import re

# 匹配 "uuid" 字段而非裸子串——会话 id 作为 sessionId 出现在每一行，
# 裸子串会把「拿会话 id 冒充 turn uuid」的伪指针误判为命中。
_UUID_FIELD = re.compile(r'"uuid"\s*:\s*"([^"]+)"')


def extract_turn_uuids(lines) -> set[str]:
    """从 jsonl 行迭代器提取全部 uuid 字段值（一遍扫完，供同文件多条指针复用）。

    这是 **Claude Code 专用**的快路：CC transcript 每条记录自带 `"uuid"` 字段。
    别的 harness 没有这个字段（Codex 的 turn uuid 由 parser 按行号合成、opencode 用
    message id），跨来源请走 `turn_uuids_for`。
    """
    out: set[str] = set()
    for line in lines:
        out.update(_UUID_FIELD.findall(line))
    return out


# 支持 uuid 级指针回看的来源。不在其中的来源只核文件存在性，并在报告里如实说明
# 少了这道核验（见 view_model 的 source_notes）。加一家来源就要想清楚它属哪一边。
POINTER_UUID_SOURCES = frozenset({"claude-code", "codex"})


def turn_uuids_for(path, source_name: str) -> set[str] | None:
    """按来源取该会话文件里全部**真人轮次**的 uuid 集合。

    返回 None 表示「本来源不支持指针 uuid 回看」——调用方此时**只核文件存在性**，
    不要把指针一律判成未命中。这条区分是承重的：Codex/opencode 的 uuid 形状与 CC
    完全不同，若沿用 CC 的正则，每一条证据指针都会被打上 ⚠「未命中」——用户看到的
    是一份「证据全都对不上」的报告，而真相只是核验方式没跟上来源。宁可少一道核验、
    并如实说明少了这道核验，也不要制造整片假警报。
    """
    from pathlib import Path as _Path

    p = _Path(path)
    if source_name == "claude-code":
        try:
            with p.open(encoding="utf-8") as f:
                return extract_turn_uuids(f)
        except OSError:
            return set()   # 文件读不了 = 一个 uuid 都命中不了（与「不支持核验」不同）
    if source_name == "codex":
        # Codex rollout 记录没有 uuid 字段，turn uuid 由 parser 合成（形如 turn-<行号>）。
        # 直接问 parser 要，避免在这里复刻一份「哪条算真人轮次」的判别逻辑——
        # 复刻出来的第二份判别迟早与 parser 漂移，而漂移的表现又是安静地误判指针。
        try:
            from .codex_source import parse as _parse
            return {t.uuid for t in _parse(p).user_turns if t.uuid}
        except (OSError, ImportError, ValueError):
            return set()
    # opencode 会话存在 SQLite 库里、不是「一个文件一场会话」，指针回看要按 message id
    # 查库，v1 暂不支持：如实返回 None（不支持核验），而不是假装核验过。
    return None


def split_pointer(pointer) -> tuple[str, str | None]:
    """拆「path#uuid」：无 # 或 # 后为空 → uuid 为 None（会话级指针）。

    按最后一个 # 切（路径自身可能含 #），且 uuid 段含 / 视为路径的一部分
    （turn uuid 不可能含 /）——避免把含 # 目录下的合法指针误判未命中。
    """
    s = str(pointer or "").strip()
    path, sep, uuid = s.rpartition("#")
    if not sep or "/" in uuid:
        return s, None
    return path.strip(), (uuid.strip() or None)


def flag_missing_pointers(profile: dict, pointer_ok) -> tuple[dict, list[str]]:
    """核验 evidence/highlights 全部指针，未命中的条目加 pointer_missing=True。

    pointer_ok(path, uuid_or_None) -> bool 由调用方注入（IO 在外，便于直接测试）。
    返回 (新 profile, 未命中指针原文列表)；不修改入参。
    frictions[].pointers（字符串列表）同样核验，并归一为
    {"pointer", pointer_missing?} dict 供渲染直接消费。
    """
    out = dict(profile or {})
    misses = []
    for key in ("evidence", "highlights"):
        items = out.get(key)
        if not isinstance(items, list):
            continue
        new_items = []
        for e in items:
            if isinstance(e, dict):
                path, uuid = split_pointer(e.get("pointer"))
                if not (path and pointer_ok(path, uuid)):
                    e = {**e, "pointer_missing": True}
                    misses.append(str(e.get("pointer", "")))
            new_items.append(e)
        out[key] = new_items

    # 摩擦指针：frictions[].pointers 是字符串列表（LLM 契约），此处归一成
    # {"pointer", pointer_missing?} dict——渲染端与证据共用 _ptr_chip 消费。
    frs = out.get("frictions")
    if isinstance(frs, list):
        new_frs = []
        for f in frs:
            if isinstance(f, dict) and isinstance(f.get("pointers"), list):
                entries = []
                for p in f["pointers"]:
                    entry = {"pointer": str(p)}
                    path, uuid = split_pointer(p)
                    if not (path and pointer_ok(path, uuid)):
                        entry["pointer_missing"] = True
                        misses.append(str(p))
                    entries.append(entry)
                f = {**f, "pointers": entries}
            new_frs.append(f)
        out["frictions"] = new_frs
    return out, misses
