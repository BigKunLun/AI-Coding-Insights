"""opencode（SQLite 会话库）来源解析测试。

数据来源标注约定 —— 每个 fixture 的形状分两档，测试里逐处注明：
- **实测**：形状取自本机 v1.18.18 真库（3 会话 / 61 消息 / 251 part）。
  已覆盖：session 全列、message.data(user/assistant)、part.data 的
  text / tool / reasoning / step-start / step-finish / file、
  tool ∈ {bash, read, write, webfetch, task}、status ∈ {completed, error}。
- **源码推测（未经真实数据验证）**：本机零样本，形状取自反编译服务端 bundle 的
  effect-schema 定义。包括：`edit` 工具与它的 metadata.filediff、MCP 工具调用、
  snapshot / patch / compaction / subtask / agent / retry part、
  pending / running 态 tool part、workspace.branch、V2 表（session_message）。
  这些 fixture 都手工构造，若上游形状与推测不符，**测试会绿但真库解析会漏数**——
  故 parser 对它们一律走「认得出就取，认不出就不取」，不做强断言。

隐私红线（本文件最重的一组断言）：patch 全文 / 文件全文 / 读文件回显 / reasoning
原文这四类内容绝不能出现在 ParsedSession 的任何字段里，见
`test_隐私_禁忌原文不出现在任何字段`——它遍历 dataclass 全字段递归扫哨兵串。
"""
import json
import sqlite3
import dataclasses
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ai_coding_insights import opencode_source as ocs
from ai_coding_insights.sources import OPENCODE
from ai_coding_insights.timeutil import parse_timestamp

FIXTURES = Path(__file__).parent / "fixtures" / "opencode"
SCHEMA_SQL = (FIXTURES / "schema.sql").read_text(encoding="utf-8")

# 本机真库实测的 migration 末行；parser 白名单里必须有它，否则真库直接降级为空
REAL_STAMP = "20260622202450_simplify_session_input"

MS = 1786625469370          # 实测量级的 epoch 毫秒（2026-06 前后）


def iso(ms: int) -> str:
    return (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=ms)).isoformat()


@pytest.fixture(autouse=True)
def 隔离本机配置(tmp_path, monkeypatch):
    """把 MCP 配置目录钉到一个空目录（需要时各测试再 monkeypatch 覆盖）。

    不隔离的话 `mcp_server_names()` 会读开发者真机的
    `~/.config/opencode/opencode.jsonc`——测试结果就跟「这台机器装了哪些 MCP」
    绑死（真机上恰好有个叫 `bash` 的 server 就会红），CI 上还读不到。
    顺带清掉 $OPENCODE_DB，免得开发者本机设了它就污染 db_paths 的用例。
    """
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(tmp_path / "_空配置目录"))
    monkeypatch.delenv("OPENCODE_DB", raising=False)


# ------------------------------------------------------------------ fixture 构造

def make_db(path, *, sessions=(), messages=(), parts=(),
            stamp=REAL_STAMP, session_message_rows=0, drop_tables=()):
    """按真库结构建一个测试库，数据全手工造。

    `sessions/messages/parts` 收的是 dict，缺的列由这里补默认值——测试只写它关心的字段。
    """
    conn = sqlite3.connect(str(path))
    conn.executescript(SCHEMA_SQL)
    for t in drop_tables:
        conn.execute(f"DROP TABLE IF EXISTS `{t}`")
    if stamp is not None:
        conn.execute("INSERT INTO migration (id, time_completed) VALUES (?, ?)", (stamp, MS))
    # session.project_id 有外键指到 project；SQLite 默认不强制外键，但仍补一行，
    # 免得日后有人开了 PRAGMA foreign_keys 就整片红。
    conn.execute("INSERT INTO project (id, worktree, time_created, time_updated,"
                 " sandboxes) VALUES ('global', '/tmp/repo', ?, ?, '[]')", (MS, MS))
    for s in sessions:
        row = {
            "id": "ses_a", "project_id": "global", "workspace_id": None, "parent_id": None,
            "slug": "slug", "directory": "/tmp/repo", "path": None, "title": "t",
            "version": "1.18.18", "share_url": None, "summary_additions": None,
            "summary_deletions": None, "summary_files": None, "summary_diffs": None,
            "metadata": None, "cost": 0.0, "tokens_input": 0, "tokens_output": 0,
            "tokens_reasoning": 0, "tokens_cache_read": 0, "tokens_cache_write": 0,
            "revert": None, "permission": None, "agent": "build", "model": None,
            "time_created": MS, "time_updated": MS, "time_compacting": None,
            "time_archived": None,
        }
        row.update(s)
        cols = ", ".join(f"`{k}`" for k in row)
        conn.execute(f"INSERT INTO session ({cols}) VALUES ({', '.join('?' * len(row))})",
                     tuple(row.values()))
    for m in messages:
        row = {"id": "msg_a", "session_id": "ses_a", "time_created": MS,
               "time_updated": MS, "data": "{}"}
        row.update(m)
        if isinstance(row["data"], dict):
            row["data"] = json.dumps(row["data"])
        conn.execute("INSERT INTO message (id, session_id, time_created, time_updated, data)"
                     " VALUES (?,?,?,?,?)", tuple(row.values()))
    for p in parts:
        row = {"id": "prt_a", "message_id": "msg_a", "session_id": "ses_a",
               "time_created": MS, "time_updated": MS, "data": "{}"}
        # 调用点写 part_id= 更好读（与 tool_part 的参数名一致），这里归一到列名 id
        p = dict(p)
        if "part_id" in p:
            p["id"] = p.pop("part_id")
        row.update(p)
        if isinstance(row["data"], dict):
            row["data"] = json.dumps(row["data"])
        conn.execute("INSERT INTO part (id, message_id, session_id, time_created,"
                     " time_updated, data) VALUES (?,?,?,?,?,?)", tuple(row.values()))
    for i in range(session_message_rows):
        conn.execute("INSERT INTO session_message (id, session_id, type, seq,"
                     " time_created, time_updated, data) VALUES (?,?,?,?,?,?,?)",
                     (f"smsg_{i}", "ses_a", "text", i, MS, MS, "{}"))
    conn.commit()
    conn.close()
    return Path(path)


