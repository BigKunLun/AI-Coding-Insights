import json
import re
from pathlib import Path

DEFAULT_SNAPSHOT_DIR = Path.home() / ".ai-coding-insights" / "snapshots"

_CORE_KEYS = ["landed_ratio", "commit_count", "landed_count", "edit_count",
              "session_count", "human_input_count", "tool_breadth", "active_days",
              "token_total", "subagent_sessions", "workflow_sessions", "mcp_sessions",
              "duration_median_min"]

_DATE_STEM = re.compile(r"^\d{4}-\d{2}-\d{2}$")  # 快照文件名仅认 YYYY-MM-DD，杂散 json 不参与排序


def save_snapshot(metrics: dict, posture: dict, outcome: dict, generated_at: str,
                  window: dict, dir: Path = DEFAULT_SNAPSHOT_DIR) -> Path:
    """把一次报告的脱敏指标+四维分落盘到 dir/<YYYY-MM-DD>.json。返回写入的 Path。

    文件名取 generated_at 的日期部分（generated_at[:10]）。只存传入的脱敏指标与
    四维分，函数本身不做任何业务文本处理（调用方保证已脱敏）。
    """
    dir.mkdir(parents=True, exist_ok=True)
    path = dir / f"{generated_at[:10]}.json"
    payload = {
        "generated_at": generated_at,
        "window": window,
        "metrics": metrics,
        "posture_distribution": posture,
        "outcome": outcome,
    }
    # 临时文件 + 原子替换：写一半被打断不会留下截断 json 毁掉下次基线
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return path


def load_latest(before: str | None = None, dir: Path = DEFAULT_SNAPSHOT_DIR) -> dict | None:
    """返回最近一次快照内容（dict），无则 None。

    按文件名（YYYY-MM-DD，字典序==时间序）排序。before 给定时只取 stem 严格小于
    before 的最大者。目录不存在 / 无合法日期名 json / 最新快照损坏不可解析：返回 None
    （损坏按无基线降级，不阻断本次报告）。
    """
    if not dir.exists():
        return None
    stems = sorted(p.stem for p in dir.glob("*.json") if _DATE_STEM.match(p.stem))
    if before is not None:
        stems = [s for s in stems if s < before]
    if not stems:
        return None
    path = dir / f"{stems[-1]}.json"
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return loaded if isinstance(loaded, dict) else None


def diff_metrics(current: dict, previous: dict | None) -> dict:
    """计算 current 相对 previous 的增量同比。

    current/previous 是指标 dict（含 _CORE_KEYS 各键的数值；调用方负责构造）。
    - previous 为 None → 返回 {"baseline": True}（整体首次，无可比基线）
    - 否则对每个 _CORE_KEYS 的 k：
        - 基线键缺失或为 None（如上次是空 metrics 脏快照），或当前键为 None →
          标 no_base，不出假箭头（delta/arrow 均 None），根治 now-0 的满值假上涨。
        - 两边都有值 → 给出 now / prev / delta / arrow。
    """
    if previous is None:
        return {"baseline": True}
    result: dict = {}
    for k in _CORE_KEYS:
        now = current.get(k)
        prev = previous.get(k)
        if prev is None or now is None:            # 缺失/空基线 → 不出假箭头
            result[k] = {"now": now, "prev": prev, "delta": None,
                         "arrow": None, "no_base": True}
            continue
        delta = now - prev
        arrow = "↑" if delta > 0 else ("↓" if delta < 0 else "→")
        result[k] = {"now": now, "prev": prev, "delta": delta, "arrow": arrow}
    return result
