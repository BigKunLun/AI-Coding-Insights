from datetime import datetime, timedelta, date
from pathlib import Path
from .models import RemoteRule, ParsedSession
from .repo_identity import classify
from .sources import CLAUDE_CODE, Source, get_source
from .timeutil import parse_timestamp


def _as_source(source) -> Source:
    """把 None / 来源名 / Source 对象统一成 Source。

    None 兜底到 claude-code 而不是 detect_source()：本函数是取数入口，
    来源必须由调用方（cli）显式决断并写进报告标注，不能在最底层偷偷跟环境变。
    """
    if source is None:
        return get_source(CLAUDE_CODE)
    return source if isinstance(source, Source) else get_source(source)


def discover_sessions(projects_dir, rules: list[RemoteRule] | None,
                              lookback_days: int, now: datetime,
                              since: datetime | None = None,
                              source=None) -> list[ParsedSession]:
    """rules=None 即 mode="all"：全部纳入，不跑 git 归属判定（省子进程）。
    rules 为列表即 mode="include"：仅纳入 remote 命中的项目（宁漏勿误）。

    *source*：`sources.Source` 或来源名，缺省 claude-code。窗口/归属/时间戳三条
    过滤规则**对所有来源同口径**——跨 harness 的可比性全靠这里不分叉。
    """
    # cutoff 对齐当天 00:00，与报告标注的 since_date（整天口径）一致——否则按 now 的
    # 时刻算会把 since_date 当天早于运行时刻的会话误排除，边界会话归属随运行时刻漂移。
    cutoff = (now - timedelta(days=lookback_days)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    results: list[ParsedSession] = []
    verdict: dict[str, bool] = {}  # per-cwd 缓存：同 cwd 多会话只跑一次 git 子进程
    for parsed in _as_source(source).iter_sessions(Path(projects_dir)):
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


def detect_data_start(projects_dir, source=None) -> str | None:
    """返回该来源数据中全局最早可解析的 timestamp（ISO 字符串）。

    实现委托给来源自己的 `earliest_ts`（各家记录形状不同，时间戳键名也不同）；
    文件型来源统一走 `sources.head_earliest_ts`：每个文件只读前几十行（文件可能
    极大，绝不全读）。坏行 / 坏文件 / 无 timestamp 静默跳过，全无则 None。

    用途：与窗口 since_date 对比，识别 harness 自身清理策略（如 CC 默认
    cleanupPeriodDays=30）导致的「名义窗口 vs 实际数据起点」错位（隐患 E）。
    """
    return _as_source(source).earliest_ts(Path(projects_dir))


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
