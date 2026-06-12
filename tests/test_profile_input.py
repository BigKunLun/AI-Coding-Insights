from ai_coding_insights.models import ParsedSession, UserTurn, OutcomeStats, SessionStats
from ai_coding_insights.profile_input import build_session_input
from ai_coding_insights.signals import anchors


def test_anchors_detect_behavior():
    a = anchors("这段不对，应该用 https://x.com 里的写法\n```py\nx=1\n```")
    assert a["override"] and a["link"] and a["code"]
    assert anchors("Traceback (most recent call last)")["error"]


def test_build_session_input_redacts_secrets():
    # 密钥类 token 在出规则层前必须就地脱敏——batch JSON 是 LLM 层的直接输入
    turns = [UserTurn("u1", "部署时用 token=ghp_" + "a" * 24 + " 登录", "t")]
    s = ParsedSession("f", "sess", "/r", "main", turns, ["Bash"], ["m"], None, None)
    st = SessionStats("sess", "/r", 1, 0.0, 60.0, ["Bash"], ["m"])
    o = OutcomeStats("sess", "/r", 0, 0, 0)
    d = build_session_input(s, st, o)
    assert "ghp_" not in d["turns"][0]["text"]
    assert "[REDACTED]" in d["turns"][0]["text"]


def test_build_session_input_keeps_raw_full_text():
    turns = [UserTurn("u1", "继续", "t"), UserTurn("u2", "X" * 1000, "t")]
    s = ParsedSession("f", "sess", "/r", "main", turns, ["Bash"], ["m"], None, None)
    st = SessionStats("sess", "/r", 2, 0.5, 60.0, ["Bash"], ["m"])
    o = OutcomeStats("sess", "/r", 3, 2, 9)
    d = build_session_input(s, st, o)
    assert d["session_id"] == "sess"
    assert d["signals"]["landed_count"] == 2 and d["signals"]["commit_count"] == 3
    assert d["turns"][0]["text"] == "继续"
    # 不再截断：完整保留 1000 字，无省略号
    assert d["turns"][1]["text"] == "X" * 1000
    assert not d["turns"][1]["text"].endswith("…")
    assert "anchors" in d["turns"][0]


def test_build_session_input_no_truncation():
    # 50 条 turn，其中一条为 2000 个非空白字符
    turns = [UserTurn(f"u{i}", "继续", "t") for i in range(50)]
    long_text = "x" * 2000
    long_idx = 30
    turns[long_idx] = UserTurn(f"u{long_idx}", long_text, "t")
    s = ParsedSession("f", "sess", "/r", "main", turns, ["Bash"], ["m"], None, None)
    st = SessionStats("sess", "/r", 50, 0.5, 60.0, ["Bash"], ["m"])
    o = OutcomeStats("sess", "/r", 0, 0, 0)
    d = build_session_input(s, st, o)
    # 不再截到 40 条
    assert len(d["turns"]) == 50
    # 长 turn 完整保留 2000 字，无截短、无省略号
    assert len(d["turns"][long_idx]["text"]) == 2000
    assert d["turns"][long_idx]["text"] == long_text
    assert d["turns"][long_idx]["chars"] == 2000
