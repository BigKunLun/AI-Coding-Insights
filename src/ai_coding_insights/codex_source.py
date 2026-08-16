"""Codex CLI 来源适配（`~/.codex/sessions/YYYY/MM/DD/rollout-<本地时间>-<sessionId>.jsonl`）。

Codex 的 rollout 记录形状与 CC transcript 完全不同，**不能套 CC 的 parser**：
顶层固定三键 `{"timestamp", "type", "payload"}`，语义全在 `payload` 里，且
`session_meta` / `turn_context` 的 payload **没有 `type` 字段** —— 故判别键是
`(record.type, payload.type, payload.role)` 三元组，不是单一 type。

## 字段映射表（证据来源：本机实测 = 2026-06-03 的单个 rollout，203 行 / 3 turn，
   Codex Desktop 发起、cli_version 0.136.0-alpha.2；标「推测」的是无样本、按
   格式推断写的宽松兼容分支）

| ParsedSession 字段    | 来源                                                       | 证据 |
|-----------------------|------------------------------------------------------------|------|
| session_id            | `session_meta.payload.id`（UUIDv7）                         | 实测；缺 meta 时回退文件名末段 UUID（推测，供片段文件兜底） |
| cwd                   | `session_meta.payload.cwd`，退化到 `turn_context.payload.cwd`| 实测 |
| git_branch            | `session_meta.payload.git.branch`                           | 实测 |
| first_ts / last_ts    | 顶层 `.timestamp`（ISO8601 UTC 带 Z），203/203 行都有        | 实测 |
| user_turns            | `event_msg.user_message.message` 为主，`response_item.message[role=user]` 兜底，两通道去重 | 主判据实测 3/3；兜底通道未验证（本机所有真人轮次两通道都落，纯 CLI 是否只落其一无样本） |
| models_used           | `turn_context.payload.model`（每 turn 一条，如 "gpt-5.5"）   | 实测；assistant 消息本身不带 model |
| tools_used            | `function_call.name` + `custom_tool_call.name`               | 实测（exec_command ×44 / apply_patch ×2） |
| edit_count            | `event_msg.patch_apply_end` 且 `success is True` 的条数      | 实测 2 条 |
| edited_paths          | 同上记录 `changes` 的 **key**（已是绝对路径）                | 实测 |
| thinking_block_count  | `response_item.payload.type == "reasoning"` 的块数           | 实测 18；summary 恒空数组、content 为加密串，只能数块数 |
| token_usage           | 最后一条 `event_msg.token_count.info.total_token_usage`      | 实测（该字段会话累计单调递增，24 条从 19605 递增到 858556） |
| cc_versions           | `session_meta.payload.cli_version`（每会话 1 次）            | 实测 |
| mcp_servers           | 三种形状宽松匹配，见下                                       | **未验证**：本机零 MCP 调用样本 |
| record_type_counts    | `f"{type}/{payload.type}"`（payload 无 type 时退化为 `type`）| 实测 |
| commits / plan_mode / option_pick / skill_* / background / max_parallel_agents | 恒空 | Codex 无对位概念，见 sources.py 的能力集注释；**空是「未测量」不是「没做」** |

## 三个承重决策

1. **token 桶做了口径换算，不是照抄字段名**。Codex 的 `input_tokens` **包含**
   `cached_input_tokens`（实测：input 19323 / cached 3456 / output 282，而
   `total_tokens` 19605 == input + output），而 CC/Anthropic 口径里 `input_tokens`
   与 `cache_read_input_tokens` 是**互斥**的两桶，`token_total` 直接四桶相加。
   若照抄 `input ← input_tokens`，缓存部分会被算两遍、Codex 的 token_total 系统性
   虚高（本机样本虚高 85%）。故这里 `input ← input_tokens - cached_input_tokens`，
   使四桶之和恰好等于 `total_tokens`，与 CC 同口径可比。

2. **只取 `total_token_usage` 的最后一条，不累加 `last_token_usage`**。前者是会话
   累计（实测单调递增到 858556），后者是本次请求增量；累加 total 会平方级双算。

3. **patch 的 `changes` 只取 key，value 一个字节都不碰**。value 里装着新增文件的
   **全文**（`content`）或 **unified diff**（`unified_diff`）——这是隐私铁律的正面
   撞击点：一旦带进 ParsedSession 任何字段，会随批次流向 LLM 层，业务代码就出本机了。
   路径本身也只在本机内做交集匹配、只出计数（见 git_outcome.py）。

## 性能与防御

- 单条 `session_meta` 带 21K 系统提示（`base_instructions`）+ 10 个工具的完整 JSON
  Schema（`dynamic_tools`），占 357K 样本文件的绝大多数体积。这里**逐行流式**读取，
  且只从 meta 里摘 5 个短标量，那两个巨型字段既不落变量也不进任何产物。
- `session_meta` 一个文件里出现多次（实测 3 次，每 turn 重发）且**首条缺 `memory_mode`**
  → 全程 `.get()`，不假定 key 齐全；标量取首个非空值（与 CC parser 的 `x = x or ...` 同风格）。
- 单条脏记录不炸整场解析：坏 JSON 行 / 裸值行 / payload 非 dict / message 非 dict /
  token 非 int / changes 非 dict，逐处挡掉后 `continue`。
- `function_call.arguments`（JSON 字符串）与 `custom_tool_call.input`（patch DSL 纯文本）
  **刻意不解析**：里面是命令行与补丁正文（业务内容），且当前无任何指标需要它。
"""
import json
import posixpath
import re
from pathlib import Path

