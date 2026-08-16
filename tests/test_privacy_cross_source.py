"""隐私铁律的**跨来源**闸门（spec 验收标准第 4 条）。

各家 parser 的单测各自查过自己不泄漏，但那是「每家自证」。这里查的是另一件事：
**这道网是不是所有来源共用同一个出口**。多源化最容易出的事故不是某家 parser 写错，
而是新加一家时绕开了 `profile_input.build_session_input` —— 那是密钥脱敏（`redact`）
的唯一落点，绕开它 batch 就带着原始 token 进了 LLM 上下文，而且不会有任何报错。

三层守：
1. **出口唯一**：批次内容只能由 `build_session_input` 产出（内省 cli 的取数路径）。
2. **来源无关**：无论 `ParsedSession.source` 是哪家，密钥都被抹掉。
3. **真数据**：拿各家真实 fixture 跑一遍，确认业务原文 / 文件全文 / diff 一个字都不进批次。
"""
import inspect
import json
import re
import sqlite3
from pathlib import Path

import pytest

from ai_coding_insights import cli
from ai_coding_insights.models import OutcomeStats, ParsedSession, SessionStats, UserTurn
from ai_coding_insights.profile_input import build_session_input
from ai_coding_insights.sources import SOURCE_NAMES

FIXTURES = Path(__file__).parent / "fixtures"

# 每条都是真实形态的凭证样本；一条都不许出现在批次里。
_密钥样本 = [
    "sk-abcdefghijklmnopqrstuvwxyz012345",
    "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
    "AKIA0123456789ABCDEF",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NSJ9.QWxpY2VCb2JDaGFybGll",
    "Authorization: Bearer abcdef0123456789abcdef",
    "postgres://user:Str0ng!P@ss@db.internal:5432/appdb",
    "password=Str0ng!P@ss",
    "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----",
]


def _会话(source: str, 文本: str) -> ParsedSession:
    return ParsedSession(
        file_path="/tmp/x", session_id="s1", cwd="/tmp/repo", git_branch=None,
        user_turns=[UserTurn("u1", 文本, "2026-08-01T00:00:00Z")],
        tools_used=[], models_used=[], first_ts=None, last_ts=None, source=source)


def _批次文本(session: ParsedSession) -> str:
    st = SessionStats(session.session_id, session.cwd, len(session.user_turns), 0.0,
                      None, session.tools_used, session.models_used)
    oc = OutcomeStats(session.session_id, session.cwd, 0, 0, 0)
    return json.dumps(build_session_input(session, st, oc), ensure_ascii=False)


# ------------------------------------------------------------ 1. 出口唯一

def test_批次内容只由_build_session_input_产出():
    """密钥网只有一个落点，取数路径必须都从那儿过。

    这条钉的是**结构**而不是行为：将来加一家来源时，如果有人在 `_emit_batches` 里
    另起一条「直接把 ParsedSession 序列化进 batch」的近路，本测试立刻红。
    行为型测试挡不住这种事——它只会测已存在的那条路。
    """
    源码 = inspect.getsource(cli._emit_batches)
    assert "build_session_input" in 源码, "_emit_batches 不再经 build_session_input 取批次内容"
    m = re.search(r"batches\s*=\s*make_batches\((\w+)\)", 源码)
    assert m, "_emit_batches 里读不到 make_batches(...) 的调用"
    变量 = m.group(1)
    赋值 = re.search(rf"{变量}\s*=\s*\[build_session_input\(", 源码)
    assert 赋值, (
        f"喂给 make_batches 的 {变量} 不是由 build_session_input 逐会话构造的——"
        "有人绕开了唯一的密钥脱敏落点")


def test_脱敏落点在_build_session_input_内部且覆盖全部自由文本():
    """batch 里唯一的自由文本字段是 turn 的 `text`，它必须过 redact。"""
    源码 = inspect.getsource(build_session_input)
    assert "redact_secrets" in 源码
    assert re.search(r'"text":\s*txt', 源码) or re.search(r"txt\s*=\s*redact_secrets", 源码), (
        "build_session_input 里 text 字段不是脱敏后的值")


# ------------------------------------------------------------ 2. 来源无关

@pytest.mark.parametrize("source", SOURCE_NAMES)
@pytest.mark.parametrize("密钥", _密钥样本)
def test_任何来源的批次都不带凭证(source, 密钥):
    产物 = _批次文本(_会话(source, f"帮我看看这个报错，配置里是 {密钥} 这一行"))
    裸值 = 密钥.split()[-1].strip("'\"")
    assert 裸值 not in 产物, f"{source} 的批次里漏出了凭证：{裸值[:12]}…"
    assert "[REDACTED]" in 产物


@pytest.mark.parametrize("source", SOURCE_NAMES)
def test_任何来源的批次字段面都一致(source):
    """批次结构与来源无关——extractor 的 prompt 只写了一套字段名。

    某家 parser 若让 batch 多长出/少掉一个字段，extractor 会安静地读空。
    """
    产物 = json.loads(_批次文本(_会话(source, "把这段改成按配置注入")))
    assert set(产物) == {"session_id", "cwd", "file_path", "signals", "turns"}
    assert set(产物["turns"][0]) == {"uuid", "chars", "text", "anchors"}


