"""阈值校准通道：把「拍脑袋的初设值」变成「有据可依的分位定位」（无 IO 纯函数）。

定位：这是开发者自己调阈值时看的手动调试命令，不进 SKILL.md 编排、不产 HTML。
它不替人改阈值——只回答两个问题：
1. 本机历史快照里，各核心指标的分布长什么样（min/p25/median/p75/max/n）；
2. 当前 `stage.py` 的 `StageThresholds` / `PostureBands` 与 `view_model.py` 的雷达
   打满线，各自落在这个分布的第几分位（0=低于所有观测，1=高于所有观测）。

诚实第一（本项目一贯取向，违反即 bug）：
- 样本不足必须出声。3 个快照给出「p75 = 12」这种看起来很确定的数字是骗人的，
  故每层（整体 / 每条阈值）都带 caveat，渲染层必须原样带出。
- 快照里根本没记的指标一律标「不可测」，绝不拿别的指标顶包充数。
- 跨口径（posture_rubric 变更）的样本，对受口径影响的键直接剔除——旧口径观测与
  新口径阈值放一起求分位是伪定位。

隐私（定位级约束）：本模块只吃 `snapshot.py` 落盘的**已脱敏标量**（`_CORE_KEYS`
白名单 + 姿态四档比例），绝不引入新数据源（不读会话原文/batch/obs/git），
输出里只有指标名与数字，不含任何项目名或路径。
"""

from .snapshot import (CURRENT_POSTURE_RUBRIC, _CALIBER_SENSITIVE_KEYS,
                       _CORE_KEYS)
from .stage import (DEFAULT_POSTURE_BANDS, DEFAULT_STAGE_THRESHOLDS,
                    PostureBands, StageThresholds, normalize_posture)
from .view_model import RADAR_BREADTH_FULL, RADAR_DEPTH_FULL_TURNS, safe_num

# 样本量分级门槛（初设值，本身也是拍的——但它只影响「说不说得准」的措辞，不影响数字）
MIN_PERCENTILE_SAMPLES = 5    # 低于此连分位形状都谈不上，只当量级参考
MIN_RELIABLE_SAMPLES = 20     # 低于此波动大，不足以据此动阈值

# 姿态派生序列键（由快照顶层 posture_distribution 算出，不在 _CORE_KEYS 里）
POSTURE_L3 = "posture_L3"
POSTURE_L4 = "posture_L4"
POSTURE_L34 = "posture_L3+L4"
_POSTURE_KEYS = (POSTURE_L3, POSTURE_L4, POSTURE_L34)

# 合成信号派生序列键：stage.py 的 `_stage_values` 用分项现算，快照只存分项，
# 这里按同一套规则回算，让 s3_depth_signal / s4_advanced 从「不可测」变可测。
DEPTH_SIGNAL = "depth_signal"
ADVANCED = "advanced_orchestration"
_DEPTH_PARTS = ("subagent_sessions", "plan_mode_sessions")
_ADVANCED_PARTS = ("max_parallel_agents", "background_sessions", "custom_skill_count")
_DERIVED_KEYS = (DEPTH_SIGNAL, ADVANCED)

# 阈值方向：分位的读法完全取决于它。下限门「≥ 才过」与上限门「> 即判超」的
# 0% 分位含义正好相反，混用一套图例会把结论带反（实测踩过：L4 稳定超上限却被
# 读成「门形同虚设」）。刻度线不是门，只控可视化拉伸。
DIR_FLOOR = "floor"       # 下限门：值 ≥ 阈值才算过
DIR_CEILING = "ceiling"   # 上限门：值 > 阈值即判超
DIR_SCALE = "scale"       # 刻度线：雷达满格，无过/不过之分
# 文案参数：只决定 reason 的措辞，对 state / 档位判定**完全无影响**（空转旋钮）。
# 单列一档不是洁癖：把空转旋钮混在真门里、用同一套「过门/未过门」图例呈现，
# 是比方向写反更难发现的错——照着调低了，重跑报告一个字不变，也没人告诉你它是空转的。
DIR_TEXT = "text"

# 受口径版本影响、跨 rubric 不可混采的序列键：git 口径三键（定义随 rubric 变）
# + 姿态三键（rubric 名字就是 posture_rubric，分档语义随之变）。
_STALE_SENSITIVE = frozenset(_CALIBER_SENSITIVE_KEYS) | frozenset(_POSTURE_KEYS)

