"""opencode 来源适配（SQLite 会话库，非 jsonl）。

与 CC / Codex 不同，opencode 把全部会话塞在**一个 SQLite 库**里，没有「每会话一个
文件」这回事——这正是 `sources.Source` 把接口定成 `iter_sessions` 而非「列文件 +
parse」的原因。本模块只暴露 registry 要的两个函数（`iter_sessions` / `earliest_ts`），
外加几个供调试与测试用的公开件（`schema_stamp` / `is_known_schema` / `db_paths` /
`mcp_server_names`）。

═══════════════════════════════════════════════════════════════════════════
一、库在哪：文件名不固定，且环境变量可完全覆盖
═══════════════════════════════════════════════════════════════════════════
正式 channel 写 `opencode.db`，非正式 channel 写 `opencode-<channel>.db`；`$OPENCODE_DB`
可指到任意路径（甚至 `:memory:`）。故 `db_paths()` **先看 $OPENCODE_DB，否则 glob
`opencode*.db`**——只认死 `opencode.db` 会让 dev/beta 用户整个漏掉。

🚨 **只读打开，永不写用户活库**：库是 WAL 模式且上游有一串 SQLite 损坏 / 锁竞争的
open issue。一律 `sqlite3.connect("file:...?mode=ro", uri=True)`（见 `_connect`，
有测试守着写入必须被 SQLite 拒绝）。

⚠️ **`mode=ro` 的边界，说清楚免得误以为零副作用**（本机实测）：库文件本身一个字节
都不会变（损坏风险的来源就在这），但 SQLite 读 WAL 库时**仍会在同目录建
`-shm` / `-wal` 边车文件**——这是 WAL 读取路径的固有行为，opencode 自己跑起来也有
这两个文件，不影响它的数据。
想彻底不碰目录只有 `immutable=1` 一条路，而它**明确不能用**：immutable 会跳过
`-wal`，意味着**读不到还留在 WAL 里没 checkpoint 的最新会话**——那是「静默少数据」，
比建两个边车文件坏得多，正是本项目最不能接受的故障形态。

═══════════════════════════════════════════════════════════════════════════
二、schema 版本策略（承重）
═══════════════════════════════════════════════════════════════════════════
上游 4 个半月 38 次 migration（平均 4 天一次），release notes 不标破坏性变更。
本项目最危险的故障是**不报错、只安静产出错误结论**，所以这里的策略是白名单：

    migration 表末行（按 id 排序）= schema 版本戳 → 不在 KNOWN_SCHEMA_STAMPS
    → 出声告警 + 返回空迭代，**绝不硬解**。

硬解一个改过形状的 schema，结果不是崩溃而是「轮次少一半 / 编辑数归零」的报告，
用户还以为是自己的问题。宁可交白卷。追加新版本时：核对形状后把版本戳加进
`KNOWN_SCHEMA_STAMPS` 即可（模块级常量，就是为此设计的）。

**另有两套并存的消息模型**：V1 = `message` + `part`（当前主力）；V2 = `session_message`
+ `session_input` + `session_context_epoch`（在建，与 V1 完全不兼容，2026-06-22 被整表
清空过）。本 parser **只读 V1**；一旦发现 `session_message` 有行就显式告警——
默默只读 V1 会漏掉全部 V2 会话，又是一份「看着正常」的错报告。

═══════════════════════════════════════════════════════════════════════════
三、字段映射表（证据来源：[实测]=本机 v1.18.18 真库；[源码]=反编译服务端 bundle，
    本机零样本**未经真实数据验证**）
═══════════════════════════════════════════════════════════════════════════
ParsedSession 字段        ← 来源                                          证据
────────────────────────────────────────────────────────────────────────────
file_path                 ← 库文件路径（DB 型来源无每会话文件）           [实测]
session_id                ← `session.id`（ses_ 前缀，单调可当时序）       [实测]
cwd                       ← `session.directory`（绝对路径）                [实测]
git_branch                ← 恒 None，见下「为什么不反查 git」             [实测]
first_ts / last_ts        ← `time_created`/`time_updated`（**epoch 毫秒**）[实测]
                            转 ISO8601 UTC，并用 message 时间放宽跨度
user_turns                ← role=="user" 的 message 下 type=="text" 的 part[实测]
  .uuid                   ← `message.id`（data JSON 里的 id 被上游剥掉了） [实测]
tools_used                ← type=="tool" part 的 `.tool`（含失败调用）     [实测]
models_used               ← assistant message 的**平铺** modelID/providerID[实测]
                            拼成 "<providerID>/<modelID>"
token_usage               ← `session.tokens_*` 会话累计列（见下）          [实测]
edit_count / edited_paths ← 成功的 edit/write/apply_patch part（见下）     [实测/源码]
thinking_block_count      ← type=="reasoning" 的 part **块数**（不取正文） [实测]
plan_mode_count           ← `session.agent=="plan"` ∪ `plan_exit` 工具调用 [实测/源码]
max_parallel_agents       ← 同一条 assistant message 下 task part 数的峰值 [实测]
parallel_agent_turns      ← 同上聚合里 ≥2 的 message 条数                  [实测]
cc_versions               ← `[session.version]`（如 "1.18.18"；开发构建为
                            字面量 "local"）                               [实测]
mcp_servers               ← 配置文件 mcp 键名 × 工具名前缀匹配（见下）     [源码]
record_type_counts        ← `message:<role>` + `part:<type>`（含未知类型） [实测]
────────────────────────────────────────────────────────────────────────────
**恒为默认值**（opencode 无对位概念或本机未验证，故不填而非填 0）：
commits / option_pick_count / skill_names / skill_invoke_counts /
background_task_count。它们对应的 aggregate 字段由 `sources.py` 的能力集声明
落进 `unmeasured`，渲染层打「未测量」——**未测量 ≠ 0**。

── 为什么不去反查 git 分支 ──
只有实验性的 `workspace.branch` 记分支（本机 0 行）。跑 `git branch` 拿到的是
「现在的分支」而不是「会话发生时的分支」，那是伪证据。故一律 None。归属判定走
cwd，不依赖分支，无损失。

── token 为什么取会话列而不累加 part ──
`session.tokens_input/output/cache_read/cache_write` 是 projector **已经把每个
step-finish 累加好**的会话累计值；再去累加 `step-finish` part 就是双算。
分桶映射：input←tokens_input，output←tokens_output，cache_read←tokens_cache_read，
cache_creation←**tokens_cache_write**。
`tokens_reasoning` 无对位桶（ParsedSession 的桶只有 4 格），**主动丢弃**而不是并进
output——并进去会让跨来源的 output 口径不可比。
`cost` 在订阅制下恒 0，**不取**：当成本指标读会得出「零成本」的错误结论。
分桶 key 取 `session.model` JSON 的 `<providerID>/<id>`——⚠️ [实测] 本机这个 JSON 用的
是 **`id`** 键（`{"id":"glm-5.2","providerID":"zhipuai-coding-plan"}`），与 assistant
message 上的 `modelID` **不同名**，两个键都认。

── 编辑：怎么算「成功」，路径从哪取 ──
落地率是与奖励挂钩的硬指标，铁律「归属宁漏勿误」：**只认 `state.status ==
"completed"`**，pending / running / error 一概不算。
路径按序尝试（新旧两套形状并存，只认一套 = 换版本就静默漏光）：
  1. `state.metadata.filediff.file`（legacy edit）              [源码，未经真数据验证]
  2. `state.metadata.filepath` / `.file`（write 用的就是全小写）[实测]
  3. `state.input.filePath`（legacy）                            [实测]
  4. `state.input.path`（新版）                                  [源码，未经真数据验证]
路径取不出来时**仍计 edit_count、只是不进 edited_paths**——「路径没提取到」不等于
「这次编辑没发生」，少算一次编辑动作比少一条 git 交集证据更失真。

── 口径别名：为什么把 `task` 改名成 `Agent` ──
`signals.aggregate_metrics` 用**字面量** `"Agent" in tools` 数 `subagent_sessions`、
用 `t.startswith("mcp__")` 数 `mcp_sessions`（那两个文件不归本模块改）。而
`sources.py` 给 OPENCODE **声明了 CAP_SUBAGENT**，意味着 `subagent_sessions` 不进
`unmeasured`——若照原名输出 `task`，它会渲染成**真值 0**，即「你没用过子代理」这个
错误结论。故做最小口径别名（**改名不是加名**，tool_breadth 不虚高）：
    task                    → Agent
    <server>_<tool>（MCP）  → mcp__<server>__<tool>
其余工具（bash/read/write/webfetch/…）保留 opencode 原生名。

── 并行子代理是口径映射，不是同一事物 ──
opencode 没有「同一条 message 内并发 N 个 agent」的直接对应。这里按**同一条
assistant message 下的 task part 数**聚合，与 CC 按 message.id 聚合同构。读数时
应理解为「同一轮里派出去几个」，而非 CC 那种确证的并发执行。

── MCP：为什么必须读配置文件 ──
命名规则是 `sanitize(clientName) + "_" + sanitize(name)`，但内置工具 `apply_patch` /
`todowrite` / `plan_exit` 也含下划线，纯字符串匹配必然误判。故先读
`~/.config/opencode/opencode.json[c]` 的 **mcp 键名**再做最长前缀匹配。
🚨 那个文件里有明文 API key（本机实测存在 `Authorization: Bearer …`）——
**只取键名，绝不读任何 value**（见 `mcp_server_names`）。识别不到就留空：
`sources.py` 未声明 CAP_MCP，真识别到了会由 `_drop_measured` 自动摘出 unmeasured。

── 为什么排除 parent_id 非空的行 ──
task 派生的子会话在 `session` 表里也是一行。当独立会话产出会让会话数虚高，更糟的是
子会话里那条「注入的 subagent prompt」是 role=user，会被当成**真人轮次**，直接污染
姿势分布的分母。故 `iter_sessions` 只产出根会话（子会话的内容通过父会话的 task
part 体现）。注意 `earliest_ts` **不做**这个过滤——它问的是「数据从何时起」。

═══════════════════════════════════════════════════════════════════════════
四、隐私（定位级铁律，违反即 bug）
═══════════════════════════════════════════════════════════════════════════
以下四类是**完整业务原文**，绝不能进 ParsedSession 任何字段、不落任何产物：
  · `state.metadata.diff`        —— 完整 patch
  · `state.input.content`        —— 写入文件的全文
  · `state.metadata.display.text`/`.preview` —— 读到的文件全文
  · `reasoning.text`             —— 模型思考原文（本模块只数块数）
另外主动不取的：`state.output`（工具回包全文）、`state.input.command`（命令原文）、
`state.input.prompt`（派发指令原文）、`session.title` / `session.summary_diffs`
（含业务语义）。实现上一律**按白名单取具体键**，从不整体搬运 dict——有
`test_隐私_禁忌原文不出现在任何字段` 遍历 dataclass 全字段扫哨兵串守着。
真人输入原文照常保留（脱敏在 `profile_input` 的 redact 那层做）。
"""
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from .models import ParsedSession, UserTurn
from .sources import OPENCODE