# ------------------------------------------------------------ 3. 真 fixture

def test_codex_真_fixture_的批次不含文件全文或_diff():
    """Codex 的 patch 记录里带 `content`（新建文件全文）与 `unified_diff`。

    它们**只能**留在 parser 内部用来数编辑、取路径；一旦进了批次就是业务原文出规则层。
    """
    from ai_coding_insights.codex_source import parse

    fixture = FIXTURES / "codex" / "full-session.jsonl"
    原始 = fixture.read_text(encoding="utf-8")
    session = parse(fixture)
    产物 = _批次文本(session)

    # 从 fixture 里取出真实存在的 patch 正文片段，逐条确认没进批次
    片段 = []
    for line in 原始.splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        changes = (rec.get("payload") or {}).get("changes")
        if isinstance(changes, dict):
            for ch in changes.values():
                for key in ("content", "unified_diff"):
                    v = ch.get(key)
                    if isinstance(v, str) and len(v) > 12:
                        片段.append(v[:40])
    assert 片段, "fixture 里没有 patch 正文，这条守卫会空转——请检查 fixture"
    for f in 片段:
        assert f not in 产物, f"Codex 批次里漏出了文件正文/diff 片段：{f[:20]}…"


def test_opencode_真_schema_下的批次不含工具原文(tmp_path):
    """opencode 把**读到的文件全文**、模型思考原文都存在 part 表里，比 CC 的 jsonl 更「重」。

    用真库导出的 schema 建一份带原文的库，确认这些槽位一个字都没进批次。
    """
    from ai_coding_insights.opencode_source import KNOWN_SCHEMA_STAMPS, iter_sessions

    schema = (FIXTURES / "opencode" / "schema.sql").read_text(encoding="utf-8")
    db = tmp_path / "opencode.db"
    conn = sqlite3.connect(db)
    conn.executescript(schema)
    conn.execute("INSERT INTO migration (id, time_completed) VALUES (?, ?)",
                 (sorted(KNOWN_SCHEMA_STAMPS)[-1], 1),)
    # session 表 NOT NULL 且无默认值的列必须给全（真库 schema，不能只填我们关心的几列）
    conn.execute(
        "INSERT INTO session (id, project_id, slug, directory, title, version,"
        " time_created, time_updated) VALUES (?,?,?,?,?,?,?,?)",
        ("ses_a", "global", "demo-slug", str(tmp_path / "repo"), "会话标题（业务原文，不该外泄）",
         "1.18.18", 1786625469480, 1786625471252))
    禁忌 = {
        "reasoning": "内部思考原文：这里应当先确认调用方的边界条件再改",
        "read_output": "文件全文原文 line1 line2 line3 秘密业务逻辑",
        "diff": "@@ -1 +1 @@\n-旧的一行\n+新的一行",
        # 会话标题是 LLM 生成的、几乎必然含业务语义，同样不许进批次
        "title": "会话标题（业务原文，不该外泄）",
    }
    conn.execute("INSERT INTO message (id, session_id, time_created, time_updated, data)"
                 " VALUES (?,?,?,?,?)",
                 ("msg_u", "ses_a", 1786625469480, 1786625469480,
                  json.dumps({"role": "user", "time": {"created": 1786625469480}})))
    conn.execute("INSERT INTO message (id, session_id, time_created, time_updated, data)"
                 " VALUES (?,?,?,?,?)",
                 ("msg_a", "ses_a", 1786625469500, 1786625469500,
                  json.dumps({"role": "assistant", "modelID": "m", "providerID": "p",
                              "time": {"created": 1786625469500}})))
    conn.execute("INSERT INTO part (id, message_id, session_id, time_created, time_updated, data)"
                 " VALUES (?,?,?,?,?,?)",
                 ("prt_1", "msg_u", "ses_a", 1, 1,
                  json.dumps({"type": "text", "text": "把这段改成按配置注入"})))
    conn.execute("INSERT INTO part (id, message_id, session_id, time_created, time_updated, data)"
                 " VALUES (?,?,?,?,?,?)",
                 ("prt_2", "msg_a", "ses_a", 2, 2,
                  json.dumps({"type": "reasoning", "text": 禁忌["reasoning"]})))
    conn.execute("INSERT INTO part (id, message_id, session_id, time_created, time_updated, data)"
                 " VALUES (?,?,?,?,?,?)",
                 ("prt_3", "msg_a", "ses_a", 3, 3,
                  json.dumps({"type": "tool", "tool": "read", "callID": "c1",
                              "state": {"status": "completed",
                                        "input": {"filePath": str(tmp_path / "repo" / "a.py")},
                                        "output": 禁忌["read_output"],
                                        "metadata": {"diff": 禁忌["diff"]}}})))
    conn.commit()
    conn.close()

    sessions = list(iter_sessions(tmp_path))
    assert sessions, "fixture 库里应解析出一场会话"
    产物 = "".join(_批次文本(s) for s in sessions)
    for 名, 原文 in 禁忌.items():
        assert 原文[:20] not in 产物, f"opencode 批次里漏出了 {名} 原文"
    assert "把这段改成按配置注入" in 产物, "真人输入被误删了（脱敏不该吃掉真人轮次）"