SERIES_KEYS = tuple(_CORE_KEYS) + _DERIVED_KEYS + _POSTURE_KEYS


def percentile(values, q: float):
    """线性插值分位数（与 numpy 默认的 type-7 一致）。空样本返回 None。

    小样本边界：n=1 时任何 q 都返回该值（唯一的观测就是全部信息）；
    n=2 时在两点之间线性插值。不做任何「样本够不够」的判断，那是 sample_caveat 的事。
    """
    nums = sorted(float(v) for v in values)
    n = len(nums)
    if n == 0:
        return None
    if n == 1:
        return nums[0]
    q = min(max(float(q), 0.0), 1.0)
    pos = q * (n - 1)
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return nums[lo] + (nums[hi] - nums[lo]) * frac


def describe(values) -> dict:
    """五数概括 + 样本数 + 样本量警告。空样本各统计量为 None（不编 0）。"""
    nums = [float(v) for v in values]
    n = len(nums)
    return {
        "n": n,
        "min": min(nums) if n else None,
        "p25": percentile(nums, 0.25),
        "median": percentile(nums, 0.5),
        "p75": percentile(nums, 0.75),
        "max": max(nums) if n else None,
        "caveat": sample_caveat(n),
    }


def locate_threshold(values, threshold):
    """阈值落在观测分布的第几分位（0.0-1.0）。空样本返回 None。

    定义为中位秩比例：(严格小于阈值的个数 + 等于阈值的个数 / 2) / 总数。
    纯位置量：0.0 = 阈值低于所有观测，1.0 = 高于所有观测，0.5 = 卡在中位。
    **不含任何「过没过门」的语义**——那取决于门的方向，由 `read_percentile` 按
    direction 翻译（下限门与上限门对同一个 0.0 的解读正好相反）。
    用中位秩而非简单 `<=`，是为了避免观测全等于阈值时给出 1.0 的误导结论。
    """
    nums = [float(v) for v in values]
    n = len(nums)
    if n == 0:
        return None
    t = float(threshold)
    below = sum(1 for v in nums if v < t)
    equal = sum(1 for v in nums if v == t)
    return (below + equal / 2.0) / n


def sample_caveat(n: int):
    """样本量警告文案；样本足够时返回 None。

    这是本模块「诚实第一」的落点：宁可说测不准，不可假装测得准。
    """
    n = int(n or 0)
    if n <= 0:
        return "无样本，无法定位分位"
    if n < MIN_PERCENTILE_SAMPLES:
        return f"仅 {n} 个样本，分位数不可靠，只能当量级参考"
    if n < MIN_RELIABLE_SAMPLES:
        return (f"仅 {n} 个样本，分位数波动大不可靠，"
                f"不足以据此改阈值（建议 ≥{MIN_RELIABLE_SAMPLES}）")
    return None


_DIR_LABEL = {DIR_FLOOR: "下限门", DIR_CEILING: "上限门", DIR_SCALE: "刻度线",
              DIR_TEXT: "文案参数"}

_EXTREME = 0.05   # 分位贴到两端多近才改用「几乎全部」的定性说法


def read_percentile(direction: str, p) -> str:
    """分位 + 门方向 → 该怎么读（无 IO 纯函数）。p 为 None 返回空串（不编读法）。

    这是修「方向带反结论」的落点：同一个 0% 分位，
    - 下限门（≥ 才过）= 阈值低于所有观测 = 人人过门，门形同虚设；
    - 上限门（> 即判超）= 阈值低于所有观测 = 每个窗口都超限、次次触发判定；
    - 刻度线 = 满格低于所有观测 = 轴被截顶。
    三种读法各自成立，混用会把「该调高」读成「该调低」。
    第四类 DIR_TEXT 是空转旋钮：分位仍是有效的位置信息，但**不能给任何过门读法**——
    它压根不参与判定，说「该调高/该调低」等于诱导人做一次不产生任何变化的改动。
    """
    if p is None:
        return ""
    p = float(p)
    if direction == DIR_TEXT:
        return f"约 {p:.0%} 的观测低于它；此参数只决定 reason 措辞，改它不改变判定结果"
    if direction == DIR_CEILING:
        if p <= _EXTREME:
            return "几乎所有观测都超过上限（次次判超，多半该调高或重审这条带）"
        if p >= 1 - _EXTREME:
            return "几乎没有观测触及上限（观测基本都在限内，这条带没起作用）"
        return f"约 {1 - p:.0%} 的观测超过上限"
    if direction == DIR_SCALE:
        if p <= _EXTREME:
            return "满格设低了，观测普遍高于它、轴长期被截顶"
        if p >= 1 - _EXTREME:
            return "满格设高了，观测长期贴不到满格、图形被压扁"
        return f"约 {1 - p:.0%} 的观测达到或超过满格"
    if p <= _EXTREME:
        return "几乎所有观测都过门（门形同虚设）"
    if p >= 1 - _EXTREME:
        return "几乎没有观测过得去（门过高）"
    return f"约 {p:.0%} 的观测在门下（未过门）"