# ---------------------------------------------------------------- 常量（可追加）

#: 已核对过形状的 schema 版本戳（`migration` 表按 id 排序的**末行**）。
#: 不在此集 = 显式降级为空迭代 + 告警，绝不硬解。追加新版本前请先核对本文件
#: 「字段映射表」里标 [实测] 的那些键是否还在。
KNOWN_SCHEMA_STAMPS: frozenset[str] = frozenset({
    # 本机 v1.18.18 实测（3 会话 / 61 消息 / 251 part）
    "20260622202450_simplify_session_input",
})

#: parser 必须存在的 V1 表；缺任一张说明 schema 不是我们认识的形状，整库降级。
_REQUIRED_TABLES = ("session", "message", "part")

#: 算「编辑动作」的工具名。write/apply_patch 与 edit 同理都会改盘。
#: edit=[实测缺样本/源码]，write=[实测]，apply_patch/multiedit=[源码，未经真数据验证]。
_EDIT_TOOLS = frozenset({"edit", "write", "apply_patch", "multiedit"})

#: 子代理派发工具的原生名 → CC 口径别名（见顶部「口径别名」小节）。
_SUBAGENT_TOOL = "task"
_SUBAGENT_ALIAS = "Agent"

#: plan 模式退出工具的原生名。
_PLAN_EXIT_TOOL = "plan_exit"

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _warn(msg: str) -> None:
    """降级告警走 stderr。

    不抛异常也不静默：抛异常会让一次 schema 漂移把整个报告打掉，静默则退化成
    「不报错的错报告」——那正是本项目定义的最危险故障。stdout 留给编排契约
    （scan 的 manifest JSON），告警只能走 stderr。
    """
    print(f"[opencode-source] {msg}", file=sys.stderr)