def tool_part(tool, *, status="completed", input=None, metadata=None,
              part_id="prt_a", message_id="msg_a", session_id="ses_a", call_id="call_1"):
    """tool part 的 data JSON（形状实测自真库：callID / tool / state{status,input,metadata}）。"""
    state = {"status": status, "input": input or {}, "metadata": metadata or {},
             "time": {"start": MS}, "title": "t"}
    return {"id": part_id, "message_id": message_id, "session_id": session_id,
            "data": {"type": "tool", "callID": call_id, "tool": tool, "state": state}}


def text_part(text, **extra):
    d = {"type": "text", "text": text, "time": {"start": MS}}
    d.update({k: v for k, v in extra.items()
              if k in ("synthetic", "ignored")})
    row = {"data": d}
    row.update({k: v for k, v in extra.items()
                if k not in ("synthetic", "ignored")})
    return row


def user_msg(mid="msg_u", **kw):
    """实测：user message.data 的 model 是**嵌套对象** {providerID, modelID}。"""
    row = {"id": mid, "data": {"role": "user", "agent": "build",
                               "model": {"providerID": "prov", "modelID": "mdl"},
                               "time": {"created": MS}}}
    row.update(kw)
    return row


def asst_msg(mid="msg_a", **kw):
    """实测：assistant message.data 的 model 是**平铺**的 modelID / providerID。"""
    row = {"id": mid, "data": {"role": "assistant", "agent": "build", "mode": "build",
                               "modelID": "mdl", "providerID": "prov",
                               "time": {"created": MS, "completed": MS + 10}}}
    row.update(kw)
    return row


def one_session(tmp_path, **kw):
    """建库 → 解析 → 返回唯一一个 ParsedSession（断言确实只有一个）。"""
    db = make_db(tmp_path / "opencode.db", **kw)
    got = list(ocs.iter_sessions(db.parent))
    assert len(got) == 1, f"期望 1 个会话，实得 {len(got)}"
    return got[0]


# ------------------------------------------------------------------ schema 版本策略

def test_白名单含本机实测版本戳():
    # 真库末行版本戳必须在白名单里，否则本机直接全量降级为空——这是最容易静默失效的一环
    assert REAL_STAMP in ocs.KNOWN_SCHEMA_STAMPS
    assert ocs.is_known_schema(REAL_STAMP)


def test_schema_stamp_取_migration_末行(tmp_path):
    db = tmp_path / "opencode.db"
    make_db(db, stamp=None)
    conn = sqlite3.connect(str(db))
    # 乱序插入：版本戳必须按 id 排序取末行，不能取插入顺序的最后一条
    for s in ("20260101000000_b", REAL_STAMP, "20250101000000_a"):
        conn.execute("INSERT INTO migration (id, time_completed) VALUES (?,?)", (s, MS))
    conn.commit()
    conn.close()
    assert ocs.schema_stamp(db) == REAL_STAMP


def test_未知版本戳_显式降级不硬解(tmp_path, capsys):
    """核心防线：schema 变了宁可交白卷 + 出声，也不能硬解出一份看着正常的错数据。"""
    make_db(tmp_path / "opencode.db", stamp="29991231235959_未来迁移",
            sessions=[{"id": "ses_a"}],
            messages=[user_msg()], parts=[dict(text_part("你好"), message_id="msg_u")])
    got = list(ocs.iter_sessions(tmp_path))
    assert got == []
    assert "29991231235959" in capsys.readouterr().err


def test_无_migration_表_视为未知(tmp_path):
    make_db(tmp_path / "opencode.db", stamp=None, drop_tables=("migration",),
            sessions=[{"id": "ses_a"}])
    assert ocs.schema_stamp(tmp_path / "opencode.db") is None
    assert not ocs.is_known_schema(None)
    assert list(ocs.iter_sessions(tmp_path)) == []


def test_migration_表为空_视为未知(tmp_path):
    make_db(tmp_path / "opencode.db", stamp=None, sessions=[{"id": "ses_a"}])
    assert ocs.schema_stamp(tmp_path / "opencode.db") is None
    assert list(ocs.iter_sessions(tmp_path)) == []


def test_检测到_V2_数据要告警(tmp_path, capsys):
    """V2（session_message/session_input）与 V1 完全不兼容；有 V2 行还只读 V1 会漏数，必须出声。"""
    make_db(tmp_path / "opencode.db", session_message_rows=3,
            sessions=[{"id": "ses_a"}],
            messages=[user_msg()], parts=[dict(text_part("你好"), message_id="msg_u")])
    got = list(ocs.iter_sessions(tmp_path))
    assert len(got) == 1                       # V1 仍照读，只是加告警
    err = capsys.readouterr().err
    assert "V2" in err and "session_message" in err


def test_无_V2_数据不告警(tmp_path, capsys):
    make_db(tmp_path / "opencode.db", sessions=[{"id": "ses_a"}])
    list(ocs.iter_sessions(tmp_path))
    assert "V2" not in capsys.readouterr().err


# ------------------------------------------------------------------ 库发现

