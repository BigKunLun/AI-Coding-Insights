import json
import math
import re
from pathlib import Path

DEFAULT_SNAPSHOT_DIR = Path.home() / ".ai-coding-insights" / "snapshots"

# 快照白名单：只收**纯标量**（int/float/None）。白名单语义不可退回黑名单——黑名单式
# 会让 aggregate 新增的 dict 字段与含项目名的 project_breakdown 静默泄入快照（隐私）。
# 新增键必须同时满足：① 是标量，② 不含任何项目名/路径/业务文本。
# 后半段是 2026-07 补入的定级/诊断口径标量（原来快照没记，导致对应阈值在 calibrate
# 里结构性不可测）；旧快照没有这些键，diff_metrics 的 `prev is None → no_base`
# 守卫负责不把「缺失」当 0 算涨幅。
_CORE_KEYS = ["landed_ratio", "commit_count", "landed_count",
              "git_landed_count", "git_commit_total", "dropped_count", "edit_count",
              "session_count", "human_input_count", "tool_breadth", "active_days",
              "token_total", "subagent_sessions", "workflow_sessions", "mcp_sessions",
              "duration_median_min",
              "decision_point_count", "plan_mode_sessions", "turn_p90",
              "custom_skill_count", "background_sessions", "max_parallel_agents"]

_DATE_STEM = re.compile(r"^\d{4}-\d{2}-\d{2}$")  # 快照文件名仅认 YYYY-MM-DD，杂散 json 不参与排序

# 姿势/指标口径版本（3=双轴评级+健康带，2026-06-15 起）。除姿势分布外，landed_ratio
# 等 git 指标的定义也随版本变（v3 起 landed_ratio = 文件重叠落地 ÷ 窗口本人提交总数）。
# diff_metrics 据此识别跨口径边界，对受口径影响的 key 不出同比（无可比基线）。
CURRENT_POSTURE_RUBRIC = 3

# 跨口径不可同比的 metrics key：定义随 rubric 变更，旧口径 prev 与新口径 now 求 delta 是伪涨跌。
_CALIBER_SENSITIVE_KEYS = frozenset({"landed_ratio", "git_landed_count", "git_commit_total"})


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
        "posture_rubric": CURRENT_POSTURE_RUBRIC,   # 姿势/指标口径版本（见常量注释）。
                                # diff_metrics 据此对跨口径边界的受影响 key 不出同比（防伪涨跌）。
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


def load_all(dir: Path = DEFAULT_SNAPSHOT_DIR) -> list[dict]:
    """返回目录下全部合法快照（按日期升序），无则空列表。

    与 load_latest 共用同一套「文件名认 YYYY-MM-DD、损坏即跳过」的规则：
    杂散 json 不算样本，单个文件损坏不阻断其余样本（校准是统计用途，缺一条无妨）。
    """
    if not dir.exists():
        return []
    out: list[dict] = []
    for stem in sorted(p.stem for p in dir.glob("*.json") if _DATE_STEM.match(p.stem)):
        try:
            loaded = json.loads((dir / f"{stem}.json").read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(loaded, dict):
            out.append(loaded)
    return out


def _comparable(v) -> bool:
    """v 是否可安全参与减法比较（有限实数、非 bool）。

    快照是磁盘上的 JSON，人手改过或半写坏就能带进字符串 / NaN / Inf / 巨型整数，
    bool 又是 int 子类（True - 1 == 0，静默出一条假同比）。这里只做类型守卫、
    不做转换：不可比的值走 no_base，now/prev 仍原样透出交给渲染层降级显示。

    与 view_model.safe_num 的差别是刻意的——那边要把 "14" 读出来给用户看，
    这边是拿来求差算涨跌的，来路不明的字符串不该产出一个箭头。
    """
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    try:
        return math.isfinite(v)
    except OverflowError:   # 大到超出 float 的整数（JSON 整数无上限）
        return False


def diff_metrics(current: dict, previous: dict | None,
                 prev_rubric: int | None = None) -> dict:
    """计算 current 相对 previous 的增量同比。

    current/previous 是指标 dict（含 _CORE_KEYS 各键的数值；调用方负责构造）。
    prev_rubric 为基线快照的口径版本（None=未知/旧格式快照，按口径一致处理）。
    - previous 为 None **或不是对象** → 返回 {"baseline": True}（无可比基线）
    - 否则对每个 _CORE_KEYS 的 k：
        - 基线键缺失或为 None（如上次是空 metrics 脏快照），或当前键为 None →
          标 no_base，不出假箭头（delta/arrow 均 None），根治 now-0 的满值假上涨。
        - 任一侧的值不可比（字符串 / bool / NaN / Inf / 巨型整数，见 _comparable）→
          同样 no_base。本函数在 render-profile 里跑在**渲染之前**，渲染层的降级
          兜不住它：这里一崩就整张报告拿不到，前面所有 subagent 的工作作废。
        - 跨口径边界（prev_rubric 已知且 != 当前）且 k 受口径影响（_CALIBER_SENSITIVE_KEYS）→
          标 no_base，不出 delta/箭头：旧口径 prev 与新口径 now 求差是伪涨跌（如 landed_ratio
          换了分母）。
        - 两边都有值 → 给出 now / prev / delta / arrow。
    """
    if not isinstance(previous, dict):
        return {"baseline": True}
    cur = current if isinstance(current, dict) else {}
    caliber_changed = prev_rubric is not None and prev_rubric != CURRENT_POSTURE_RUBRIC
    result: dict = {}
    for k in _CORE_KEYS:
        now = cur.get(k)
        prev = previous.get(k)
        if (not _comparable(now) or not _comparable(prev)
                or (caliber_changed and k in _CALIBER_SENSITIVE_KEYS)):
            # 缺失/空基线、值不可比、或跨口径受影响 key → 不出假箭头
            result[k] = {"now": now, "prev": prev, "delta": None,
                         "arrow": None, "no_base": True}
            continue
        delta = now - prev
        arrow = "↑" if delta > 0 else ("↓" if delta < 0 else "→")
        result[k] = {"now": now, "prev": prev, "delta": delta, "arrow": arrow}
    return result