# ---------------------------------------------------------------- 库发现与连接

def db_paths(root) -> list[Path]:
    """该来源下的会话库文件列表。

    `$OPENCODE_DB` 一旦设置就**完全覆盖**（上游语义如此），此时 root 被忽略。
    否则在 root 下 glob `opencode*.db`（覆盖 `opencode-<channel>.db` 这类非正式
    channel 库名），排序保证跨次运行结果稳定。`-wal` / `-shm` 边车文件天然不匹配。
    """
    env = (os.environ.get("OPENCODE_DB") or "").strip()
    if env:
        return [Path(env).expanduser()]
    try:
        return sorted(p for p in Path(root).expanduser().glob("opencode*.db")
                      if p.is_file())
    except OSError:
        return []


def _connect(path) -> sqlite3.Connection | None:
    """只读打开（`mode=ro`）。打不开一律返回 None，不抛。

    用 URI 形式而非普通路径：只有 URI 才能加 `mode=ro`。普通 `sqlite3.connect`
    会在文件不存在时**新建一个空库**——那就等于我们往用户数据目录里写垃圾。
    `:memory:` 直接判不可读：别的进程的内存库我们本来就读不到，假装能读只会
    产出一份空报告还不出声。

    ⚠️ 不加 `immutable=1`：那样会跳过 WAL，读不到还没 checkpoint 的最新会话
    （静默少数据）。代价是读 WAL 库会在同目录留下 `-shm`/`-wal` 边车文件——
    库文件本身不变，这是权衡后的选择，见模块顶部说明。
    """
    p = Path(path)
    if str(p) == ":memory:" or not str(p):
        return None
    try:
        uri = p.expanduser().resolve().as_uri() + "?mode=ro"
        return sqlite3.connect(uri, uri=True)
    except (sqlite3.Error, OSError, ValueError):
        return None