def _num(v):
    """标量取数：None / bool / 字符串一律不算数值样本（bool 是 int 子类，必须先挡）。

    非有限值（NaN / ±Inf）与超出 float 的巨型整数同样不算样本——JSON 允许
    `Infinity`/`NaN` 字面量，整数也无上限，人手改过或半写坏的快照就能带进来。
    放行它们的下场不是「少一条样本」而是整次校准崩掉：`round(inf)` / `int(nan)` 直接抛。
    有限性判定复用 `view_model.safe_num`（同一条纪律不各写一套）；但**不接受数字字符串**，
    与 safe_num 在这一点上刻意分歧：报告渲染宁可把 "14" 读出来给用户看，
    而校准是拿来动阈值的，来路不明的字符串不该混进分布。
    """
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    n = safe_num(v)
    return None if n is None else float(n)


def _is_stale(snapshot: dict) -> bool:
    """该快照是否处于旧口径（rubric 缺失按旧口径处理：来路不明不混采）。"""
    return snapshot.get("posture_rubric") != CURRENT_POSTURE_RUBRIC


def _parts(metrics: dict, keys) -> dict | None:
    """取一组分项标量；任一缺失/非数值 → None（整条派生观测跳过）。

    「缺一就跳过」不是洁癖：老快照根本没记这些分项，补 0 会编出「没有高阶编排」
    这种假观测，把分布整体往下拽——与 diff 侧「缺失不当 0」是同一条纪律。
    """
    out = {}
    for k in keys:
        v = _num(metrics.get(k))
        if v is None:
            return None
        out[k] = v
    return out


def extract_series(snapshots,
                   thresholds: StageThresholds = DEFAULT_STAGE_THRESHOLDS) -> dict:
    """一组快照 → {序列键: [观测值]}（无 IO 纯函数）。

    只取 `_CORE_KEYS` 白名单标量、两条合成信号派生序列与姿态派生三键，白名单外的
    键一律不进——这同时是隐私闸：新数据源想溜进校准通道必须先过这份白名单。
    姿态四档和 ≈ 0 视为「没有观测」，整条跳过而非计成 0.0（计 0 会把分布往下拽）。
    合成信号（depth_signal / advanced_orchestration）按 `stage.py` 同款规则从分项
    回算，分项不全的快照整条跳过；`advanced` 依赖分项自身的门，故随 thresholds 变动。
    旧口径快照对 `_STALE_SENSITIVE` 的键直接剔除（伪定位防线）。
    """
    series: dict[str, list] = {k: [] for k in SERIES_KEYS}
    for snap in snapshots or []:
        if not isinstance(snap, dict):
            continue
        stale = _is_stale(snap)
        metrics = snap.get("metrics")
        metrics = metrics if isinstance(metrics, dict) else {}
        for k in _CORE_KEYS:
            if stale and k in _STALE_SENSITIVE:
                continue
            v = _num(metrics.get(k))
            if v is not None:
                series[k].append(v)
        depth = _parts(metrics, _DEPTH_PARTS)
        if depth is not None:
            series[DEPTH_SIGNAL].append(max(depth.values()))
        adv = _parts(metrics, _ADVANCED_PARTS)
        if adv is not None:
            series[ADVANCED].append(float(sum(1 for ok in (
                adv["max_parallel_agents"] >= thresholds.s4_parallel_min,
                adv["background_sessions"] >= thresholds.s4_background_min,
                adv["custom_skill_count"] >= thresholds.s4_custom_min,
            ) if ok)))
        if stale:
            continue
        pd = snap.get("posture_distribution")
        if not isinstance(pd, dict):
            continue
        # 逐档先过 _num 再交给 normalize_posture：后者内部是裸 float()，
        # 一个脏档（字符串 / NaN / Inf）就抛 ValueError/OverflowError 炸掉整次校准。
        # 脏档不补 0——半信半疑的分布比没有分布更坏，整条快照的姿态观测直接跳过。
        clean = {k: _num(pd.get(k, 0) or 0) for k in ("L1", "L2", "L3", "L4")}
        if any(v is None for v in clean.values()):
            continue
        norm = normalize_posture(clean)
        if sum(norm.values()) < 1e-9:
            continue    # 全零 = 无姿态观测，不是「L3 占比 0」
        series[POSTURE_L3].append(norm["L3"])
        series[POSTURE_L4].append(norm["L4"])
        series[POSTURE_L34].append(norm["L3"] + norm["L4"])
    return series


