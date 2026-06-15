"""增量窗口决策纯函数。

v5 把取数从「固定 30 天」改为「自上次检查以来的增量窗口」。
本模块只负责纯函数决策，不碰 IO/scan。

规则：
- 首次（无上次快照）：回看 floor 天（默认 30，对齐 CC 默认保留期）。
- 距上次检查不足 floor 天（默认 30）：拒绝，数据不足以测评进步。
- 否则：回看 min(N, cap) 天，N 为距上次检查天数。
"""

from dataclasses import dataclass
from datetime import date, timedelta

WINDOW_FLOOR_DAYS = 30
WINDOW_CAP_DAYS = 45


@dataclass
class WindowDecision:
    status: str                       # "first" | "ok" | "too_soon"
    lookback_days: int                # discover 用回看天数；too_soon 时 0
    since_date: date | None           # 窗口起点 = today - lookback_days；too_soon 时 None
    until_date: date                  # = today
    last_check_date: date | None      # 上次快照日期；首次 None
    days_since_last: int | None       # N；首次 None
    message: str | None               # too_soon 提示；其余 None

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "lookback_days": self.lookback_days,
            "since_date": self.since_date.isoformat() if self.since_date else None,
            "until_date": self.until_date.isoformat(),
            "last_check_date": self.last_check_date.isoformat() if self.last_check_date else None,
            "days_since_last": self.days_since_last,
            "message": self.message,
        }


def decide_window(last_check_date: date | None, today: date,
                  floor: int = WINDOW_FLOOR_DAYS, cap: int = WINDOW_CAP_DAYS) -> WindowDecision:
    if last_check_date is None:
        # 首次回看 floor（非 cap）：CC 默认 cleanupPeriodDays=30 只物理保留 30 天 transcript，
        # 名义回看 45 天在默认环境下必然触发 truncated——制造「报告写 45、实际只有 30」的
        # 认知割裂。用 floor 与物理保留期 + 增量门槛对齐，cap 仅作增量路径回看上限。
        return WindowDecision("first", floor, today - timedelta(days=floor), today, None, None, None)
    n = (today - last_check_date).days
    if n < floor:
        return WindowDecision(
            "too_soon", 0, None, today, last_check_date, n,
            f"距上次检查仅 {n} 天，攒够 {floor} 天再来测评（数据不足以测评进步）")
    days = min(n, cap)
    return WindowDecision("ok", days, today - timedelta(days=days), today, last_check_date, n, None)