def test_glob_匹配非正式_channel_库名(tmp_path):
    # 非正式 channel 写 opencode-<channel>.db，只认 opencode.db 会整个漏掉 dev/beta 用户
    for n in ("opencode.db", "opencode-dev.db"):
        make_db(tmp_path / n, sessions=[{"id": "ses_" + n[:4]}])
    (tmp_path / "unrelated.db").write_bytes(b"")
    names = [p.name for p in ocs.db_paths(tmp_path)]
    assert names == ["opencode-dev.db", "opencode.db"]


def test_OPENCODE_DB_环境变量完全覆盖(tmp_path, monkeypatch):
    make_db(tmp_path / "opencode.db", sessions=[{"id": "ses_ignored"}])
    other = make_db(tmp_path / "elsewhere.db", sessions=[{"id": "ses_wanted"}])
    monkeypatch.setenv("OPENCODE_DB", str(other))
    assert ocs.db_paths(tmp_path) == [other]
    assert [s.session_id for s in ocs.iter_sessions(tmp_path)] == ["ses_wanted"]


def test_OPENCODE_DB_为_memory_不炸(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_DB", ":memory:")
    assert list(ocs.iter_sessions(tmp_path)) == []
    assert ocs.earliest_ts(tmp_path) is None


def test_多库合并迭代(tmp_path):
    make_db(tmp_path / "opencode.db", sessions=[{"id": "ses_1"}])
    make_db(tmp_path / "opencode-beta.db", sessions=[{"id": "ses_2"}])
    assert sorted(s.session_id for s in ocs.iter_sessions(tmp_path)) == ["ses_1", "ses_2"]


# ------------------------------------------------------------------ 健壮性（都不许抛）

def test_根目录不存在不抛(tmp_path):
    missing = tmp_path / "nope"
    assert list(ocs.iter_sessions(missing)) == []
    assert ocs.earliest_ts(missing) is None
    assert ocs.db_paths(missing) == []


def test_不是_sqlite_文件不抛(tmp_path):
    (tmp_path / "opencode.db").write_text("这不是数据库", encoding="utf-8")
    assert list(ocs.iter_sessions(tmp_path)) == []
    assert ocs.earliest_ts(tmp_path) is None
    assert ocs.schema_stamp(tmp_path / "opencode.db") is None


def test_缺表不抛(tmp_path):
    make_db(tmp_path / "opencode.db", drop_tables=("part",), sessions=[{"id": "ses_a"}])
    assert list(ocs.iter_sessions(tmp_path)) == []


def test_缺_session_message_表不抛(tmp_path):
    """老版本没有 V2 表；探测 V2 不能因为表不存在就整场失败。"""
    make_db(tmp_path / "opencode.db", drop_tables=("session_message",),
            sessions=[{"id": "ses_a"}], messages=[user_msg()],
            parts=[dict(text_part("你好"), message_id="msg_u")])
    assert len(list(ocs.iter_sessions(tmp_path))) == 1


def test_坏_JSON_跳过而不吞整场会话(tmp_path):
    """单条脏记录不该让整个会话解析失败（与 CC parser 逐行 try 同策略）。"""
    s = one_session(
        tmp_path,
        sessions=[{"id": "ses_a"}],
        messages=[user_msg("msg_u"), asst_msg("msg_a"),
                  {"id": "msg_bad", "data": "{不是合法 JSON"}],
        parts=[dict(text_part("真人一句"), part_id="prt_1", message_id="msg_u"),
               {"id": "prt_bad", "message_id": "msg_a", "data": "]["},
               tool_part("bash", part_id="prt_2")])
    assert [t.text for t in s.user_turns] == ["真人一句"]
    assert s.tools_used == ["bash"]


def test_data_是裸值不抛(tmp_path):
    s = one_session(tmp_path, sessions=[{"id": "ses_a"}],
                    messages=[{"id": "msg_x", "data": "[1,2,3]"}, asst_msg()],
                    parts=[{"id": "prt_x", "message_id": "msg_a", "data": "\"字符串\""},
                           tool_part("bash", part_id="prt_y")])
    assert s.tools_used == ["bash"]


# ------------------------------------------------------------------ 字段逐个映射

def test_基础字段映射(tmp_path):
    s = one_session(tmp_path, sessions=[{
        "id": "ses_abc", "directory": "/Users/x/proj", "version": "1.18.18",
        "time_created": MS, "time_updated": MS + 60_000}])
    assert s.session_id == "ses_abc"
    assert s.cwd == "/Users/x/proj"
    assert s.source == OPENCODE
    assert s.cc_versions == ["1.18.18"]
    assert s.file_path.endswith("opencode.db")       # DB 型来源无「每会话一个文件」


def test_git_branch_恒为_None(tmp_path):
    """opencode 不落会话当时的分支（只有实验性 workspace 表才记）；不去跑 git 反查——
    那查到的是「现在的分支」，不是会话发生时的分支，属于伪证据。"""
    s = one_session(tmp_path, sessions=[{"id": "ses_a", "workspace_id": "wsp_1"}])
    assert s.git_branch is None


def test_时间戳_毫秒转_ISO_且能被_parse_timestamp_读回(tmp_path):
    """跨来源口径一致性闸门：窗口/时长/日粒度全靠 timeutil.parse_timestamp 解析它。"""
    s = one_session(tmp_path, sessions=[{
        "id": "ses_a", "time_created": 1786625469370, "time_updated": 1786627986772}])
    assert s.first_ts == "2026-08-13T12:51:09.370000+00:00"
    dt = parse_timestamp(s.first_ts)
    assert dt is not None and dt.tzinfo is not None
    assert int(dt.timestamp() * 1000) == 1786625469370
    assert parse_timestamp(s.last_ts) > dt


def test_时间跨度并入_message_时间(tmp_path):
    s = one_session(tmp_path, sessions=[{"id": "ses_a", "time_created": MS,
                                         "time_updated": MS + 1000}],
                    messages=[asst_msg("msg_a", time_created=MS - 5000,
                                       time_updated=MS + 90_000)])
    assert s.first_ts == iso(MS - 5000)
    assert s.last_ts == iso(MS + 90_000)


def test_models_used_是_provider_斜杠_model(tmp_path):
    s = one_session(tmp_path, sessions=[{"id": "ses_a"}], messages=[
        asst_msg("msg_1", data={"role": "assistant", "modelID": "glm-5.2",
                                "providerID": "zhipuai-coding-plan"}),
        asst_msg("msg_2", data={"role": "assistant", "modelID": "sonnet",
                                "providerID": "anthropic"})])
    assert s.models_used == ["anthropic/sonnet", "zhipuai-coding-plan/glm-5.2"]


def test_user_message_的嵌套_model_不进_models_used(tmp_path):
    """user message 的 model 是嵌套对象，形状与 assistant 的平铺字段不同；
    它记的是「这条输入将发给谁」，不是「谁答的」，不算模型使用。"""
    s = one_session(tmp_path, sessions=[{"id": "ses_a"}],
                    messages=[user_msg("msg_u")],
                    parts=[dict(text_part("你好"), message_id="msg_u")])
    assert s.models_used == []


# ------------------------------------------------------------------ 真人轮次

def test_synthetic_与_ignored_的_text_part_不算真人轮次(tmp_path):
    """synthetic 是机器注入（compaction 续写 / plan 批准转 build / 子代理结果回注 /
    plan-mode 提示注入）。不排除就会把机器话当成人类输入，直接污染姿势分布分母。"""
    s = one_session(
        tmp_path, sessions=[{"id": "ses_a"}],
        messages=[user_msg("msg_1"), user_msg("msg_2"), user_msg("msg_3")],
        parts=[dict(text_part("真人写的"), part_id="prt_1", message_id="msg_1"),
               dict(text_part("机器注入的续写", synthetic=True),
                    part_id="prt_2", message_id="msg_2"),
               dict(text_part("被忽略的", ignored=True),
                    part_id="prt_3", message_id="msg_3")])
    assert [t.text for t in s.user_turns] == ["真人写的"]


def test_轮次_uuid_用_message_id(tmp_path):
    """证据指针要能回看到具体这一轮；message.data 里的 id 被上游剥掉了，只能用列上的 id。"""
    s = one_session(tmp_path, sessions=[{"id": "ses_a"}],
                    messages=[user_msg("msg_zzz")],
                    parts=[dict(text_part("一句话"), message_id="msg_zzz")])
    assert [t.uuid for t in s.user_turns] == ["msg_zzz"]
    assert s.user_turns[0].timestamp == iso(MS)


def test_同一轮多个_text_part_按_part_id_升序拼接(tmp_path):
    """part 顺序按 id（prt_ 前缀单调），不按 time_created——同毫秒写入时后者顺序不稳。"""
    s = one_session(tmp_path, sessions=[{"id": "ses_a"}], messages=[user_msg("msg_u")],
                    parts=[dict(text_part("后"), part_id="prt_b",
                                message_id="msg_u", time_created=MS - 999),
                           dict(text_part("前"), part_id="prt_a",
                                message_id="msg_u", time_created=MS)])
    assert [t.text for t in s.user_turns] == ["前后"]


def test_assistant_的_text_part_不算真人轮次(tmp_path):
    s = one_session(tmp_path, sessions=[{"id": "ses_a"}], messages=[asst_msg("msg_a")],
                    parts=[dict(text_part("模型的回答"), message_id="msg_a")])
    assert s.user_turns == []


def test_无_text_part_的_user_message_不算轮次(tmp_path):
    """实测有纯附件（file part）的 user message；无文字内容不构成一次指令输入。"""
    s = one_session(tmp_path, sessions=[{"id": "ses_a"}], messages=[user_msg("msg_u")],
                    parts=[{"id": "prt_f", "message_id": "msg_u",
                            "data": {"type": "file", "filename": "a.png",
                                     "mime": "image/png", "url": "data:..."}}])
    assert s.user_turns == []


# ------------------------------------------------------------------ 子会话

def test_子会话不被当成独立会话(tmp_path):
    """parent_id 非空的行是 task 派生的子会话。当独立会话产出会让会话数虚高、
    并把子代理内部的模型自问自答混进「真人轮次」口径。"""
    s = one_session(
        tmp_path,
        sessions=[{"id": "ses_parent", "parent_id": None},
                  {"id": "ses_child", "parent_id": "ses_parent"}],
        messages=[user_msg("msg_u", session_id="ses_parent"),
                  user_msg("msg_c", session_id="ses_child")],
        parts=[dict(text_part("父会话真人输入"), part_id="prt_1",
                    message_id="msg_u", session_id="ses_parent"),
               dict(text_part("子会话里的注入 prompt"), part_id="prt_2",
                    message_id="msg_c", session_id="ses_child")])
    assert s.session_id == "ses_parent"
    assert [t.text for t in s.user_turns] == ["父会话真人输入"]


def test_task_工具映射为_Agent_口径别名(tmp_path):
    """signals.aggregate 用字面量 "Agent" 数 subagent_sessions（不可改动）。
    OPENCODE 声明了 CAP_SUBAGENT，若照原名 task 输出，subagent_sessions 会渲染成
    真值 0 = 「你没用过子代理」的错误结论——正是本项目最怕的静默错报。"""
    s = one_session(tmp_path, sessions=[{"id": "ses_a"}], messages=[asst_msg()],
                    parts=[tool_part("task", part_id="prt_1",
                                     input={"subagent_type": "general",
                                            "description": "d", "prompt": "p"},
                                     metadata={"sessionId": "ses_child",
                                               "parentSessionId": "ses_a"})])
    assert "Agent" in s.tools_used
    assert "task" not in s.tools_used          # 是改名不是加名，tool_breadth 不虚高


def test_同一_message_内多个_task_算并行(tmp_path):
    """口径映射（非同一事物）：opencode 无「单轮并发 N 个」的直接对应，
    按同一条 assistant message 下的 task part 数聚合，与 CC 的 message.id 聚合同构。"""
    s = one_session(
        tmp_path, sessions=[{"id": "ses_a"}],
        messages=[asst_msg("msg_1"), asst_msg("msg_2")],
        parts=[tool_part("task", part_id="prt_1", message_id="msg_1", call_id="c1"),
               tool_part("task", part_id="prt_2", message_id="msg_1", call_id="c2"),
               tool_part("task", part_id="prt_3", message_id="msg_1", call_id="c3"),
               tool_part("task", part_id="prt_4", message_id="msg_2", call_id="c4")])
    assert s.max_parallel_agents == 3
    assert s.parallel_agent_turns == 1          # 只有 msg_1 是 ≥2


def test_单个_task_不算并行轮次(tmp_path):
    s = one_session(tmp_path, sessions=[{"id": "ses_a"}], messages=[asst_msg()],
                    parts=[tool_part("task", part_id="prt_1")])
    assert s.max_parallel_agents == 1
    assert s.parallel_agent_turns == 0


# ------------------------------------------------------------------ 编辑（承重：落地率入口）

def test_edit_成功才进_edited_paths(tmp_path):
    """归属宁漏勿误：落地率与奖励挂钩，没改成的文件绝不能算 AI 落地。
    ⚠️ `edit` 工具与 metadata.filediff 形状**源自源码，本机零样本未经真实数据验证**。"""
    s = one_session(
        tmp_path, sessions=[{"id": "ses_a"}], messages=[asst_msg()],
        parts=[tool_part("edit", part_id="prt_1", status="completed", call_id="c1",
                         metadata={"filediff": {"file": "/repo/ok.py",
                                                "additions": 3, "deletions": 1}}),
               tool_part("edit", part_id="prt_2", status="error", call_id="c2",
                         metadata={"filediff": {"file": "/repo/failed.py"}}),
               tool_part("edit", part_id="prt_3", status="pending", call_id="c3",
                         input={"filePath": "/repo/pending.py"}),
               tool_part("edit", part_id="prt_4", status="running", call_id="c4",
                         input={"filePath": "/repo/running.py"})])
    assert s.edited_paths == ["/repo/ok.py"]
    assert s.edit_count == 1


@pytest.mark.parametrize("metadata,input,expected", [
    # legacy edit：路径在 metadata.filediff.file（源码推测，未经真实数据验证）
    ({"filediff": {"file": "/repo/a.py"}}, {}, "/repo/a.py"),
    # write：路径在 metadata.filepath（**实测**自本机真库，注意是全小写 filepath）
    ({"filepath": "/repo/b.py"}, {}, "/repo/b.py"),
    # legacy input.filePath（**实测**：write 的 input 就是这个键）
    ({}, {"filePath": "/repo/c.py"}, "/repo/c.py"),
    # 新版 input.path（源码推测，未经真实数据验证）
    ({}, {"path": "/repo/d.py"}, "/repo/d.py"),
])
def test_两套路径键都要认(tmp_path, metadata, input, expected):
    """新旧两套形状并存；只认一套 = 换个版本就静默漏掉全部编辑路径。"""
    s = one_session(tmp_path, sessions=[{"id": "ses_a"}], messages=[asst_msg()],
                    parts=[tool_part("edit", metadata=metadata, input=input)])
    assert s.edited_paths == [expected]


def test_write_与_apply_patch_也算编辑(tmp_path):
    s = one_session(
        tmp_path, sessions=[{"id": "ses_a"}], messages=[asst_msg()],
        parts=[tool_part("write", part_id="prt_1", call_id="c1",
                         metadata={"filepath": "/repo/new.py"},
                         input={"filePath": "/repo/new.py", "content": "文件全文"}),
               # apply_patch 形状源自源码，未经真实数据验证
               tool_part("apply_patch", part_id="prt_2", call_id="c2",
                         input={"path": "/repo/p.py"})])
    assert s.edited_paths == ["/repo/new.py", "/repo/p.py"]
    assert s.edit_count == 2


def test_路径认不出仍计入_edit_count(tmp_path):
    """未测量 ≠ 0 的镜像：路径提不出来不代表这次编辑没发生，编辑动作照计，
    只是不进 edited_paths（宁可少一条 git 交集证据，不可少算一次编辑动作）。"""
    s = one_session(tmp_path, sessions=[{"id": "ses_a"}], messages=[asst_msg()],
                    parts=[tool_part("apply_patch", input={"patch": "*** Begin Patch"})])
    assert s.edit_count == 1
    assert s.edited_paths == []


def test_非编辑工具不进_edited_paths(tmp_path):
    """read 也带 filePath，但读文件不是编辑；算进去会把落地率分子灌水。"""
    s = one_session(tmp_path, sessions=[{"id": "ses_a"}], messages=[asst_msg()],
                    parts=[tool_part("read", input={"filePath": "/repo/read_only.py"})])
    assert s.edited_paths == []
    assert s.edit_count == 0


# ------------------------------------------------------------------ token

def test_token_取会话累计列不重复累加(tmp_path):
    """projector 已把 step-finish 累加进 session 列；再累加 step-finish part 就是双算。"""
    s = one_session(
        tmp_path,
        sessions=[{"id": "ses_a", "tokens_input": 1000, "tokens_output": 200,
                   "tokens_cache_read": 5000, "tokens_cache_write": 30,
                   "tokens_reasoning": 77,
                   "model": json.dumps({"id": "glm-5.2", "providerID": "zhipuai",
                                        "variant": "default"})}],
        messages=[asst_msg()],
        parts=[{"id": "prt_1", "message_id": "msg_a",
                "data": {"type": "step-finish", "cost": 0,
                         "tokens": {"input": 999, "output": 999,
                                    "cache": {"read": 999, "write": 999}}}}])
    assert s.token_usage == {"zhipuai/glm-5.2": {
        "input": 1000, "output": 200, "cache_read": 5000, "cache_creation": 30}}


def test_session_model_的_id_键与_modelID_键都要认(tmp_path):
    """**实测**：本机 v1.18.18 的 session.model JSON 用的是 `id` 而非 `modelID`
    （与 assistant message 的平铺 modelID 不同名）。两个键都认，避免换版本静默丢分桶。"""
    for raw, key in ((json.dumps({"id": "m1", "providerID": "p"}), "p/m1"),
                     (json.dumps({"modelID": "m2", "providerID": "p"}), "p/m2")):
        d = tmp_path / key.replace("/", "_")
        d.mkdir()
        s = one_session(d, sessions=[{"id": "ses_a", "model": raw, "tokens_input": 5}])
        assert list(s.token_usage) == [key]


def test_token_全零不产生空分桶(tmp_path):
    """parse_health 的 token 金丝雀用 `bool(s.token_usage)` 判存在性；
    全零还建桶会让「没测到」看起来像「测到了」。"""
    s = one_session(tmp_path, sessions=[{"id": "ses_a"}])
    assert s.token_usage == {}


def test_model_列坏掉时回退到_assistant_模型(tmp_path):
    s = one_session(tmp_path, sessions=[{"id": "ses_a", "model": "{坏 JSON",
                                         "tokens_input": 10}],
                    messages=[asst_msg()])
    assert list(s.token_usage) == ["prov/mdl"]


def test_cost_不进任何字段(tmp_path):
    """订阅制下 cost 恒 0，当成本指标读会得出「零成本」的错误结论。"""
    s = one_session(tmp_path, sessions=[{"id": "ses_a", "cost": 12.5}])
    assert "12.5" not in json.dumps(dataclasses.asdict(s), ensure_ascii=False)


# ------------------------------------------------------------------ thinking / plan / 工具

def test_reasoning_part_只数块数(tmp_path):
    s = one_session(tmp_path, sessions=[{"id": "ses_a"}], messages=[asst_msg()],
                    parts=[{"id": f"prt_{i}", "message_id": "msg_a",
                            "data": {"type": "reasoning", "text": "思考原文",
                                     "time": {"start": MS}}} for i in range(3)])
    assert s.thinking_block_count == 3


def test_plan_取_agent_与_plan_exit_的并集(tmp_path):
    s = one_session(tmp_path, sessions=[{"id": "ses_a", "agent": "plan"}],
                    messages=[asst_msg()],
                    parts=[tool_part("plan_exit", part_id="prt_1", call_id="c1"),
                           tool_part("plan_exit", part_id="prt_2", call_id="c2")])
    assert s.plan_mode_count == 3               # agent=plan 记 1 + 两次 plan_exit


def test_plan_两种形状各自单独也算(tmp_path):
    (tmp_path / "a").mkdir()
    a = one_session(tmp_path / "a", sessions=[{"id": "ses_a", "agent": "plan"}])
    (tmp_path / "b").mkdir()
    b = one_session(tmp_path / "b", sessions=[{"id": "ses_a", "agent": "build"}],
                    messages=[asst_msg()], parts=[tool_part("plan_exit")])
    assert a.plan_mode_count == 1 and b.plan_mode_count == 1


def test_非_plan_会话为零(tmp_path):
    s = one_session(tmp_path, sessions=[{"id": "ses_a", "agent": "build"}],
                    messages=[asst_msg()], parts=[tool_part("bash")])
    assert s.plan_mode_count == 0


def test_tools_used_去重排序且含各状态(tmp_path):
    """工具广度按「用过什么」算，失败的调用也是用过（与 CC 口径一致：tool_use 就计）。"""
    s = one_session(tmp_path, sessions=[{"id": "ses_a"}], messages=[asst_msg()],
                    parts=[tool_part("bash", part_id="prt_1", call_id="c1"),
                           tool_part("bash", part_id="prt_2", call_id="c2"),
                           tool_part("webfetch", part_id="prt_3", call_id="c3",
                                     status="error"),
                           tool_part("read", part_id="prt_4", call_id="c4")])
    assert s.tools_used == ["bash", "read", "webfetch"]


def test_record_type_counts_收录未知类型(tmp_path):
    """漂移雷达：parser 不认识的新 part 类型也要落进去，否则新版本加了记录我们看不见。
    ⚠️ snapshot / compaction 形状源自源码，未经真实数据验证。"""
    s = one_session(
        tmp_path, sessions=[{"id": "ses_a"}],
        messages=[user_msg("msg_u"), asst_msg("msg_a")],
        parts=[dict(text_part("你好"), part_id="prt_1", message_id="msg_u"),
               {"id": "prt_2", "message_id": "msg_a",
                "data": {"type": "snapshot", "snapshot": "abc"}},
               {"id": "prt_3", "message_id": "msg_a",
                "data": {"type": "compaction", "auto": True}}])
    assert s.record_type_counts == {
        "message:user": 1, "message:assistant": 1,
        "part:text": 1, "part:snapshot": 1, "part:compaction": 1}


# ------------------------------------------------------------------ MCP

def test_mcp_从配置读_server_名再前缀匹配(tmp_path, monkeypatch):
    """内置工具 apply_patch / todowrite / plan_exit 也含下划线，纯字符串匹配必误判；
    必须拿配置里的 server 名单做前缀匹配才准。"""
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "opencode.json").write_text(json.dumps({
        "mcp": {"context7": {"type": "remote", "url": "http://x",
                             "headers": {"Authorization": "Bearer 绝密"}},
                "web-search-prime": {"type": "remote"}}}), encoding="utf-8")
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(cfg))
    s = one_session(
        tmp_path, sessions=[{"id": "ses_a"}], messages=[asst_msg()],
        parts=[tool_part("context7_resolve_library_id", part_id="prt_1", call_id="c1"),
               # server 名里的 '-' 被 sanitize 成 '_'
               tool_part("web_search_prime_search", part_id="prt_2", call_id="c2"),
               tool_part("apply_patch", part_id="prt_3", call_id="c3",
                         input={"path": "/repo/a.py"}),
               tool_part("todowrite", part_id="prt_4", call_id="c4")])
    assert s.mcp_servers == ["context7", "web-search-prime"]
    # 工具名转成 CC 口径的 mcp__<server>__<tool>：signals 用 startswith("mcp__") 数 mcp_sessions
    assert "mcp__context7__resolve_library_id" in s.tools_used
    assert "mcp__web-search-prime__search" in s.tools_used
    # 内置的含下划线工具保持原名，绝不能被当成 MCP
    assert "apply_patch" in s.tools_used and "todowrite" in s.tools_used