def _spec(group, name, value, metric, note=None, direction=DIR_FLOOR) -> dict:
    """阈值条目：metric=None 表示结构性不可测（快照根本没记这个指标），note 说明原因。

    direction 必填语义（默认下限门，因为绝大多数闸门是「≥ 才过」）：写错方向比
    不写更危险——分位读法会整个反过来，见 DIR_* 常量注释。
    """
    return {"group": group, "name": name, "value": value,
            "metric": metric, "note": note, "direction": direction}


_STAGE_GROUP = "成熟度档位闸门"
_POSTURE_GROUP = "姿态健康带"
_RADAR_GROUP = "雷达打满线"


def threshold_specs(thresholds: StageThresholds = DEFAULT_STAGE_THRESHOLDS,
                    bands: PostureBands = DEFAULT_POSTURE_BANDS) -> list:
    """当前全部待校准阈值 → 各自该对哪条观测序列 + 门的方向（无 IO 纯函数）。

    `StageThresholds` / `PostureBands` 的每个字段都必须在这份清单里出现，
    对不上快照序列的就明写 metric=None + 原因——漏项等于悄悄放弃校准。
    方向逐条对着 `stage.py` 的判定式定：`decide_stage` 的闸门全是 `>=`（下限门）；
    `diagnose_posture` 里只有 `l4 > l4_healthy_ceiling` 是上限门，决策点样本门、
    引导力下限、Plan 次数门是「达到才算」的下限门；而 `l3_healthy_floor` /
    `l4_healthy_floor` 走过 ceiling 与 guide_floor 之后两条分支都 `return 健康`，
    对 state 毫无影响，只挑 reason 措辞 —— 故标 DIR_TEXT 单列，不混进真门的图例。
    （这条耦合由 tests/test_calibrate.py 的
    `test_stage_的两条健康下沿对判定完全无影响` 钉住：哪天让它们真的分档，那条会红。）
    """
    t, b = thresholds, bands
    return [
        _spec(_STAGE_GROUP, "s2_active_days", t.s2_active_days, "active_days"),
        _spec(_STAGE_GROUP, "s2_human_input", t.s2_human_input, "human_input_count"),
        _spec(_STAGE_GROUP, "s2_tool_breadth", t.s2_tool_breadth, "tool_breadth"),
        _spec(_STAGE_GROUP, "s3_active_days", t.s3_active_days, "active_days"),
        _spec(_STAGE_GROUP, "s3_human_input", t.s3_human_input, "human_input_count"),
        _spec(_STAGE_GROUP, "s3_tool_breadth", t.s3_tool_breadth, "tool_breadth"),
        _spec(_STAGE_GROUP, "s3_depth_signal", t.s3_depth_signal, DEPTH_SIGNAL,
              "派生序列：按 stage 同款取 subagent/plan 较大者，两键齐备的快照才计入"),
        _spec(_STAGE_GROUP, "s4_active_days", t.s4_active_days, "active_days"),
        _spec(_STAGE_GROUP, "s4_human_input", t.s4_human_input, "human_input_count"),
        _spec(_STAGE_GROUP, "s4_advanced", t.s4_advanced, ADVANCED,
              "派生序列：三个分项各自过自己的门算 1 项，故随分项阈值一起变动"),
        _spec(_STAGE_GROUP, "s4_git_landed", t.s4_git_landed, "git_landed_count"),
        _spec(_STAGE_GROUP, "s4_parallel_min", t.s4_parallel_min, "max_parallel_agents"),
        _spec(_STAGE_GROUP, "s4_background_min", t.s4_background_min,
              "background_sessions"),
        _spec(_STAGE_GROUP, "s4_custom_min", t.s4_custom_min, "custom_skill_count"),
        _spec(_POSTURE_GROUP, "min_decision_points", b.min_decision_points,
              "decision_point_count", "样本门：低于它整档不判姿态"),
        _spec(_POSTURE_GROUP, "l3_healthy_floor", b.l3_healthy_floor, POSTURE_L3,
              "空转旋钮：只决定 diagnose_posture 的 reason 措辞，不改变 state——"
              "调它重跑报告判定一个字不变（两条分支都返回「健康」）", DIR_TEXT),
        _spec(_POSTURE_GROUP, "l4_healthy_floor", b.l4_healthy_floor, POSTURE_L4,
              "空转旋钮：同 l3_healthy_floor，只决定 reason 措辞，不改变 state", DIR_TEXT),
        _spec(_POSTURE_GROUP, "l4_healthy_ceiling", b.l4_healthy_ceiling, POSTURE_L4,
              "唯一的上限门：L4 占比超过它即判「偏对抗」", DIR_CEILING),
        _spec(_POSTURE_GROUP, "guide_floor", b.guide_floor, POSTURE_L34),
        _spec(_POSTURE_GROUP, "min_handsoff_plan_sessions",
              b.min_handsoff_plan_sessions, "plan_mode_sessions"),
        _spec(_RADAR_GROUP, "RADAR_BREADTH_FULL", RADAR_BREADTH_FULL, "tool_breadth",
              None, DIR_SCALE),
        _spec(_RADAR_GROUP, "RADAR_DEPTH_FULL_TURNS", RADAR_DEPTH_FULL_TURNS, "turn_p90",
              None, DIR_SCALE),
    ]