from .models import ParsedSession, UserTurn
from .sources import CODEX, file_scanner, head_earliest_ts
from .timeutil import parse_timestamp

# 目录层级是 `YYYY/MM/DD/`，但用递归 glob 而非固定三层：文件名前缀 `rollout-` 已经
# 足够选择性（不会像 CC 那样把无关 jsonl 卷进来），递归还能容忍 root 直接指到某一天。
_PATTERN = "**/rollout-*.jsonl"

# 文件名末段是 sessionId（`rollout-<本地时间>-<uuid>.jsonl`），缺 session_meta 的
# 片段文件靠它兜底。**未验证**：本机没有缺 meta 的样本，按文件名规律推断。
_FILENAME_SID = re.compile(
    r"-([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$")

# 注入到 user 角色里的系统上下文（不是真人打的字）。判据是 content 首块文本的开头标签。
_INJECTED_PREFIXES = ("<environment_context>", "<user_instructions>")

# 真人轮次跨通道去重的记录距离窗口：同一轮的 response_item 与 event_msg 实测紧邻
# （相隔 1 条）。给到 8 条是留余量，又不至于把「隔了几轮之后用户真的又发了一遍同样
# 的话」误并成一次。
_DEDUP_WINDOW = 8


def _as_int(v) -> int:
    # 与 jsonl_parser 同规则：非 int（脏记录里的字符串/None）归零；bool 是 int 子类，
    # 显式排除，避免 True 被当 1 计。
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


def _texts(content) -> str:
    """从 message.content 里拼接文本块。

    Codex 的块类型是 `input_text`（user/developer）与 `output_text`（assistant）；
    也认 `text` 以防版本漂移。非 list / 非 dict 块 / 非字符串 text 一律跳过。
    """
    if not isinstance(content, list):
        return ""
    parts = [t for b in content
             if isinstance(b, dict) and b.get("type") in ("input_text", "output_text", "text")
             and isinstance(t := b.get("text"), str)]
    return "".join(parts)


def _mcp_server(payload: dict, name: str) -> str | None:
    """从工具调用记录里认 MCP server 名（**三种形状都认，收不到就返回 None**）。

    本机零 MCP 调用样本，真实形状未验证；写宽松是因为「认错」的代价（多一个 server 名）
    远小于「认不到」（Codex 用户的 MCP 使用被判成 0）。三种形状：
      ① `mcp__<server>__<tool>`（与 CC 同前缀，工具规格里的 namespace 就长这样）
      ② 裸工具名 + payload 上另有 namespace/server 字段
      ③ 旧版可能的 `payload.type == "mcp_tool_call"`（server 落在独立字段里）
    """
    if name.startswith("mcp__"):
        parts = name.split("__", 2)
        if len(parts) >= 2 and parts[1]:
            return parts[1]
    for key in ("namespace", "server", "mcp_server"):
        v = payload.get(key)
        if isinstance(v, str) and v:
            return v[len("mcp__"):] if v.startswith("mcp__") and len(v) > 5 else v
    return None