def test_无配置时不识别_MCP_而非误判(tmp_path, monkeypatch):
    """识别不到就留空（sources 未声明 CAP_MCP，signals 会自动把它摘进 unmeasured）；
    猜测式匹配会把内置工具误报成 MCP，比留空更坏。"""
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(tmp_path / "无此目录"))
    s = one_session(tmp_path, sessions=[{"id": "ses_a"}], messages=[asst_msg()],
                    parts=[tool_part("context7_resolve_library_id")])
    assert s.mcp_servers == []
    assert s.tools_used == ["context7_resolve_library_id"]


def test_jsonc_注释可容忍(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "opencode.jsonc").write_text(
        '{\n  // 行注释\n  "$schema": "https://x/config.json",\n'
        '  /* 块注释 */\n  "mcp": { "zread": { "type": "remote" } }\n}\n',
        encoding="utf-8")
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(cfg))
    assert ocs.mcp_server_names() == ("zread",)


def test_jsonc_剥不干净时放弃识别而不抛(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "opencode.jsonc").write_text("{ 完全不是 JSON ", encoding="utf-8")
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(cfg))
    assert ocs.mcp_server_names() == ()


def test_配置里的密钥值绝不读取(tmp_path, monkeypatch):
    """那个文件里有明文 API key（本机实测存在 Authorization: Bearer …）。
    只准取键名——返回值里出现任何 value 就是把密钥往外递。"""
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "opencode.json").write_text(json.dumps({
        "mcp": {"zai": {"headers": {"Authorization": "Bearer sk-绝密令牌"},
                        "environment": {"Z_AI_API_KEY": "sk-另一个绝密"}}}}),
        encoding="utf-8")
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(cfg))
    names = ocs.mcp_server_names()
    assert names == ("zai",)
    assert "绝密" not in json.dumps(names, ensure_ascii=False)