def _tables(conn: sqlite3.Connection) -> set[str]:
    try:
        return {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    except sqlite3.Error:
        return set()


def _stamp_of(conn: sqlite3.Connection) -> str | None:
    """migration 表按 id 排序的末行。

    id 形如 `YYYYMMDDHHMMSS_<名字>`，字典序即时间序，所以 `ORDER BY id DESC LIMIT 1`
    就是末行。**不能按插入顺序取最后一条**——补跑迁移时插入顺序会乱。
    """
    try:
        row = conn.execute("SELECT id FROM migration ORDER BY id DESC LIMIT 1").fetchone()
    except sqlite3.Error:
        return None
    return row[0] if row and isinstance(row[0], str) and row[0] else None


def schema_stamp(db_path) -> str | None:
    """公开版版本戳探测：自己开一次只读连接。读不到（缺表 / 空表 / 不是库）返回 None。"""
    conn = _connect(db_path)
    if conn is None:
        return None
    try:
        return _stamp_of(conn)
    finally:
        conn.close()


def is_known_schema(stamp) -> bool:
    """版本戳是否在白名单里。None / 非字符串一律 False（未知即降级）。"""
    return isinstance(stamp, str) and stamp in KNOWN_SCHEMA_STAMPS


def _has_v2_rows(conn: sqlite3.Connection) -> bool:
    """V2 消息模型是否已有数据。表不存在（老版本）不算有——那是正常的 V1 库。"""
    try:
        return conn.execute("SELECT 1 FROM session_message LIMIT 1").fetchone() is not None
    except sqlite3.Error:
        return False


# ---------------------------------------------------------------- 小工具

def _as_int(v) -> int:
    """脏值安全取整。bool 是 int 子类，显式排除，避免 True 被当 1 计。"""
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


def _pos_ms(v) -> int | None:
    """正的 epoch 毫秒才算有效时间戳；0 / 负数 / 非 int 视为缺失（不是 1970 年）。"""
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else None


def _iso(ms: int) -> str:
    """epoch 毫秒 → ISO8601 UTC 字符串。

    用 `_EPOCH + timedelta` 而非 `fromtimestamp(ms/1000)`：后者先过一遍 float，
    毫秒级会有舍入抖动，而窗口边界与日粒度聚合都按这个串切。
    产出形如 `2026-06-22T04:51:09.370000+00:00`，`timeutil.parse_timestamp` 能读回
    （有测试做 round-trip 守着，这是跨来源口径一致性的闸门）。
    """
    return (_EPOCH + timedelta(milliseconds=ms)).isoformat()


def _loads(raw):
    """data 列的 JSON。坏 JSON / 裸值一律返回 None——单条脏记录不该炸整场会话解析
    （与 CC parser 的逐行 try 同策略）。"""
    if not isinstance(raw, str):
        return None
    try:
        d = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def _dict(v) -> dict:
    return v if isinstance(v, dict) else {}


def _str(v) -> str | None:
    return v if isinstance(v, str) and v else None


# ---------------------------------------------------------------- MCP 名单

_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_]")