def parse(path) -> ParsedSession:
    """解析单个 Codex rollout 文件为 ParsedSession（来源标记 = codex）。"""
    path = Path(path)
    user_turns: list[UserTurn] = []
    tools: list[str] = []
    models: list[str] = []
    mcp_servers: set[str] = set()
    raw_paths: set[str] = set()     # patch 的 changes key 原样收着，收尾统一归一成绝对路径
    versions: set[str] = set()
    record_type_counts: dict = {}
    edit_count = 0
    thinking_block_count = 0
    session_id = cwd = git_branch = first_ts = last_ts = None
    current_model: str | None = None
    # 最后一条 token_count 的累计用量 + 当时在用的 model（token 桶按 model 分组）
    last_usage: dict | None = None
    last_usage_model: str | None = None
    # 跨通道去重台账：[记录序号, 文本, 来自哪个通道, 是否已被配对消费]
    recent_turns: list[list] = []

    def _human_turn(idx: int, channel: str, text: str, ts: str) -> None:
        """登记一条真人轮次，同一轮的两个通道只算一次。

        只跟**另一个通道**的未消费条目配对：同通道内的重复文本是用户真的连发了两遍
        （比如两次「继续」），必须各计一次，不能并。
        """
        for entry in recent_turns:
            if (entry[0] >= idx - _DEDUP_WINDOW and entry[1] == text
                    and entry[2] != channel and not entry[3]):
                entry[3] = True     # 消费掉，防止第三条同文本被一起吞掉
                return
        recent_turns.append([idx, text, channel, False])
        while recent_turns and recent_turns[0][0] < idx - _DEDUP_WINDOW:
            recent_turns.pop(0)
        # uuid 是**合成**的：Codex 记录里没有任何 per-record id（turn_id 只落在
        # task_started/patch 上，user_message 上没有）。用 0 基行号，既稳定可复现、
        # 又不含文件名/业务信息。⚠ 已知副作用：evidence_check 现在靠在 transcript 里
        # 正则找 `"uuid"` 字段来核验指针，Codex rollout 没有这个字段，Codex 的证据
        # 指针会被全量标 pointer_missing——需在 evidence_check/cli 侧按来源分派才算修好。
        user_turns.append(UserTurn(uuid=f"turn-{idx}", text=text, timestamp=ts))

    # 逐行迭代文件句柄而非 splitlines()：jsonl 只以 \n 分隔，splitlines() 会在粘贴
    # 内容里的 U+2028/U+2029/U+0085 处多断行，打碎有效记录（同 jsonl_parser 的教训）。
    with path.open(encoding="utf-8") as f:
        for idx, raw in enumerate(f):
            if not raw.strip():
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):   # 裸值行（数组/字符串/数字）无会话语义
                continue
            rtype = rec.get("type")
            payload = rec.get("payload")
            if not isinstance(payload, dict):
                payload = {}
            ptype = payload.get("type")
            if isinstance(rtype, str) and rtype:
                key = f"{rtype}/{ptype}" if isinstance(ptype, str) and ptype else rtype
                record_type_counts[key] = record_type_counts.get(key, 0) + 1
            ts = rec.get("timestamp")
            if isinstance(ts, str) and ts:   # 非字符串 timestamp 视为缺失，避免比较炸
                first_ts = ts if first_ts is None else min(first_ts, ts)
                last_ts = ts if last_ts is None else max(last_ts, ts)
            else:
                ts = ""

            # --- 会话级标量：session_meta（多次重发，取首个非空）---
            if rtype == "session_meta":
                sid = payload.get("id")
                if isinstance(sid, str) and sid:
                    session_id = session_id or sid
                c = payload.get("cwd")
                if isinstance(c, str) and c:
                    cwd = cwd or c
                git = payload.get("git")
                if isinstance(git, dict):
                    br = git.get("branch")
                    if isinstance(br, str) and br:
                        git_branch = git_branch or br
                ver = payload.get("cli_version")
                if isinstance(ver, str) and ver:
                    versions.add(ver)
                continue    # base_instructions / dynamic_tools 绝不读取

            # --- 每轮上下文：turn_context 带 model（assistant 消息自己不带）---
            if rtype == "turn_context":
                m = payload.get("model")
                if isinstance(m, str) and m:
                    models.append(m)
                    current_model = m
                c = payload.get("cwd")
                if isinstance(c, str) and c:
                    cwd = cwd or c
                continue

            # --- 真人输入：主通道 event_msg.user_message ---
            if ptype == "user_message":
                msg = payload.get("message")
                if isinstance(msg, str) and msg.strip():
                    _human_turn(idx, "event", msg, ts)
                continue

            # --- 真人输入兜底通道：response_item.message[role=user] ---
            if ptype == "message":
                role = payload.get("role")
                if role != "user":
                    continue        # developer 是系统注入，assistant 无需入账
                text = _texts(payload.get("content"))
                if text.strip() and not text.lstrip().startswith(_INJECTED_PREFIXES):
                    _human_turn(idx, "response", text, ts)
                continue

            # --- 推理块：只数块数（summary 恒空、content 加密，拿不到任何语义）---
            if ptype == "reasoning":
                thinking_block_count += 1
                continue

            # --- 工具调用：function_call / custom_tool_call / 旧版 mcp_tool_call ---
            if ptype in ("function_call", "custom_tool_call", "mcp_tool_call"):
                name = payload.get("name")
                if isinstance(name, str) and name:
                    tools.append(name)
                    server = _mcp_server(payload, name)
                    if server:
                        mcp_servers.add(server)
                continue

            # --- token 用量：只留最后一条累计值（见模块 docstring 决策 2）---
            if ptype == "token_count":
                info = payload.get("info")
                if isinstance(info, dict):
                    total = info.get("total_token_usage")
                    if isinstance(total, dict):
                        last_usage = total
                        last_usage_model = current_model
                continue

            # --- 文件编辑：patch_apply_end 是权威记录（落地率承重）---
            if ptype == "patch_apply_end":
                # `success is True` 而非 truthy：字符串 "true" 之类的脏值不算成功。
                # 落地率与奖励挂钩，铁律「归属宁漏勿误」——宁可漏报也不虚报。
                if payload.get("success") is not True:
                    continue
                changes = payload.get("changes")
                if not isinstance(changes, dict):
                    continue
                edit_count += 1
                for p in changes:       # 🚨 只取 key；value 是文件全文 / diff，绝不碰
                    if isinstance(p, str) and p:
                        raw_paths.add(p)
                continue

    # session_id 兜底：缺 session_meta 的片段文件从文件名末段 UUID 取（未验证分支）
    if not session_id:
        m = _FILENAME_SID.search(path.stem)
        if m:
            session_id = m.group(1)

    # edited_paths 归一：实测 changes 的 key 就是绝对路径，但相对路径（若某版本这么落）
    # 直接丢会让落地率的分子无声变小。已知 cwd 时按工作区根拼回绝对路径；cwd 都不知道
    # 就只能丢——路径匹配是本机内交集，拼不出绝对路径的条目匹配不上任何东西。
    edited_paths: set[str] = set()
    for p in raw_paths:
        if p.startswith("/"):
            edited_paths.add(p)
        elif cwd:
            edited_paths.add(posixpath.normpath(posixpath.join(cwd, p)))

    token_usage: dict = {}
    if last_usage is not None:
        cached = _as_int(last_usage.get("cached_input_tokens"))
        token_usage[last_usage_model or "unknown"] = {
            # input 扣掉 cached：Codex 的 input_tokens 含缓存，CC 口径不含（决策 1）
            "input": max(0, _as_int(last_usage.get("input_tokens")) - cached),
            "output": _as_int(last_usage.get("output_tokens")),
            "cache_read": cached,
            "cache_creation": 0,    # Codex 无「写缓存」概念，恒 0 是事实不是缺测
        }

    return ParsedSession(
        file_path=str(path), session_id=session_id or "", cwd=cwd or "",
        git_branch=git_branch, user_turns=user_turns,
        tools_used=sorted(set(tools)), models_used=sorted(set(models)),
        first_ts=first_ts, last_ts=last_ts,
        edit_count=edit_count, token_usage=token_usage,
        mcp_servers=sorted(mcp_servers),
        thinking_block_count=thinking_block_count,
        cc_versions=sorted(versions),
        record_type_counts=record_type_counts,
        edited_paths=sorted(edited_paths),
        source=CODEX)


iter_sessions = file_scanner(_PATTERN, parse)

earliest_ts = head_earliest_ts(_PATTERN, lambda rec: parse_timestamp(rec.get("timestamp")))


def session_files(root) -> list[Path]:
    """该来源下的会话文件列表（init 向导按 cwd 归组时用，不做全量解析）。"""
    return sorted(Path(root).glob(_PATTERN))
