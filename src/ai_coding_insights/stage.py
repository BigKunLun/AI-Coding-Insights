"""成长阶段判定（确定性查表，无 IO 纯函数）。

定位约束：这是给本人看的成长定位，不是考核分数。判定规则随结果一起返回
（criteria/gaps），渲染层必须原样展示，让人知道「为什么在这、怎么往上」。

成熟度轴（decide_stage）改为绝对值闸门：从 aggregate 取活跃天数、有效输入条数、
工具广度等硬指标的绝对量，逐档过门——根治旧口径下低用量者凭 posture 比例
虚高被抬档的问题（比例只反映「这点用量里怎么用」，不反映「用了多少」）。
深度信号（思考/子代理/Plan 任一）与高阶编排（真并行/后台/自建扩展）为合成信号。
git 落地次数为 git 主锚硬证据（见 git_outcome.py），仅作引领期闸门。
仍属软信号初筛，不得直接用于奖惩；阈值初设值待人群分位校准，整体可覆盖。

posture 分布仍由阶段一 extractor 逐 turn 语义分档计数、规则层聚合组装
（见 assemble_posture；AskUserQuestion 答题为协议硬信号计入 L2），
供后续姿势诊断轴消费，不再参与成熟度定级。
"""

from dataclasses import dataclass


def normalize_posture(posture_distribution: dict) -> dict:
    """把 posture_distribution 归一到比例形态（值均为 float，含 L1-L4 四键）。

    生产路径输入恒为 assemble_posture 的 0-1 输出（和为 1 或全零）；
    百分数分支（和 > 1.5 视为 /100）仅作直接喂 dict 调用本模块时的防御兜底。
    所有消费方（判档/渲染）都应经此单点归一，不得各自再判形态。
    """
    pd = posture_distribution or {}
    vals = {k: float(pd.get(k, 0) or 0) for k in ("L1", "L2", "L3", "L4")}
    if sum(vals.values()) > 1.5:
        vals = {k: v / 100.0 for k, v in vals.items()}
    return vals


def assemble_posture(llm_posture_counts: dict, option_pick_count) -> dict:
    """LLM 逐 turn 语义分档计数 + 协议硬信号 → L1-L4 分布（确定性算术，无 IO 纯函数）。

    v2 口径（2026-06-12）：四档分界是语义问题，由看得见原文的阶段一 extractor
    逐条判档并按会话输出 posture_counts（verify-obs 已闸「每会话四档总和 ==
    输入条数」），本函数只做算术。AskUserQuestion 已答题数是协议级硬信号
    （L2「选择」的机械事实），直接并入 L2，不经 LLM。
    分母 = 计数总和 + 答题数（与决策点数学等价，自洽分母消灭两侧错位）；
    为 0 → 全零分布（decide_stage 走探索期兜底）。非法值按 0 计，防御不抛错。
    """
    pc = llm_posture_counts or {}

    def _n(key):
        v = pc.get(key)
        return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 0

    l1, l2, l3, l4 = _n("L1"), _n("L2"), _n("L3"), _n("L4")
    try:
        picks = max(0, int(option_pick_count or 0))
    except (TypeError, ValueError):
        picks = 0
    dp = l1 + l2 + l3 + l4 + picks
    if dp <= 0:
        return {"L1": 0.0, "L2": 0.0, "L3": 0.0, "L4": 0.0}
    return {"L1": l1 / dp, "L2": (l2 + picks) / dp, "L3": l3 / dp, "L4": l4 / dp}


@dataclass(frozen=True)
class StageThresholds:
    """成熟度档位绝对闸门（初设值待人群分位校准，可整体替换覆盖）。"""
    s2_active_days: int = 5
    s2_human_input: int = 80
    s2_tool_breadth: int = 6
    s3_active_days: int = 12
    s3_human_input: int = 300
    s3_tool_breadth: int = 10
    s3_depth_signal: int = 1
    s4_active_days: int = 20
    s4_human_input: int = 800
    s4_advanced: int = 1
    s4_git_landed: int = 5
    s4_parallel_min: int = 2
    s4_background_min: int = 2
    s4_custom_min: int = 1


DEFAULT_STAGE_THRESHOLDS = StageThresholds()


