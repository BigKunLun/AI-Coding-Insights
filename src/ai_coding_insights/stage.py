"""成长阶段判定（确定性查表，无 IO 纯函数）。

定位约束：这是给本人看的成长定位，不是考核分数。判定规则随结果一起返回
（criteria/gaps），渲染层必须原样展示，让人知道「为什么在这、怎么往上」。
posture 分布由规则层组装（L1/L2 硬信号，L3/L4 分界来自 LLM 的 l4_share，
见 assemble_posture），tool_breadth/landed_ratio 来自硬指标——本判定含
LLM 软信号成分，仍属软信号初筛，不得用于奖惩。

阈值依据（2026-06-11 调研，docs/调研-AI驾驭力分级业界基准-*.md）：业界没有
「个体 AI 驾驭水平」的可计算分档基准（主流框架全是组织级或定性自评），阈值
无外部分位可抄。首版按「引领期=窄口筛头部」设定——真实人群中高主导行为
自然占比仅 8-16%（Anthropic Economic/Fluency Index）。待攒 2-3 个月度真实
分布后按本机人群分位重定四档。
"""

# 阶段从高到低逐档匹配；每档: (序号, 名称, 条件列表[(描述, 值键, 谓词)])
# 谓词输入: l4, l34(=L3+L4), tb(tool_breadth), lr(landed_ratio)
# 值键对应返回值 values 字典的键——渲染层据此取「实际值」，判据文案随便改不会断渲染
_STAGES = [
    (4, "引领期", [
        ("L4 主导占比 ≥ 35%",  "L4",           lambda l4, l34, tb, lr: l4 >= 0.35),
        ("L3+L4 合计 ≥ 70%",   "L3+L4",        lambda l4, l34, tb, lr: l34 >= 0.70),
        ("工具广度 ≥ 15 种",    "tool_breadth", lambda l4, l34, tb, lr: tb >= 15),
        ("提交落地率 ≥ 50%",    "landed_ratio", lambda l4, l34, tb, lr: lr >= 0.50),
    ]),
    (3, "精通期", [
        ("L3+L4 合计 ≥ 55%",   "L3+L4",        lambda l4, l34, tb, lr: l34 >= 0.55),
        ("工具广度 ≥ 10 种",    "tool_breadth", lambda l4, l34, tb, lr: tb >= 10),
    ]),
    (2, "进阶期", [
        ("L3+L4 合计 ≥ 35%（开始主动引导）", "L3+L4", lambda l4, l34, tb, lr: l34 >= 0.35),
        ("工具广度 ≥ 6 种",     "tool_breadth", lambda l4, l34, tb, lr: tb >= 6),
    ]),
    (1, "探索期", []),               # 兜底
]


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


def assemble_posture(decision_point_count: int, short_turn_count: int,
                     option_pick_count: int, l4_share) -> dict:
    """硬信号 + LLM 分界 → L1-L4 分布（确定性组装，无 IO 纯函数）。

    分母 = 决策点（有效真人输入 + AskUserQuestion 已答题数）。
    L1 = 极短输入占比、L2 = 选项回答占比——全硬算；剩余质量按 l4_share
    （L4 在 L3+L4 中的份额，LLM 唯一输出）切成 L3/L4。
    决策点为 0 → 全零分布（decide_stage 走探索期兜底）。
    short ⊆ turns、picks 与 turns 不相交，数学上 L1+L2 ≤ 1；仍防御钳位。
    """
    dp = int(decision_point_count or 0)
    if dp <= 0:
        return {"L1": 0.0, "L2": 0.0, "L3": 0.0, "L4": 0.0}
    l1 = max(0.0, min(1.0, (short_turn_count or 0) / dp))
    l2 = max(0.0, min(1.0, (option_pick_count or 0) / dp))
    rest = max(0.0, 1.0 - l1 - l2)
    try:
        share = max(0.0, min(1.0, float(l4_share or 0)))
    except (TypeError, ValueError):
        share = 0.0
    l4 = round(rest * share, 10)
    return {"L1": l1, "L2": l2, "L3": round(rest - l4, 10), "L4": l4}


def decide_stage(posture_distribution: dict, tool_breadth: int, landed_ratio: float) -> dict:
    """返回 dict，键含：
    - stage: 1-4
    - name: 阶段名
    - criteria: 本档判定依据，每项 {"desc": 文案, "key": values 的值键}（兜底档 key 为 None）
    - gaps: 距上一档未满足项（结构同 criteria）
    - values: 归一化后的实际值 {"L4", "L3+L4", "tool_breadth", "landed_ratio"}，
      渲染层按判据的 key 在此取「你的实际值」，不做文案匹配。
    """
    pd = normalize_posture(posture_distribution)
    # 统一 round 抑制浮点误差（如 0.08+0.47=0.5499999… → 0.55），避免恰好达标者被降档。
    l4 = round(pd["L4"], 6)
    l34 = round(pd["L4"] + pd["L3"], 6)
    tb = int(tool_breadth or 0)
    lr = round(float(landed_ratio or 0), 6)
    args = (l4, l34, tb, lr)

    matched_idx = len(_STAGES) - 1
    for i, (_num, _name, conds) in enumerate(_STAGES):
        if all(pred(*args) for _, _, pred in conds):
            matched_idx = i
            break
    num, name, conds = _STAGES[matched_idx]
    gaps = []
    if matched_idx > 0:                       # 有上一档可冲
        next_conds = _STAGES[matched_idx - 1][2]
        gaps = [{"desc": desc, "key": key}
                for desc, key, pred in next_conds if not pred(*args)]
    criteria = ([{"desc": desc, "key": key} for desc, key, _ in conds]
                or [{"desc": "未达进阶期条件（兜底档）", "key": None}])
    return {"stage": num, "name": name, "criteria": criteria, "gaps": gaps,
            "values": {"L4": l4, "L3+L4": l34, "tool_breadth": tb, "landed_ratio": lr}}
