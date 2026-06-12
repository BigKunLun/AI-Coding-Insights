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
    """从 jsonl 行迭代器提取全部 uuid 字段值（一遍扫完，供同文件多条指针复用）。"""
    out: set[str] = set()
    for line in lines:
        out.update(_UUID_FIELD.findall(line))
    return out


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
    return out, misses