def _stage_values(m: dict, t: StageThresholds) -> dict:
    """从 aggregate 提取定级用绝对值（含两个合成信号）。非数值按 0；
    意外浮点先 round 取整（不向零截断，避免 10.9 漏掉 ≥11 闸门）。"""
    def g(k):
        try:
            return int(round(float(m.get(k, 0) or 0)))
        except (TypeError, ValueError):
            return 0
    depth_signal = max(g("thinking_sessions"), g("subagent_sessions"),
                       g("plan_mode_sessions"))
    advanced = sum(1 for ok in (
        g("max_parallel_agents") >= t.s4_parallel_min,
        g("background_sessions") >= t.s4_background_min,
        g("custom_skill_count") >= t.s4_custom_min,
    ) if ok)
    return {
        "active_days": g("active_days"),
        "human_input_count": g("human_input_count"),
        "tool_breadth": g("tool_breadth"),
        "depth_signal": depth_signal,
        "advanced_orchestration": advanced,
        "git_landed_count": g("git_landed_count"),
    }


def _stages(t: StageThresholds):
    """从高到低；每档 (序号, 名称, [(判据文案, 值键, 谓词)])。谓词输入 = _stage_values 的 dict。"""
    return [
        (4, "引领期", [
            (f"活跃天数 ≥ {t.s4_active_days} 天", "active_days",
             lambda v: v["active_days"] >= t.s4_active_days),
            (f"有效输入 ≥ {t.s4_human_input} 条", "human_input_count",
             lambda v: v["human_input_count"] >= t.s4_human_input),
            (f"高阶编排信号 ≥ {t.s4_advanced} 项（真并行/后台/自建扩展）",
             "advanced_orchestration",
             lambda v: v["advanced_orchestration"] >= t.s4_advanced),
            (f"git 落地 ≥ {t.s4_git_landed} 次", "git_landed_count",
             lambda v: v["git_landed_count"] >= t.s4_git_landed),
        ]),
        (3, "精通期", [
            (f"活跃天数 ≥ {t.s3_active_days} 天", "active_days",
             lambda v: v["active_days"] >= t.s3_active_days),
            (f"有效输入 ≥ {t.s3_human_input} 条", "human_input_count",
             lambda v: v["human_input_count"] >= t.s3_human_input),
            (f"工具广度 ≥ {t.s3_tool_breadth} 种", "tool_breadth",
             lambda v: v["tool_breadth"] >= t.s3_tool_breadth),
            (f"深度信号 ≥ {t.s3_depth_signal}（思考/子代理/Plan 任一）", "depth_signal",
             lambda v: v["depth_signal"] >= t.s3_depth_signal),
        ]),
        (2, "进阶期", [
            (f"活跃天数 ≥ {t.s2_active_days} 天", "active_days",
             lambda v: v["active_days"] >= t.s2_active_days),
            (f"有效输入 ≥ {t.s2_human_input} 条", "human_input_count",
             lambda v: v["human_input_count"] >= t.s2_human_input),
            (f"工具广度 ≥ {t.s2_tool_breadth} 种", "tool_breadth",
             lambda v: v["tool_breadth"] >= t.s2_tool_breadth),
        ]),
        (1, "探索期", []),
    ]


def decide_stage(metrics: dict,
                 thresholds: StageThresholds = DEFAULT_STAGE_THRESHOLDS) -> dict:
    """绝对值闸门式成熟度定级。返回 stage/name/criteria/gaps/values；
    values 为 _stage_values 的实际值（渲染层按 key 取值，不做文案匹配）。"""
    v = _stage_values(metrics or {}, thresholds)
    stages = _stages(thresholds)
    matched = len(stages) - 1
    for i, (_n, _nm, conds) in enumerate(stages):
        if all(pred(v) for _, _, pred in conds):
            matched = i
            break
    num, name, conds = stages[matched]
    gaps = []
    if matched > 0:
        gaps = [{"desc": d, "key": k}
                for d, k, pr in stages[matched - 1][2] if not pr(v)]
    criteria = ([{"desc": d, "key": k} for d, k, _ in conds]
                or [{"desc": "未达进阶期条件（兜底档）", "key": None}])
    return {"stage": num, "name": name, "criteria": criteria,
            "gaps": gaps, "values": v}
