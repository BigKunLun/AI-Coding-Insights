"""成长阶段判定（确定性查表，无 IO 纯函数）。

定位约束：这是给本人看的成长定位，不是考核分数。判定规则随结果一起返回
（criteria/gaps），渲染层必须原样展示，让人知道「为什么在这、怎么往上」。
posture 分布由阶段一 extractor 逐 turn 语义分档计数、规则层聚合组装
（见 assemble_posture；AskUserQuestion 答题为协议硬信号计入 L2），
tool_breadth/landed_ratio 来自硬指标——本判定含
LLM 软信号成分，仍属软信号初筛，不得用于奖惩。
landed_ratio 自 2026-06-12 起为 git 主锚口径：git 落地/（落地+观测丢弃），
见 models.AggregateMetrics.landed_ratio；transcript 不可观测环境不再恒 0。

阈值依据（v2 口径，2026-06-12 重校初设）：v1 口径把全部非短非选项输入默认为
L3+L4，35/55/70% 是按该虚高分布定的。v2 逐 turn 语义分档后，放行/选择被
LLM 归位到 L1/L2，L3+L4 为真实语义占比——按业界锚点（Anthropic Economic/
Fluency Index：真实人群低主导交互约 80%，高主导行为人群占比 8-16%）将各档
阈值约折半初设（引领期仍取「窄口筛头部」）。这是无本机分布数据时的折算值，
待攒 2-3 个月度 v2 真实分布后按本机人群分位重定（快照已带 posture_rubric=2
口径标记，跨口径不可同比）。
"""

# 阶段从高到低逐档匹配；每档: (序号, 名称, 条件列表[(描述, 值键, 谓词)])
# 谓词输入: l4, l34(=L3+L4), tb(tool_breadth), lr(landed_ratio)
# 值键对应返回值 values 字典的键——渲染层据此取「实际值」，判据文案随便改不会断渲染
_STAGES = [
    (4, "引领期", [
        ("L4 主导占比 ≥ 15%",  "L4",           lambda l4, l34, tb, lr: l4 >= 0.15),
        ("L3+L4 合计 ≥ 50%",   "L3+L4",        lambda l4, l34, tb, lr: l34 >= 0.50),
        ("工具广度 ≥ 15 种",    "tool_breadth", lambda l4, l34, tb, lr: tb >= 15),
        ("提交落地率 ≥ 50%（git 口径）", "landed_ratio", lambda l4, l34, tb, lr: lr >= 0.50),
    ]),
    (3, "精通期", [
        ("L3+L4 合计 ≥ 35%",   "L3+L4",        lambda l4, l34, tb, lr: l34 >= 0.35),
        ("工具广度 ≥ 10 种",    "tool_breadth", lambda l4, l34, tb, lr: tb >= 10),
    ]),
    (2, "进阶期", [
        ("L3+L4 合计 ≥ 15%（开始主动引导）", "L3+L4", lambda l4, l34, tb, lr: l34 >= 0.15),
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