def test_最长前缀优先(tmp_path, monkeypatch):
    """server 名互为前缀时（web / web_search），短名先匹配会把 server 认错。"""
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "opencode.json").write_text(
        json.dumps({"mcp": {"web": {}, "web_search": {}}}), encoding="utf-8")
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(cfg))
    s = one_session(tmp_path, sessions=[{"id": "ses_a"}], messages=[asst_msg()],
                    parts=[tool_part("web_search_query")])
    assert s.mcp_servers == ["web_search"]
    assert s.tools_used == ["mcp__web_search__query"]


# ------------------------------------------------------------------ 隐私（定位级铁律）

FORBIDDEN = {
    "patch全文": "PATCH内容哨兵_diff_不得外泄",
    "文件全文": "FILECONTENT哨兵_write_content_不得外泄",
    "读文件回显": "DISPLAY哨兵_read_display_不得外泄",
    "reasoning原文": "REASONING哨兵_思考原文_不得外泄",
    "bash命令": "BASHCMD哨兵_命令原文",
    "工具输出": "OUTPUT哨兵_工具回包原文",
    "会话标题": "TITLE哨兵_会话标题含业务语义",
    "子代理prompt": "SUBPROMPT哨兵_派发指令原文",
}


def _walk(v):
    """递归展平 dataclass / dict / list，产出所有标量，供哨兵扫描。"""
    if dataclasses.is_dataclass(v):
        v = dataclasses.asdict(v)
    if isinstance(v, dict):
        for k, sub in v.items():
            yield from _walk(k)
            yield from _walk(sub)
    elif isinstance(v, (list, tuple, set)):
        for sub in v:
            yield from _walk(sub)
    else:
        yield v


