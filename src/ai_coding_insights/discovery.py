import json
from datetime import datetime, timedelta, date
from pathlib import Path
from .models import RemoteRule, ParsedSession
from .repo_identity import classify
from .jsonl_parser import parse_session
from .timeutil import parse_timestamp

_HEAD_LINES = 50  # detect_data_start 每个 transcript 最多读前几行；文件可能极大，绝不全读。
                  # 文件头可能有数行无 timestamp 的元记录（summary/file-history 等），上限须容纳它们。

def discover_sessions(projects_dir, rules: list[RemoteRule] | None,
                              lookback_days: int, now: datetime,
                              since: datetime | None = None) -> list[ParsedSession]:
    """rules=None 即 mode="all"：全部纳入，不跑 git 归属判定（省子进程）。
    rules 为列表即 mode="include"：仅纳入 remote 命中的项目（宁漏勿误）。"""
    cutoff = now - timedelta(days=lookback_days)
    results: list[ParsedSession] = []
    verdict: dict[str, bool] = {}  # per-cwd 缓存：同 cwd 多会话只跑一次 git 子进程
    for path in sorted(Path(projects_dir).glob("*/*.jsonl")):  # depth 2 = top-level only
        parsed = parse_session(path)
        if not parsed.cwd:
            continue
        last = parse_timestamp(parsed.last_ts)
        if last is None or last < cutoff:
            continue  # 无可解析时间戳的会话宁漏勿误：无法证明在窗口内，一律不纳入
        if since is not None and last < since:
            continue  # only 纳入 last_ts >= since 的会话
        if rules is not None:
            if parsed.cwd not in verdict:
                verdict[parsed.cwd] = classify(parsed.cwd, rules)
            if not verdict[parsed.cwd]:
                continue
        results.append(parsed)
    return results


def detect_data_start(projects_dir) -> str | None:
    """返回本机 transcript 中全局最早可解析的 timestamp（ISO 字符串）。

    遍历 projects_dir/*/*.jsonl，每个文件只读前 _HEAD_LINES 行（文件可能极大，
    绝不全读；会话起始事件通常在文件头部）。坏行 / 坏文件 / 无 timestamp 静默跳过。
    无任何可解析时间返回 None。

    用途：与窗口 since_date 对比，识别 Claude Code 默认 cleanupPeriodDays 清理导致的
    「名义窗口 vs 实际数据起点」错位（隐患 E）。
    """
    earliest: datetime | None = None
    for path in Path(projects_dir).glob("*/*.jsonl"):
        try:
            with path.open(encoding="utf-8") as f:
                for _, line in zip(range(_HEAD_LINES), f):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(rec, dict):
                        continue
                    ts = parse_timestamp(rec.get("timestamp"))
                    if ts is not None:
                        if earliest is None or ts < earliest:
                            earliest = ts
                        break  # 文件内记录按时间追加，首个可解析时间即该文件最早，无需继续读
        except OSError:
            continue
    return earliest.isoformat() if earliest is not None else None


def is_window_truncated(since_date: date | None, data_start: str | None) -> bool:
    """实际数据起点是否晚于窗口起点（即名义窗口头部数据已被本机清理）。

    两者都存在、且 data_start 的日期严格晚于 since_date 时为 True；
    其余（任一为 None / 数据起点早于或等于窗口起点）为 False。纯函数，无 IO。
    """
    if since_date is None or not data_start:
        return False
    start = parse_timestamp(data_start)
    if start is None:
        return False
    return start.date() > since_date
