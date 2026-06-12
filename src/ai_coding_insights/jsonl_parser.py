import json
from pathlib import Path
from .models import ParsedSession, UserTurn, CommitRef


def extract_text(content) -> str | None:
    # §5 真人/工具回执判别的承重点。判别逻辑：
    # 含任意 tool_result block → None（工具回包，非真人轮次）；
    # 否则收集所有 text block 文字——保留 text+image 的多模态人类轮次（贴截图 + 打字指令），
    #   至少一个 text → 返回拼接，无 text（如纯 image）→ None。
    # 安全性依据：text 与 tool_result 在 corpus 从不共现，故放宽 text 收集不会重引入 tool_result 误判。
    if isinstance(content, str):
        return content
    if isinstance(content, list) and content:
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        parts = [b.get("text", "") for b in content
                 if isinstance(b, dict) and b.get("type") == "text"]
        if parts:
            return "".join(parts)
    return None


def _msg_dict(line: dict) -> dict:
    # message 在旧版/异常记录里可能是字符串等非 dict；统一降级为空 dict，单条脏记录不炸整个会话
    msg = line.get("message")
    return msg if isinstance(msg, dict) else {}


def is_human_turn(line: dict) -> bool:
    # isMeta 是 hook/命令注入的伪 turn，须先排除；再交给 extract_text 做内容判别
    if line.get("type") != "user" or line.get("isMeta"):
        return False
    return extract_text(_msg_dict(line).get("content")) is not None


def session_cwd(path) -> str:
    """只取会话的 cwd：读到首个带 cwd 的 user/assistant 记录即停。

    与 parse_session().cwd 同语义（首个非空 cwd），但不做全文件解析——
    供只需 cwd 的调用方（init 向导扫全量历史会话）用，文件可能极大。"""
    with Path(path).open(encoding="utf-8") as f:
        for raw in f:
            if not raw.strip():
                continue
            try:
                line = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if (isinstance(line, dict) and line.get("type") in ("user", "assistant")
                    and line.get("cwd")):
                return line["cwd"]
    return ""


def parse_session(path) -> ParsedSession:
    path = Path(path)
    user_turns, tools, models = [], [], []
    commits, edit_count = [], 0
    token_usage: dict = {}
    ask_ids: set = set()            # 待配对的 AskUserQuestion tool_use id
    option_pick_count = 0
    seen_sha = set()
    session_id = cwd = git_branch = first_ts = last_ts = None
    # 必须逐行迭代文件句柄而非 splitlines()：jsonl 仅以 \n 分隔，但 splitlines()
    # 还会在粘贴内容里的 Unicode 行分隔符（U+2028/U+2029/U+0085）处断行，
    # 打碎有效记录 → JSONDecodeError 被静默吞掉丢记录。text-mode 行迭代只认 \n/\r。
    with path.open(encoding="utf-8") as f:
        for raw in f:
            if not raw.strip():
                continue
            try:
                line = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if line.get("type") in ("user", "assistant"):
                session_id = session_id or line.get("sessionId")
                cwd = cwd or line.get("cwd")
                git_branch = git_branch or line.get("gitBranch")
                ts = line.get("timestamp")
                if isinstance(ts, str) and ts:  # 非字符串 timestamp 视为缺失，避免 min/max 比较类型炸
                    first_ts = ts if first_ts is None else min(first_ts, ts)
                    last_ts = ts if last_ts is None else max(last_ts, ts)
            if is_human_turn(line):
                msg = _msg_dict(line)
                user_turns.append(UserTurn(
                    uuid=line.get("uuid", ""),
                    text=extract_text(msg.get("content")) or "",
                    timestamp=line.get("timestamp", "")))
            if line.get("type") == "assistant":
                msg = _msg_dict(line)
                model = msg.get("model")
                if model and model != "<synthetic>":
                    models.append(model)
                usage = msg.get("usage")
                if model and model != "<synthetic>" and isinstance(usage, dict):
                    bucket = token_usage.setdefault(model, {
                        "input": 0, "output": 0, "cache_read": 0, "cache_creation": 0})
                    bucket["input"] += usage.get("input_tokens") or 0
                    bucket["output"] += usage.get("output_tokens") or 0
                    bucket["cache_read"] += usage.get("cache_read_input_tokens") or 0
                    bucket["cache_creation"] += usage.get("cache_creation_input_tokens") or 0
                for b in msg.get("content") or []:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        tools.append(b.get("name", ""))
                        if b.get("name") == "AskUserQuestion" and b.get("id"):
                            ask_ids.add(b["id"])
            # AskUserQuestion 选项回答：tool_result 按 id 配对 + 顶层 toolUseResult
            # 含 answers dict（拒绝时是字符串回执，天然排除）。每答一题计 1 个决策点。
            if line.get("type") == "user" and ask_ids:
                content = _msg_dict(line).get("content")
                if isinstance(content, list):
                    paired = [b.get("tool_use_id") for b in content
                              if isinstance(b, dict) and b.get("type") == "tool_result"
                              and b.get("tool_use_id") in ask_ids]
                    if paired:
                        ask_ids.difference_update(paired)   # 同 id 只配对一次
                        tur_top = line.get("toolUseResult")
                        answers = (tur_top.get("answers")
                                   if isinstance(tur_top, dict) else None)
                        if isinstance(answers, dict):
                            option_pick_count += len(answers)
            tur = line.get("toolUseResult")
            if isinstance(tur, dict):
                go = tur.get("gitOperation")
                if isinstance(go, dict):
                    c = go.get("commit")
                    if isinstance(c, dict) and c.get("sha") and c["sha"] not in seen_sha:
                        seen_sha.add(c["sha"])
                        commits.append(CommitRef(sha=c["sha"], kind=c.get("kind", "")))
                sp = tur.get("structuredPatch")
                if isinstance(sp, list) and sp:
                    edit_count += 1
    return ParsedSession(
        file_path=str(path), session_id=session_id or "", cwd=cwd or "",
        git_branch=git_branch, user_turns=user_turns,
        tools_used=sorted(set(tools)), models_used=sorted(set(models)),
        first_ts=first_ts, last_ts=last_ts,
        commits=commits, edit_count=edit_count, token_usage=token_usage,
        option_pick_count=option_pick_count)