def test_隐私_禁忌原文不出现在任何字段(tmp_path):
    """遍历 ParsedSession 全字段（含嵌套 UserTurn / dict / list）扫哨兵串。

    这些内容一旦进 ParsedSession，就会顺着 profile_input → batch-NN.json → LLM
    出本机，直接捅破「会话原文与业务语义永不出本机」的定位级铁律。"""
    s = one_session(
        tmp_path,
        sessions=[{"id": "ses_a", "title": FORBIDDEN["会话标题"],
                   "summary_diffs": FORBIDDEN["patch全文"]}],
        messages=[asst_msg()],
        parts=[
            tool_part("edit", part_id="prt_1", call_id="c1",
                      input={"filePath": "/repo/a.py",
                             "oldString": FORBIDDEN["文件全文"],
                             "newString": FORBIDDEN["文件全文"]},
                      metadata={"filediff": {"file": "/repo/a.py"},
                                "diff": FORBIDDEN["patch全文"]}),
            tool_part("write", part_id="prt_2", call_id="c2",
                      input={"filePath": "/repo/b.py", "content": FORBIDDEN["文件全文"]},
                      metadata={"filepath": "/repo/b.py"}),
            tool_part("read", part_id="prt_3", call_id="c3",
                      input={"filePath": "/repo/c.py"},
                      metadata={"display": {"text": FORBIDDEN["读文件回显"]},
                                "preview": FORBIDDEN["读文件回显"]}),
            tool_part("bash", part_id="prt_4", call_id="c4",
                      input={"command": FORBIDDEN["bash命令"]},
                      metadata={"output": FORBIDDEN["工具输出"]}),
            tool_part("task", part_id="prt_5", call_id="c5",
                      input={"subagent_type": "general",
                             "prompt": FORBIDDEN["子代理prompt"]},
                      metadata={"sessionId": "ses_child"}),
            {"id": "prt_6", "message_id": "msg_a",
             "data": {"type": "reasoning", "text": FORBIDDEN["reasoning原文"]}},
        ])
    blob = "\n".join(str(x) for x in _walk(s))
    for label, sentinel in FORBIDDEN.items():
        assert sentinel not in blob, f"{label} 泄漏进了 ParsedSession"
    # 反向确认这一场确实解析出了东西（否则空对象也能过上面的断言）
    assert s.edited_paths == ["/repo/a.py", "/repo/b.py"]
    assert s.thinking_block_count == 1
    assert "Agent" in s.tools_used


