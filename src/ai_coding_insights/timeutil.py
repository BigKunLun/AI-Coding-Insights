from datetime import datetime, timezone

def parse_timestamp(ts: str | None) -> datetime | None:
    # transcript 由外部程序生成，timestamp 可能缺失或非字符串（脏记录），一律按不可解析处理
    if not isinstance(ts, str) or not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