def _span(snapshots) -> dict:
    """快照日期跨度（只出日期，不出文件路径）。"""
    dates = sorted(str(s.get("generated_at") or "")[:10]
                   for s in snapshots if isinstance(s, dict) and s.get("generated_at"))
    dates = [d for d in dates if d]
    return {"first": dates[0] if dates else None,
            "last": dates[-1] if dates else None}


def calibrate(snapshots,
              thresholds: StageThresholds = DEFAULT_STAGE_THRESHOLDS,
              bands: PostureBands = DEFAULT_POSTURE_BANDS) -> dict:
    """一组历史快照 + 当前阈值组 → 分布 + 每个阈值的分位定位（无 IO 纯函数）。

    返回 dict（即 `--json` 的结构）：
    - sample_count / span / stale_rubric_count / reliable / caveat：样本面貌与可信度
    - distributions: {序列键: describe(...)}
    - thresholds: [{group,name,value,metric,note,direction,measurable,n,percentile,caveat}]
      percentile 为 None 有两种情况：结构性不可测（measurable=False），或该键无样本
      （n=0）。两种都不给数字——不编。direction 决定分位怎么读（见 read_percentile）。
    """
    snaps = [s for s in (snapshots or []) if isinstance(s, dict)]
    series = extract_series(snaps, thresholds)
    n = len(snaps)
    rows = []
    for spec in threshold_specs(thresholds, bands):
        metric = spec["metric"]
        values = series.get(metric, []) if metric else []
        rows.append({
            **spec,
            "measurable": metric is not None,
            "n": len(values),
            "percentile": locate_threshold(values, spec["value"]) if metric else None,
            "caveat": sample_caveat(len(values)) if metric else None,
        })
    return {
        "sample_count": n,
        "span": _span(snaps),
        "stale_rubric_count": sum(1 for s in snaps if _is_stale(s)),
        "reliable": n >= MIN_RELIABLE_SAMPLES,
        "caveat": sample_caveat(n),
        "distributions": {k: describe(v) for k, v in series.items()},
        "thresholds": rows,
    }


def _fmt(v) -> str:
    """数值 → 紧凑显示；取不到数（None / 非有限 / 超大整数 / 非数值）一律 —（不补 0）。

    渲染是最后一步，崩在这里等于前面全部作废：`round(inf)` / `round(nan)` 会抛，
    `float(10**400)` 也会抛。这里兜住，脏值退化成一个「—」而不是一次崩溃。
    """
    f = safe_num(v) if not isinstance(v, bool) else None
    if f is None:
        return "—"
    if abs(f - round(f)) < 1e-9 and abs(f) < 1e6:
        return str(int(round(f)))
    return f"{f:.3f}".rstrip("0").rstrip(".")