def test_隐私_state_output_不进任何字段(tmp_path):
    """state.output 是工具回包全文（bash stdout / 文件内容）。"""
    s = one_session(tmp_path, sessions=[{"id": "ses_a"}], messages=[asst_msg()],
                    parts=[{"id": "prt_1", "message_id": "msg_a",
                            "data": {"type": "tool", "callID": "c1", "tool": "bash",
                                     "state": {"status": "completed",
                                               "output": "OUTPUT哨兵",
                                               "input": {"command": "CMD哨兵"}}}}])
    blob = "\n".join(str(x) for x in _walk(s))
    assert "OUTPUT哨兵" not in blob and "CMD哨兵" not in blob


def test_隐私_真人输入原文照常保留(tmp_path):
    """反向闸门：真人输入是画像的立身之本，必须保留（脱敏在 profile_input 的
    redact 那层做）。若上面的隐私实现误伤这里，画像会整个空掉。"""
    s = one_session(tmp_path, sessions=[{"id": "ses_a"}], messages=[user_msg("msg_u")],
                    parts=[dict(text_part("帮我把登录改成 OAuth"), message_id="msg_u")])
    assert [t.text for t in s.user_turns] == ["帮我把登录改成 OAuth"]


# ------------------------------------------------------------------ earliest_ts / 只读

