from .models import ParsedSession, SessionStats, OutcomeStats
from .redact import redact_secrets
from .signals import anchors


def build_session_input(session: ParsedSession, stats: SessionStats, outcome: OutcomeStats) -> dict:
    turns = []
    for t in session.user_turns:
        # 真人输入原文不截断，但密钥类 token 在出规则层前就地脱敏（redact 是唯一一道秘钥网）
        txt = redact_secrets(t.text.strip())
        turns.append({"uuid": t.uuid, "chars": t.char_len, "text": txt, "anchors": anchors(txt)})
    return {
        "session_id": session.session_id,
        "cwd": session.cwd,
        "file_path": session.file_path,
        "signals": {
            "turn_count": stats.turn_count,
            "short_turn_ratio": round(stats.short_turn_ratio, 3),
            "tools_used": session.tools_used,
            "models_used": session.models_used,
            "commit_count": outcome.commit_count,
            "landed_count": outcome.landed_count,
            "edit_count": outcome.edit_count,
        },
        "turns": turns,
    }