def _pad(s: str, width: int) -> str:
    """左对齐补空格，中日韩字符按 2 列宽计——否则含中文的列在等宽终端里会错位。"""
    w = sum(2 if ord(c) > 0x2E7F else 1 for c in str(s))
    return str(s) + " " * max(0, width - w)


def _dist_marker(d: dict) -> str:
    """分布表行尾的样本量标记（无警告 → 空串）。

    修的是「整体达标掩盖单序列 n=1」：页头只说总快照数，某个指标可能只有 1 个观测，
    而它多半在阈值表里也没有对应行（那层的 ⚠ 兜不住）→ 整屏一个警告都不出现，
    看的人就会拿 1 个观测当分布用。故每行自带出声。
    """
    if not d.get("caveat"):
        return ""
    n = int(d.get("n") or 0)
    return "  ⚠ 无样本" if n == 0 else f"  ⚠ 不可靠（样本 {n}）"


def format_report(result: dict) -> str:
    """校准结果 → 人类可读文本（无 IO 纯函数）。

    只输出指标名与数字，不含任何路径/项目名——隐私闸在这里也守一道。
    """
    lines = []
    n = result.get("sample_count", 0)
    span = result.get("span") or {}
    span_txt = (f"（{span.get('first')} ~ {span.get('last')}）"
                if span.get("first") else "")
    lines.append(f"样本：{n} 个快照{span_txt}")
    stale = result.get("stale_rubric_count") or 0
    if stale:
        lines.append(f"其中 {stale} 个为旧口径快照，受口径影响的指标已从样本中剔除")
    if result.get("caveat"):
        lines.append(f"⚠ {result['caveat']}")
    else:
        lines.append("样本量达标，分位可作为调阈值的依据（仍是本机单人分布，不代表人群）")

    lines.append("")
    lines.append("== 指标分布 ==")
    lines.append(f"{_pad('指标', 24)}{'n':>4}{'min':>10}{'p25':>10}"
                 f"{'median':>10}{'p75':>10}{'max':>10}")
    for key, d in (result.get("distributions") or {}).items():
        lines.append(
            f"{_pad(key, 24)}{d['n']:>4}{_fmt(d['min']):>10}{_fmt(d['p25']):>10}"
            f"{_fmt(d['median']):>10}{_fmt(d['p75']):>10}{_fmt(d['max']):>10}"
            f"{_dist_marker(d)}")

    lines.append("")
    lines.append("== 当前阈值定位 ==")
    # 图例必须分方向：一套「0% = 人人过门」的读法套到上限门上会把结论整个带反。
    lines.append("（分位 = 阈值在本机历史观测里的位置，读法随门的方向而不同：）")
    lines.append("  下限门（≥ 才过）：0% = 人人过门/形同虚设，100% = 无人过门")
    lines.append("  上限门（> 即判超）：0% = 观测次次超限/次次触发，100% = 从未触及")
    lines.append("  刻度线（雷达满格）：0% = 观测被截顶，100% = 长期贴不到满格")
    lines.append("  文案参数：不参与任何判定，分位仅供参考——调它不会改变报告结论")
    group = None
    for row in result.get("thresholds") or []:
        if row["group"] != group:
            group = row["group"]
            lines.append(f"[{group}]")
        head = f"  {row['name']:<28} = {_fmt(row['value']):<8}"
        if not row["measurable"]:
            lines.append(f"{head}→ 不可测：{row['note']}")
            continue
        if row["percentile"] is None:
            lines.append(f"{head}→ 不可测：{row['metric']} 在现有快照里无样本")
            continue
        direction = row.get("direction", DIR_FLOOR)
        tail = (f"→ {row['metric']} 第 {row['percentile']:.0%} 分位（n={row['n']}）"
                f"｜{_DIR_LABEL.get(direction, direction)}："
                f"{read_percentile(direction, row['percentile'])}")
        if row.get("caveat"):
            # 逐条只挂短标记，完整措辞在页头说一次——但绝不省略：每行都得看得见「不可靠」
            tail += f"  ⚠ 不可靠（样本 {row['n']}）"
        if row.get("note"):
            tail += f"  注：{row['note']}"
        lines.append(head + tail)
    return "\n".join(lines) + "\n"