def test_earliest_ts_取全库最早(tmp_path):
    make_db(tmp_path / "opencode.db",
            sessions=[{"id": "ses_1", "time_created": MS + 5000},
                      {"id": "ses_2", "time_created": MS, "parent_id": "ses_1"}])
    # 含子会话：这是「数据从何时起」，不是「会话口径」
    assert ocs.earliest_ts(tmp_path) == iso(MS)


def test_earliest_ts_未知_schema_也降级(tmp_path):
    make_db(tmp_path / "opencode.db", stamp="29991231235959_未来",
            sessions=[{"id": "ses_1", "time_created": MS}])
    assert ocs.earliest_ts(tmp_path) is None


def test_earliest_ts_空库为_None(tmp_path):
    make_db(tmp_path / "opencode.db")
    assert ocs.earliest_ts(tmp_path) is None


def test_只读打开_不写用户活库(tmp_path):
    """上游有一串 SQLite 损坏 / 锁竞争的 open issue，用户的活库绝不能被我们写。"""
    db = make_db(tmp_path / "opencode.db", sessions=[{"id": "ses_a"}],
                 messages=[user_msg("msg_u")],
                 parts=[dict(text_part("你好"), message_id="msg_u")])
    before = db.read_bytes()
    list(ocs.iter_sessions(tmp_path))
    ocs.earliest_ts(tmp_path)
    assert db.read_bytes() == before
    # 且连接确实是 ro：拿同一条路径尝试写必须被 SQLite 拒绝
    conn = ocs._connect(db)
    assert conn is not None
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO migration (id, time_completed) VALUES ('x', 1)")
    conn.close()


def test_sources_注册面能拿到本模块(tmp_path):
    """接缝闸门：sources.py 的 OPENCODE 惰性绑定的就是这两个函数名，改名即断链。"""
    from ai_coding_insights.sources import get_source
    src = get_source(OPENCODE)
    make_db(tmp_path / "opencode.db", sessions=[{"id": "ses_a"}])
    assert [s.session_id for s in src.iter_sessions(tmp_path)] == ["ses_a"]
    assert src.earliest_ts(tmp_path) == iso(MS)