def _sanitize(name: str) -> str:
    """复刻上游 `sanitize`：非 [A-Za-z0-9_] 一律换成下划线。

    所以配置里的 server 名 `web-search-prime` 在工具名里长成 `web_search_prime_*`。
    """
    return _SANITIZE_RE.sub("_", name)


def _strip_jsonc_comments(raw: str) -> str:
    """剥 `//` 行注释与 `/* */` 块注释，**字符串字面量内的不动**。

    必须识别字符串状态：配置里 `"$schema": "https://…"` 的 `//` 一剥就把整行吃掉，
    JSON 反而更坏。转义符也要跟——`"a\\"//b"` 里的 `//` 仍在串内。
    """
    out, i, n = [], 0, len(raw)
    in_str = False
    while i < n:
        c = raw[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:      # 转义对整体保留，跳过下一个字符
                out.append(raw[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and raw[i + 1] == "/":
            while i < n and raw[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and raw[i + 1] == "*":
            i += 2
            while i + 1 < n and not (raw[i] == "*" and raw[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _config_dir(explicit=None) -> Path:
    if explicit is not None:
        return Path(explicit).expanduser()
    env = (os.environ.get("OPENCODE_CONFIG_DIR") or "").strip()
    if env:
        return Path(env).expanduser()
    xdg = (os.environ.get("XDG_CONFIG_HOME") or "").strip()
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "opencode"


def mcp_server_names(config_dir=None) -> tuple[str, ...]:
    """配置文件里声明的 MCP server **键名**（排序去重）。

    🚨 **只取键名，一个 value 都不读**：这个文件里有明文 API key（本机实测存在
    `Authorization: Bearer …` 与 `Z_AI_API_KEY`）。返回值里出现任何 value 就是把
    密钥往报告链路上递，有测试专门守着。

    读不到 / 解析不了一律返回空 tuple 而不是抛：识别不到 MCP 只是少一个软信号
    （`sources.py` 本就没声明 CAP_MCP，会落进 unmeasured），而猜测式匹配会把内置
    工具误报成 MCP——留空比误判安全。jsonc 剥注释剥不干净时同样放弃。
    """
    base = _config_dir(config_dir)
    for name in ("opencode.json", "opencode.jsonc"):
        try:
            raw = (base / name).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        data = _loads(raw)
        if data is None:
            data = _loads(_strip_jsonc_comments(raw))
        mcp = _dict(data).get("mcp")
        if isinstance(mcp, dict):
            return tuple(sorted(k for k in mcp if isinstance(k, str) and k))
    return ()


def _mcp_prefixes(servers) -> list[tuple[str, str]]:
    """[(sanitize 后的前缀, 配置里的原始 server 名)]，**按前缀长度降序**。

    降序是为了最长前缀优先：server 名互为前缀时（`web` 与 `web_search`），
    短名先匹配会把 `web_search_query` 的 server 认成 `web`。
    """
    return sorted(((_sanitize(s), s) for s in servers),
                  key=lambda t: len(t[0]), reverse=True)


def _canonical_tool(name: str, prefixes) -> tuple[str, str | None]:
    """opencode 原生工具名 → (CC 口径名, MCP server 名或 None)。见顶部「口径别名」。"""
    if name == _SUBAGENT_TOOL:
        return _SUBAGENT_ALIAS, None
    for prefix, server in prefixes:
        head = prefix + "_"
        if name.startswith(head) and len(name) > len(head):
            return f"mcp__{server}__{name[len(head):]}", server
    return name, None


# ---------------------------------------------------------------- 编辑路径提取

def _edit_path(state: dict) -> str | None:
    """从 tool state 里取被编辑文件的路径。

    🚨 **按白名单逐键取，绝不整体搬 dict**：同一个 state 里紧挨着的
    `metadata.diff`（完整 patch）、`input.content`（文件全文）、`metadata.display`
    （读到的文件全文）都是业务原文，碰一下就捅破隐私网。
    顺序即优先级，新旧两套形状都要认（详见顶部映射表）。
    """
    meta = _dict(state.get("metadata"))
    fd = _dict(meta.get("filediff"))
    for candidate in (fd.get("file"), meta.get("filepath"),
                      meta.get("filePath"), meta.get("file")):
        if (p := _str(candidate)) is not None:
            return p
    inp = _dict(state.get("input"))
    for candidate in (inp.get("filePath"), inp.get("path"), inp.get("file_path")):
        if (p := _str(candidate)) is not None:
            return p
    return None


# ---------------------------------------------------------------- 单会话解析

def _parse_session(conn: sqlite3.Connection, srow, db_path: Path, prefixes) -> ParsedSession:
    (sid, directory, version, agent, model_raw,
     tok_in, tok_out, tok_cr, tok_cw, s_created, s_updated) = srow

    # message 按 (time_created, id) 排；part 按 (message_id, id) 排。
    # ⚠️ part **必须按 id 而非 time_created** 排：prt_ 前缀是单调 ID，而同一毫秒内
    # 写入的多个 part 的 time_created 会打平，按时间排序结果不稳定 → 同一轮的多段
    # 文本会被拼错顺序。
    messages = conn.execute(
        "SELECT id, time_created, time_updated, data FROM message"
        " WHERE session_id = ? ORDER BY time_created, id", (sid,)).fetchall()
    parts_by_msg: dict[str, list] = {}
    for msg_id, _pid, data in conn.execute(
            "SELECT message_id, id, data FROM part"
            " WHERE session_id = ? ORDER BY message_id, id", (sid,)):
        parts_by_msg.setdefault(msg_id, []).append(data)

    user_turns: list[UserTurn] = []
    tools: set[str] = set()
    models: set[str] = set()
    mcp_servers: set[str] = set()
    edited_paths: set[str] = set()
    record_type_counts: dict[str, int] = {}
    edit_count = 0
    thinking_block_count = 0
    plan_exit_count = 0
    agents_by_msg: dict[str, int] = {}

    first_ms = _pos_ms(s_created)
    last_ms = _pos_ms(s_updated)

    for msg_id, m_created, m_updated, m_data in messages:
        # 会话活动跨度以 session 列为底、再被 message 时间放宽：session.time_updated
        # 可能被归档等操作二次触碰，message 时间才是真正的「干活到什么时候」。
        if (ms := _pos_ms(m_created)) is not None:
            first_ms = ms if first_ms is None else min(first_ms, ms)
        if (ms := _pos_ms(m_updated)) is not None:
            last_ms = ms if last_ms is None else max(last_ms, ms)

        md = _loads(m_data)
        if md is None:
            continue                       # 坏 JSON：跳这一条，不吞整场
        role = _str(md.get("role"))
        if role:
            key = f"message:{role}"
            record_type_counts[key] = record_type_counts.get(key, 0) + 1
        if role == "assistant":
            # assistant 的 model 是**平铺**字段（user 的是嵌套对象，形状不同，
            # 且那个记的是「发给谁」不是「谁答的」，不算模型使用）。
            if (mid_ := _str(md.get("modelID"))) is not None:
                pid = _str(md.get("providerID"))
                models.add(f"{pid}/{mid_}" if pid else mid_)

        turn_text: list[str] = []
        has_text_part = False
        for p_data in parts_by_msg.get(msg_id, ()):
            pd = _loads(p_data)
            if pd is None:
                continue
            ptype = _str(pd.get("type"))
            if not ptype:
                continue
            key = f"part:{ptype}"
            record_type_counts[key] = record_type_counts.get(key, 0) + 1

            if ptype == "reasoning":
                # 只数块数，**绝不取 .text**（那是模型思考原文）
                thinking_block_count += 1
            elif ptype == "text" and role == "user":
                # 🚨 synthetic 是机器注入（compaction 续写 / plan 批准转 build /
                # 后台 subagent 结果回注 / plan-mode 提示注入），ignored 是被作废的。
                # 不排除就会把机器的话当成人类输入，直接污染姿势分布的分母。
                if pd.get("synthetic") is True or pd.get("ignored") is True:
                    continue
                has_text_part = True
                turn_text.append(_str(pd.get("text")) or "")
            elif ptype == "tool":
                raw_name = _str(pd.get("tool"))
                if raw_name is None:
                    continue
                canon, server = _canonical_tool(raw_name, prefixes)
                tools.add(canon)
                if server:
                    mcp_servers.add(server)
                # plan_exit / task 按**原生名**判：万一有人把 MCP server 起名叫
                # `plan` 或 `task`，别名后的串就认不出来了。
                if raw_name == _PLAN_EXIT_TOOL:
                    plan_exit_count += 1
                if raw_name == _SUBAGENT_TOOL:
                    agents_by_msg[msg_id] = agents_by_msg.get(msg_id, 0) + 1
                if raw_name in _EDIT_TOOLS:
                    state = _dict(pd.get("state"))
                    # 归属宁漏勿误：只认 completed，pending/running/error 都不算
                    if _str(state.get("status")) == "completed":
                        edit_count += 1
                        if (fp := _edit_path(state)) is not None:
                            edited_paths.add(fp)

        if role == "user" and has_text_part:
            user_turns.append(UserTurn(
                uuid=msg_id or "",
                text="".join(turn_text),
                # data JSON 里的 time.created 与列上的 time_created 同源，取列的
                # （列一定在，JSON 里的字段可能随版本改名）
                timestamp=_iso(ms) if (ms := _pos_ms(m_created)) is not None else ""))

    token_usage = _token_usage(model_raw, models, tok_in, tok_out, tok_cr, tok_cw)

    return ParsedSession(
        # DB 型来源没有「每会话一个文件」，file_path 填库路径。它会随 profile_input
        # 进 LLM 批次，故这里天然安全：库路径不含任何项目 / 业务语义。
        file_path=str(db_path),
        session_id=_str(sid) or "",
        cwd=_str(directory) or "",
        git_branch=None,                    # 见顶部「为什么不反查 git 分支」
        user_turns=user_turns,
        tools_used=sorted(tools),
        models_used=sorted(models),
        first_ts=_iso(first_ms) if first_ms is not None else None,
        last_ts=_iso(last_ms) if last_ms is not None else None,
        edit_count=edit_count,
        token_usage=token_usage,
        # plan 两种形状取并集（与 CC parser 对 EnterPlanMode / permission-mode:plan
        # 取并集同策略）：整场跑在 plan agent 下记 1，外加每次 plan_exit 调用。
        plan_mode_count=(1 if _str(agent) == "plan" else 0) + plan_exit_count,
        mcp_servers=sorted(mcp_servers),
        thinking_block_count=thinking_block_count,
        max_parallel_agents=max(agents_by_msg.values(), default=0),
        parallel_agent_turns=sum(1 for c in agents_by_msg.values() if c >= 2),
        cc_versions=[v] if (v := _str(version)) else [],
        record_type_counts=record_type_counts,
        edited_paths=sorted(edited_paths),
        source=OPENCODE)


def _token_usage(model_raw, models, tok_in, tok_out, tok_cr, tok_cw) -> dict:
    """会话累计 token → `{model: {input,output,cache_read,cache_creation}}`。

    全零不建桶：`parse_health` 的 token 金丝雀用 `bool(s.token_usage)` 判存在性，
    建个全零桶会让「没测到」看起来像「测到了」，把漂移雷达弄瞎。
    """
    bucket = {"input": _as_int(tok_in), "output": _as_int(tok_out),
              "cache_read": _as_int(tok_cr), "cache_creation": _as_int(tok_cw)}
    if not any(bucket.values()):
        return {}
    return {_model_key(model_raw, models): bucket}


def _model_key(model_raw, models) -> str:
    """token 分桶 key，与 `models_used` 同格式（`<providerID>/<model>`）。

    ⚠️ [实测] session.model 这个 JSON 用 **`id`** 键，assistant message 用 `modelID`，
    两者不同名——只认一个就会在换版本时静默把 token 全并到 "unknown" 桶。
    """
    d = _dict(_loads(model_raw))
    mid_ = _str(d.get("id")) or _str(d.get("modelID"))
    if mid_:
        pid = _str(d.get("providerID"))
        return f"{pid}/{mid_}" if pid else mid_
    # 会话级 model 列缺失 / 坏掉时退回本会话实际答过话的模型；都没有才 "unknown"
    # （宁可标一个明显是兜底的 key，也不要丢掉 token 量级）。
    return sorted(models)[0] if models else "unknown"


# ---------------------------------------------------------------- 对外两个函数

def _iter_db(db_path: Path) -> Iterator[ParsedSession]:
    conn = _connect(db_path)
    if conn is None:
        return
    try:
        stamp = _stamp_of(conn)
        if not is_known_schema(stamp):
            _warn(f"{db_path.name}: 未知 schema 版本戳 {stamp!r}，"
                  f"已知 {sorted(KNOWN_SCHEMA_STAMPS)}；本库跳过不解析"
                  f"（宁可交白卷，也不产出形状对不上的错数据）")
            return
        missing = [t for t in _REQUIRED_TABLES if t not in _tables(conn)]
        if missing:
            _warn(f"{db_path.name}: 缺少必需表 {missing}，本库跳过不解析")
            return
        if _has_v2_rows(conn):
            _warn(f"{db_path.name}: 检测到 V2 数据（session_message 有行），"
                  f"本版只支持 V1（message/part），结果可能不完整")
        prefixes = _mcp_prefixes(mcp_server_names())
        try:
            # 只产出根会话；parent_id 非空的是 task 派生的子会话（见顶部说明）。
            rows = conn.execute(
                "SELECT id, directory, version, agent, model,"
                " tokens_input, tokens_output, tokens_cache_read, tokens_cache_write,"
                " time_created, time_updated FROM session"
                " WHERE parent_id IS NULL OR parent_id = '' ORDER BY id").fetchall()
        except sqlite3.Error as e:
            _warn(f"{db_path.name}: 读 session 表失败（{e}），本库跳过")
            return
        for srow in rows:
            try:
                yield _parse_session(conn, srow, db_path, prefixes)
            except sqlite3.Error:
                # 单个会话读失败不该让另外 N 个会话一起没有——与 CC 的
                # `file_scanner`「一份写坏的 transcript 不吞整场扫描」同策略。
                continue
    finally:
        conn.close()


def iter_sessions(root) -> Iterator[ParsedSession]:
    """产出该 root 下所有 opencode 库里的**根会话**（`sources.Source` 契约）。

    一个 root 下可能有多个库（正式 + 非正式 channel），逐库迭代；某一库降级
    （未知 schema / 打不开）不影响其余库。
    """
    for db_path in db_paths(root):
        yield from _iter_db(db_path)


def earliest_ts(root) -> str | None:
    """全库最早的会话创建时间（ISO8601 UTC），无数据返回 None。

    与 `iter_sessions` 不同，这里**不排除子会话**：它回答的是「本机 opencode 数据
    从何时起」，用来和窗口 since_date 比对、识别「名义窗口 vs 实际数据起点」的错位；
    子会话也是实打实的数据。未知 schema 同样降级为 None——版本戳都对不上时，
    连 time_created 是不是毫秒都不该假设。
    """
    earliest: int | None = None
    for db_path in db_paths(root):
        conn = _connect(db_path)
        if conn is None:
            continue
        try:
            if not is_known_schema(_stamp_of(conn)):
                continue
            try:
                row = conn.execute("SELECT MIN(time_created) FROM session").fetchone()
            except sqlite3.Error:
                continue
            if row and (ms := _pos_ms(row[0])) is not None:
                earliest = ms if earliest is None else min(earliest, ms)
        finally:
            conn.close()
    return _iso(earliest) if earliest is not None else None
