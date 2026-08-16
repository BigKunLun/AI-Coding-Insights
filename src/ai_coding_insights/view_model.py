"""画像视图模型：报告的「算数据 / 下判定」层，全部为无 IO 纯函数。

分工（与 report.py 的边界，改任一侧都要看另一侧）：

- 本模块：派生与决策——雷达轴归一、四维代表行的值与单位、落地率/落地数/丢弃数的
  展示口径与降级链、姿态诊断与档位判定的调用、姿势条分段、趋势行、时间线柱、
  token 明细派生、能力盲区。产出纯数据 dict。
- report.py：dict → HTML。颜色表、CSS、模板辅助等纯排版常量留在那边。

转义纪律：本模块**绝不调用 escape**，也绝不产出 HTML 片段。转义一律由 report.py
在插值点做，保证同一段文本只被转义一次——历史 bug 在此：headline 若在这里预转义，
会与消费端 `_hl_nums`（内部已 escape）叠加成双重转义。
"""

import math

from .capabilities import unused_capabilities
from .evidence_check import POINTER_UUID_SOURCES
from .sources import CLAUDE_CODE, UnknownSourceError, get_source
from .stage import decide_stage, diagnose_posture, normalize_posture

# 「本来源测不到」的统一显示文本。刻意与「—」（有这个指标但这次取不到数）区分：
# 前者是能力边界（这家 harness 没这个概念），后者是数据缺失，两者对用户的含义完全不同。
UNMEASURED_TEXT = "未测量"

# 雷达图满刻度（与 stage.py 阶段阈值无关，仅控制可视化拉伸）：
RADAR_BREADTH_FULL = 35.0      # 工具广度 35 种打满（≈内置高杠杆能力全集的量级上限）
RADAR_DEPTH_FULL_TURNS = 20.0  # P90 轮次 20 打满（取 P90 后上限上浮，微会话不再拉低）

# 姿势段宽占比低于此阈值放不下「Lx NN%」标签：硬塞会被 min-content 撑出真实宽度、
# 文字溢进邻段又被裁成半字，故窄段留纯色块（占比在图例完整呈现，hover title 仍带）。
_SEG_LABEL_MIN = 0.08

_POSTURE_CODES = ("L1", "L2", "L3", "L4")

# 横幅同比摘要的字段与中文名（顺序即呈现顺序）
_DIFF_LABELS = {
    "landed_ratio": "落地率", "git_landed_count": "落地提交",
    "git_commit_total": "提交总数",
    "dropped_count": "观测丢弃", "commit_count": "会话内提交",
    "edit_count": "编辑数", "session_count": "会话数",
    "human_input_count": "有效输入", "tool_breadth": "工具广度",
    "active_days": "活跃天数",
}

_SCOPE_LABELS = {
    "all": "个人模式（全部本机会话）",
    "include": "团队模式（仅配置纳入的项目）",
}

# 未测量字段 → 给用户看的中文名。只登记会出现在报告里的字段；未登记的直接透出字段名
# （宁可露出个英文标识符，也不要把它悄悄从 caveat 里漏掉）。
_METRIC_LABELS = {
    "tool_breadth": "工具广度", "tool_session_counts": "各工具使用会话数",
    "subagent_sessions": "SubAgent 会话", "max_parallel_agents": "真并行峰值",
    "parallel_agent_turns": "真并行轮次", "workflow_sessions": "Workflow 会话",
    "mcp_sessions": "MCP 会话", "mcp_server_counts": "MCP 服务器分布",
    "skill_counts": "技能使用会话数", "skill_total_counts": "技能调用次数",
    "plan_mode_sessions": "计划模式会话", "plan_mode_count": "计划模式次数",
    "thinking_block_count": "深度推理块", "thinking_sessions": "深度推理会话",
    "background_task_count": "后台委托", "background_sessions": "后台委托会话",
    "option_pick_count": "选项应答数", "token_usage": "Token 明细",
    "token_total": "Token 总量", "git_landed_count": "落地提交",
    "git_commit_total": "提交总数", "landed_ratio": "落地率",
    "commit_count": "会话内提交", "landed_count": "会话内落地", "dropped_count": "观测丢弃",
    "edit_count": "编辑数", "custom_skill_count": "自建技能数",
    "claude_md_sessions": "项目约定文件改动",
}


def metric_label(field: str) -> str:
    return _METRIC_LABELS.get(field, field)

# (中文行名, key, 形态)。计数类（per_session）按「次/会话」密度呈现：
# 前后半段会话数往往悬殊（一段可能是另一段的数倍），原始计数对比只反映体量差，
# 全行无脑↑毫无信息量甚至误导；密度才反映行为变化。会话数基数放表头。
_TREND_ROWS = [
    ("提交", "commits", "per_session"),
    ("落地率(观测)", "landed_ratio", "ratio"),
    ("纠偏锚点", "override", "per_session"),
    ("报错锚点", "error", "per_session"),
    ("极短输入占比", "short_ratio", "ratio"),
]


def safe_num(v):
    """外部 JSON 的数值字段 → int/float；取不到数一律 None（渲染成「—」）。

    入参全是外部输入（metrics 来自 _aggregate.json、profile 由 LLM 生成），
    直接 int()/float() 会抛异常炸掉整张报告——报告是编排链的最终产物，
    崩在这一步等于前面所有工作作废，故一律换成明确降级。

    口径三条：
    - 不静默填 0：0 在本项目是实测真值（max_parallel_agents=0 意为「从未并发」），
      把「测不到」渲染成 0 是对用户撒谎，只能出 None→「—」。
    - bool 不当数字：LLM 写出 true 是脏值，渲染成 1 同样是撒谎。
    - NaN/inf 按取不到数：会污染下游格式化（int(inf) 直接抛 OverflowError）与比较。
    数字字符串（"14"）仍按数字读——值本身无歧义，没必要降级成「—」。
    """
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, str):
        s = v.strip()
        try:
            v = int(s) if s.lstrip("+-").isdigit() else float(s)
        except ValueError:
            return None
    if not isinstance(v, (int, float)):
        return None
    try:
        return v if math.isfinite(v) else None
    except OverflowError:   # 大到超出 float 的整数（JSON 整数无上限）：同按取不到数
        return None


def safe_int(v):
    """safe_num 后取整（向零截断，与旧 int() 同口径）；取不到数 → None。"""
    n = safe_num(v)
    return None if n is None else int(n)


def first_num(*vals):
    """降级链取数：按序返回第一个能取到数的值，全脏 → None。

    脏值不许打断链——git 主锚脏了要继续退到 transcript 硬证据、再退 LLM 抄值，
    而不是就地出「—」。防崩不得改变 fallback 链的优先级语义。
    """
    for v in vals:
        n = safe_num(v)
        if n is not None:
            return n
    return None


def fnum(v) -> float:
    """容错取数：None/非数→0.0。趋势/Token 条形图共用。

    OverflowError：JSON 整数无上限，float(10**400) 会抛——一个脏字段不该炸整张报告。
    """
    try:
        return float(v or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0


def cn_num(n: int) -> str:
    """1-10 的中文数字（横幅判据句「N项判据全部达标」用）；超界回退阿拉伯数字。"""
    table = "零一二三四五六七八九十"
    return table[n] if 0 <= n <= 10 else str(n)


def num_text(v) -> str:
    """数值文本：None→「—」。不转义（转义由渲染层在插值点做）。"""
    return "—" if v is None else str(v)


def pct_text(v) -> str:
    """比率文本：None/脏值→「—」，有值→整数百分比。"""
    n = safe_num(v)
    return "—" if n is None else f"{n:.0%}"


def trend_arrow(a: float, b: float) -> str:
    if b > a:
        return "↑"
    if b < a:
        return "↓"
    return "→"


def trend_view(trend: dict | None) -> dict | None:
    """窗口内趋势对比数据；trend 为 None / 空返回 None（整段不渲染）。

    返回 {first_sessions, second_sessions, rows:[{name, a_text, b_text, arrow}]}。
    arrow 为空串表示某半不可观测（不画方向）。
    """
    if not trend or not isinstance(trend, dict):
        return None
    fh = trend.get("first_half") if isinstance(trend.get("first_half"), dict) else {}
    sh = trend.get("second_half") if isinstance(trend.get("second_half"), dict) else {}
    # 会话数取不到（缺/脏）按 0，与「本就 0 会话」同路径处理：密度分母为 0 时
    # 下面统一出 0.00，不新增第三种形态。
    fh_n = safe_int(fh.get("sessions")) or 0
    sh_n = safe_int(sh.get("sessions")) or 0
    rows = []
    for name, key, kind in _TREND_ROWS:
        if key in ("commits", "landed_ratio") and not (
                fnum(fh.get("commits")) or fnum(sh.get("commits"))):
            # trend 的提交数据来自 transcript 口径：两半全 0 多半是不可观测
            # （如旧版 CC 无 gitOperation 回执），是测不到不是没提交，0% 假行不出。
            continue
        unobserv = False
        if kind == "ratio":
            # 某半 commits=0 → landed_ratio 为 None（不可观测）：记「—」，不画 0% 假值/假箭头。
            # 脏值同样按不可观测——伪造出的 0% 比空着更误导。
            a_o, b_o = safe_num(fh.get(key)), safe_num(sh.get(key))
            a, b = a_o or 0.0, b_o or 0.0
            unobserv = a_o is None or b_o is None
            a_txt = "—" if a_o is None else f"{a:.0%}"
            b_txt = "—" if b_o is None else f"{b:.0%}"
        else:
            a_raw, b_raw = fnum(fh.get(key)), fnum(sh.get(key))
            a = a_raw / fh_n if fh_n else 0.0
            b = b_raw / sh_n if sh_n else 0.0
            name = f"{name}（次/会话）"
            a_txt, b_txt = f"{a:.2f}", f"{b:.2f}"
        rows.append({"name": name, "a_text": a_txt, "b_text": b_txt,
                     "arrow": "" if unobserv else trend_arrow(a, b)})
    return {"first_sessions": fh_n, "second_sessions": sh_n, "rows": rows}


def timeline_bars(daily) -> list:
    """时间线柱：每项 {date, count, height_pct, color}。按会话数分 4 档配色，峰值满高。"""
    rows = []
    for d in (daily if isinstance(daily, list) else []):
        if isinstance(d, dict) and isinstance(d.get("date"), str) and d["date"]:
            # 会话数取不到数或为负 → 这一天成不了柱：整项跳过，
            # 好过画一根撒谎的 0 柱（0 会话是真值，与「测不到」不是一回事）。
            c = safe_int(d.get("session_count", 0))
            if c is not None and c >= 0:
                rows.append((d["date"], c))
    if not rows:
        return []
    rows.sort(key=lambda x: x[0])
    mx = max(c for _, c in rows) or 1
    out = []
    for dt, c in rows:
        if c == 0:
            color = "#ebedf0"
        elif c <= 5:
            color = "#c6e7da"
        elif c <= 12:
            color = "#6fc9b0"
        elif c <= 20:
            color = "#2d9d7e"
        else:
            color = "#1a6b5a"
        out.append({"date": dt, "count": c, "height_pct": round(c / mx * 100, 1), "color": color})
    return out


def bar_items(counts: dict, top_n: int = 15) -> tuple[list, float]:
    """降序取 Top N 项 + 最大值。返回 ([(name, count), ...], mx)。counts 为空返回 ([], 0.0)。"""
    if not counts or not isinstance(counts, dict):
        return [], 0.0
    # 非数值计数（脏 JSON）直接剔除：留着会在 -x[1] 上抛 TypeError 炸整张报告，
    # 而一个画不出条的项本来也没有呈现价值。
    clean = [(str(k), n) for k, v in counts.items() if (n := safe_num(v)) is not None]
    items = sorted(clean, key=lambda x: (-x[1], x[0]))[:top_n]  # 同计数按名排序，避免跨进程顺序不定
    if not items:
        return [], 0.0
    return items, float(items[0][1])


def token_items(token_usage: dict | None) -> list[tuple[str, float]] | None:
    """token_usage → 按 output 降序的 (模型名, output) 列表；空/全零返回 None（图整体降级）。"""
    if not token_usage or not isinstance(token_usage, dict):
        return None
    items = [(str(name), fnum(b.get("output")))
             for name, b in token_usage.items() if isinstance(b, dict)]
    if not items or max(o for _, o in items) <= 0:
        return None
    items.sort(key=lambda t: (-t[1], t[0]))  # 同 output 按模型名排序，保证可复现顺序
    return items


def fold_unmeasured(families: list[dict]) -> tuple[list[dict], list[dict]]:
    """把「未测量」的指标格从主网格摘出去，返回 `(保留的族, 折叠项)`。

    为什么折叠而不是照常显示：Codex 报告 16 格里 7 格是「未测量」，44% 的版面
    是空的，第一观感变成「这工具对我没用」，而不是「我的工具链缺这些能力」。
    摘出去之后主网格全是实数，未测量项进折叠区块——**位置变了，信息一条不少**。

    「未测量 ≠ 0」在这里仍然成立：折叠项保留原样（value 仍是「未测量」），
    绝不改写成 0 或「—」。整族被摘空则连族头一起去掉，不留空壳。

    兜底：真出现「一格都测不到」时原样退回不折叠——空白的指标区无从解释，
    满屏「未测量」至少自证了原因。
    """
    kept: list[dict] = []
    folded: list[dict] = []
    for fam in families:
        alive = [c for c in fam["cells"] if not c.get("unmeasured")]
        folded.extend({**c, "family": fam["name"]}
                      for c in fam["cells"] if c.get("unmeasured"))
        if alive:
            kept.append({**fam, "cells": alive})
    if not kept:
        return families, []
    return kept, folded


def build_view(profile: dict, meta: dict, metrics: dict | None = None,
               diff: dict | None = None) -> dict:
    """画像数据 → 视图模型（纯函数，不改入参、不碰 IO、不出 HTML）。

    返回的 dict 是 report.py 渲染的唯一输入，也是 cli 打印 stdout 接缝
    （posture_state / stage_name）的唯一真相源——两处取同一份判定，不各算各的。
    """
    # 三份入参都是外部 JSON（profile 由 LLM 生成、metrics 来自 _aggregate.json、
    # meta 由编排端拼），形态错了也不许炸：整份不是对象就按「这份没有」降级。
    # metrics 尤其要退成 None 而非 {}——两者语义不同（见 metrics=None 分支）。
    profile = profile if isinstance(profile, dict) else {}
    meta = meta if isinstance(meta, dict) else {}
    if metrics is not None and not isinstance(metrics, dict):
        metrics = None
    if diff is not None and not isinstance(diff, dict):
        diff = None
    # posture_distribution 由规则层组装注入（assemble_posture，恒 0-1 比例，
    # 和为 1 或全零）；此处归一是对手喂 dict 的防御性兜底，无百分数形态的正常来源。
    # 逐档洗成数值再交给 normalize_posture：它内部 float() 吃到脏值会抛。
    _raw_pd = profile.get("posture_distribution")
    _raw_pd = _raw_pd if isinstance(_raw_pd, dict) else {}
    pd = normalize_posture({t: (safe_num(_raw_pd.get(t)) or 0.0) for t in _POSTURE_CODES})

    def pct(key: str) -> float:
        return pd.get(key, 0.0)

    outcome = profile.get("outcome")
    outcome = outcome if isinstance(outcome, dict) else {}
    o_landed = float(safe_num(outcome.get("landed")) or 0)
    o_total = float(safe_num(outcome.get("total")) or 0)
    o_ratio = (o_landed / o_total) if o_total else 0.0

    m = metrics or {}

    # ---- 来源与「未测量」字段集（多 harness 承重）----
    # `unmeasured` 由规则层按来源能力算定（sources.unmeasured_fields）并写进
    # _aggregate.json。命中的字段一律显示「未测量」而非 0/「—」——把 Codex 里根本
    # 不存在的 Workflow 渲染成「0 次」，用户读到的是「你没用过」这个错误结论。
    _um = m.get("unmeasured")
    unmeasured = frozenset(str(x) for x in _um) if isinstance(_um, list) else frozenset()
    source_name = str(m.get("source") or CLAUDE_CODE)
    try:
        source_label = get_source(source_name).label
    except UnknownSourceError:
        # 快照/中间产物来自更新版本、含本版不认识的来源名：如实透出原名，不猜、不静默
        source_label = source_name

    # 能力集的正面声明（能力盲区据它判「这家有没有这个概念」）。
    # 键缺失时**先按来源名回填**，而不是直接退到「全集」：退全集等于对 Codex 用户
    # 报一串它压根没有的能力盲区（「你没用过 Workflow」），是典型的不报错、只安静
    # 产出错误建议。只有来源也不认识（真·旧文件）才退全集 = claude-code 行为不变。
    _caps = m.get("capabilities")
    if isinstance(_caps, list):
        source_caps = frozenset(str(c) for c in _caps)
    else:
        try:
            source_caps = frozenset(get_source(source_name).capabilities)
        except UnknownSourceError:
            source_caps = None

    def um(key: str) -> bool:
        return key in unmeasured

    # ---- 核心指标取值（metrics 缺失时按要求兜底到 outcome，再无则 None→"—"）----
    # 成果类数字统一「硬指标优先、LLM outcome 兜底」：落地数/提交数与奖励挂钩，
    # 必须以可独立验证的 metrics 为准，LLM 转抄值只作缺数时的降级显示。
    # 全部经 first_num/safe_num：脏值等同缺数，继续沿降级链往下走而不是就地炸掉。
    # 落地率的分母是「窗口内同仓本人提交总数」。分母为 0 时 `landed_ratio` 这个
    # property 退化成 0.0，但那是 0÷0 的字面值，**不是**「做了没落地」——
    # 窗口内根本没有可归属的提交（会话不在 git 仓库里、或本人这段时间没提交）。
    # 照 0% 渲染就是把「锚点缺位」说成「成果为零」，属本项目定义的错导。
    # 故此处先判分母：分母不可用就让整条降级链走到 None → 渲染成「—」。
    # 只在分母**明确存在且为 0** 时才抑制：分母整个缺席是旧格式 _aggregate.json
    # （那时还没有 git 主锚字段），那种情况仍照既有降级链信任已给的 landed_ratio，
    # 否则老快照会集体退化成「—」。
    _denom = safe_num(m.get("git_commit_total"))
    _ratio_degenerate = _denom is not None and _denom <= 0
    # 分母明确为 0 时**整条降级链一并掐掉**，不许退到 LLM 抄来的 outcome 比率——
    # 那等于用一个更不可靠的来源，把刚判定为「测不出来」的数字又填回去。
    landed_ratio = (None if _ratio_degenerate
                    else first_num(m.get("landed_ratio"),
                                   o_ratio if o_total else None))
    edit_count = safe_num(m.get("edit_count"))
    # git 主锚口径：落地数取 git_landed_count。降级链：旧口径 metrics（缺 git 键，
    # 如旧 _aggregate）退到 transcript 硬证据（landed_count 经 HEAD 验证，是 git 落地
    # 的下界）；metrics 整体缺席才用 LLM 抄值（profile.outcome 的 landed/total 已是
    # 新语义：landed=git 落地、total=落地+观测丢弃）。
    git_landed = first_num(m.get("git_landed_count"), m.get("landed_count"),
                           o_landed if o_total else None)
    # 落地率分母：窗口内同仓本人提交总数（与 git_landed 同口径）。
    git_commit_total = safe_int(m.get("git_commit_total"))
    _cc, _lc = safe_int(m.get("commit_count")), safe_int(m.get("landed_count"))

    def _dropped_fallback():
        # 硬证据兜底 commit-landed；任一端取不到数（脏/漂移 metrics）退到 LLM outcome
        if _cc is not None and _lc is not None:
            return max(0, _cc - _lc)
        return (o_total - o_landed) if o_total else None
    dropped = first_num(m.get("dropped_count"), _dropped_fallback())

    # 判定入参同样先洗：stage/posture 两个判定函数拿数直接比大小，脏值会抛
    posture_diag = (None if metrics is None else diagnose_posture(
        pd, safe_int(m.get("decision_point_count")) or 0,
        safe_int(m.get("plan_mode_sessions")) or 0,
        safe_int(m.get("thinking_sessions")) or 0,
        unmeasured=unmeasured))
    posture_state = posture_diag["state"] if posture_diag else None
    # 阶段判定只算一次：横幅大字 / 判据卡 / cli stdout 共用同一结果。
    # unmeasured 命中的判据会被跳过并记进 stage["skipped"]，档位随之标为不可比——
    # 门变松的代价必须显式呈现，不能悄悄抬档。
    stage = None if metrics is None else decide_stage(m, unmeasured=unmeasured)

    def diff_of(key: str):
        """取该指标的同比 dict；无 diff / 键缺失 / 脏值 → None。"""
        if isinstance(diff, dict) and key in diff and isinstance(diff[key], dict):
            return diff[key]
        return None

    def dur_cell(label, v):
        """时长中位数格：None/脏值→「—」无单位，有值→整数 + min 单位。"""
        n = safe_num(v)
        # `unmeasured` 键所有格子都带，形状统一（fold_unmeasured 才能一视同仁地筛）。
        # 时长恒可测：任何来源都有时间戳，取不到只是本窗口没数据，不是测不到。
        if n is not None:
            return {"label": label, "value": str(round(n)), "unit": "min",
                    "diff": None, "unmeasured": False}
        return {"label": label, "value": "—", "unit": None,
                "diff": None, "unmeasured": False}

    def cell(label, value, diff_key=None, unmeasured_key=None):
        """指标格。unmeasured_key 命中「本来源测不到」时值改显「未测量」并**抹掉同比**——
        同比拿 0 跟上次真值比会画出一个凭空的↓箭头，那是纯造谣。"""
        if unmeasured_key and um(unmeasured_key):
            return {"label": label, "value": UNMEASURED_TEXT, "unit": None,
                    "diff": None, "unmeasured": True}
        return {"label": label, "value": value, "unit": None,
                "diff": diff_of(diff_key) if diff_key else None, "unmeasured": False}

    def mcell(label, key, diff_key=None):
        """计数格：值经 safe_num，取不到数出「—」（不填 0，0 是实测真值）。
        字段进了 unmeasured 则出「未测量」——那种 0 不是真值。"""
        return cell(label, num_text(safe_num(m.get(key))), diff_key, unmeasured_key=key)

    def mtext(key: str, text: str) -> str:
        """给横幅/代表行用的文本降级：字段测不到就出「未测量」。"""
        return UNMEASURED_TEXT if um(key) else text

    tb = safe_num(m.get("tool_breadth"))
    tp90 = safe_num(m.get("turn_p90"))
    landed_ratio_text = mtext("landed_ratio", pct_text(landed_ratio))

    # ---- 横幅四数 = 四维代表值 ----
    hero_nums = [
        {"value": landed_ratio_text, "label": "成果 · 落地率"},
        {"value": posture_state if posture_diag else "—", "label": "姿态健康"},
        {"value": mtext("tool_breadth", num_text(tb)), "label": "水平 · 工具广度"},
        {"value": num_text(tp90), "label": "深度 · P90 轮次/会话"},
    ]

    # ---- 指标明细：四族，不重复横幅四数 ----
    # 不出「编辑/落地」派生比率：edit_count 是全会话编辑量、git_landed 是 git 锚落地数，
    # 跨口径相除（分子分母分属不同总体）无 per-commit 语义。只陈列两个原值。
    token_usage = m.get("token_usage")
    token_usage = token_usage if isinstance(token_usage, dict) else {}
    families = [
        {"name": "产出落地", "cells": [
            cell("落地提交", num_text(safe_int(git_landed)), "git_landed_count",
                 unmeasured_key="git_landed_count"),
            cell("提交总数", num_text(git_commit_total), "git_commit_total",
                 unmeasured_key="git_commit_total"),
            cell("观测丢弃", num_text(safe_int(dropped)), "dropped_count",
                 unmeasured_key="dropped_count"),
            cell("编辑数", num_text(edit_count), "edit_count",
                 unmeasured_key="edit_count"),
        ]},
        {"name": "协作编排", "cells": [
            mcell("SubAgent 会话", "subagent_sessions", "subagent_sessions"),
            mcell("Workflow 会话", "workflow_sessions", "workflow_sessions"),
            mcell("MCP 会话", "mcp_sessions", "mcp_sessions"),
            cell("使用模型数", num_text(len(token_usage) or None)),
        ]},
        # 高阶行为：三个维度信号均为确定性硬指标（深度推理块 / 后台委托 / 真并行），
        # 由规则层从 transcript 直接计数，不依赖 LLM 判定。真并行峰值=1、轮次=0 表示
        # 「用过子代理但总是顺序派发、从未单轮并发」——是准确信号而非缺数。
        {"name": "高阶行为", "cells": [
            mcell("深度推理", "thinking_block_count"),
            mcell("后台委托", "background_task_count"),
            mcell("真并行峰值", "max_parallel_agents"),
            mcell("真并行轮次", "parallel_agent_turns"),
        ]},
        {"name": "节奏投入", "cells": [
            mcell("会话数", "session_count", "session_count"),
            mcell("有效输入", "human_input_count", "human_input_count"),
            mcell("活跃天数", "active_days", "active_days"),
            dur_cell("时长 P90", m.get("duration_p90_min")),
        ]},
    ]
    families, unmeasured_cells = fold_unmeasured(families)

    # ---- 姿势分布：图例占比 + 大堆叠条分段 ----
    total_pd = sum(pct(t) for t in _POSTURE_CODES) or 1.0
    posture_pct_text = {t: f"{pct(t):.0%}" for t in _POSTURE_CODES}
    posture_segments = []
    for t in _POSTURE_CODES:
        frac = pct(t) / total_pd
        if frac <= 0:
            continue
        posture_segments.append({
            "code": t,
            "width_pct": frac * 100,
            "label": f"{t} {posture_pct_text[t]}" if frac >= _SEG_LABEL_MIN else "",
            "title": f"{t} {posture_pct_text[t]}",
        })

    # ---- 四维雷达轴（各自归一到 [0,1]，超满分截断；负值同样钳住不让多边形翻出去）----
    axis_posture = max(0.0, min(1.0, pct("L3") + pct("L4")))
    axis_breadth = max(0.0, min(tb / RADAR_BREADTH_FULL, 1.0)) if tb is not None else 0.0
    axis_depth = (max(0.0, min(tp90 / RADAR_DEPTH_FULL_TURNS, 1.0))
                  if tp90 is not None else 0.0)
    if landed_ratio is not None:
        axis_outcome = max(0.0, min(1.0, landed_ratio))
    elif o_total:
        axis_outcome = max(0.0, min(1.0, o_ratio))
    else:
        axis_outcome = 0.0

    breadth = profile.get("breadth")
    depth = profile.get("depth")

    def headline(block) -> str:
        # 返回原文，不在此预转义：唯一消费者是雷达 dim_rows 的 desc，统一过 _hl_nums
        # （单次 escape）。此处再 escape 会与 _hl_nums 叠加成双重转义。
        # 维度块不是对象（LLM 直接写了一段字符串）时按无 headline 处理，不炸。
        if not isinstance(block, dict):
            return ""
        return str(block.get("headline") or block.get("summary") or "")

    # 成果代表行附「落地 X · 观测丢弃 Y」（git 主锚口径）。与横幅同源：硬指标优先。
    # 测不到的那一半**整段不写**，而不是写「未测量」——这一行是一句话里的两个数，
    # 塞个「未测量」进去既拗口又是第三次重复（caveat 卡片、折叠区已各说过一次）。
    # 曾经的 bug：这里绕过降级把测不到的观测丢弃写成 0，旁边解释又说「不是 0」，
    # 同一份报告自相矛盾——比少写一个数伤可信度得多。
    landed_disp = UNMEASURED_TEXT if um("git_landed_count") else num_text(safe_int(git_landed))
    dropped_disp = UNMEASURED_TEXT if um("dropped_count") else num_text(safe_int(dropped))
    _parts = ([] if um("git_landed_count") else [f"落地 {landed_disp}"]) + \
             ([] if um("dropped_count") else [f"观测丢弃 {dropped_disp}"])
    # 两个数都测不到时整段为空，此处不能留下孤零零的分隔点
    outcome_nums = " · ".join(_parts)
    outcome_desc = " · ".join([headline(outcome)] + _parts if headline(outcome) else _parts)
    dim_rows = [
        {"name": "姿势", "value": posture_state if posture_diag else "—", "unit": "姿态",
         "desc": (posture_diag["reason"] if posture_diag
                  else f"L3+L4 合计 {axis_posture:.0%}")},
        {"name": "水平", "value": num_text(tb), "unit": "种工具", "desc": headline(breadth)},
        {"name": "深度", "value": num_text(tp90), "unit": "P90 轮/会话", "desc": headline(depth)},
        {"name": "成果", "value": landed_ratio_text, "unit": "落地率", "desc": outcome_desc},
    ]

    # ---- 横幅档位判据小结 ----
    if stage is not None:
        gaps = stage.get("gaps") or []
        stage_crit_note = (f"{cn_num(len(stage.get('criteria') or []))}项判据全部达标"
                           if not gaps else f"距下一档还差 {len(gaps)} 项判据")
        # 有判据被跳过就必须写在同一句里：读者看到「全部达标」时得知道达标的是几项、
        # 少看了哪几项，否则跨来源的档位会被当成同一把尺来比。
        if stage.get("skipped"):
            stage_crit_note += f"（{len(stage['skipped'])} 项本来源测不到，已跳过）"
    else:
        stage_crit_note = ""

    # ---- 横幅同比摘要：无 diff / 首次基线 / 逐项箭头 ----
    if diff is None:
        diff_note_kind = "none"
        diff_summary = []
    elif diff.get("baseline"):
        diff_note_kind = "baseline"
        diff_summary = []
    else:
        diff_note_kind = "items"
        # 无基线（no_base / arrow=None）的键不出箭头，整项跳过
        diff_summary = [(label, d) for key, label in _DIFF_LABELS.items()
                        if (d := diff_of(key)) is not None
                        and not d.get("no_base") and d.get("arrow") is not None]

    window = meta.get("window")
    window = window if isinstance(window, dict) else {}
    projects = meta.get("included_projects")
    projects = projects if isinstance(projects, list) else []

    # 能力盲区的谓词会拿这些值直接跟数字比大小 / 取 key，脏值会抛异常炸整张报告。
    # 只洗它真正读的两个数值字段，其余原样透传；非对象形态按「无判定依据」→ None，
    # 让 unused_capabilities 跳过对应能力，不报假阳性（比塞空 dict 说「没用过」诚实）。
    tool_counts = m.get("tool_session_counts")
    tool_counts = tool_counts if isinstance(tool_counts, dict) else {}
    _cs = m.get("customization_signals")
    caps_cs = ({**_cs, "claude_md_sessions": safe_num(_cs.get("claude_md_sessions")) or 0}
               if isinstance(_cs, dict) else None)
    caps_metrics = (None if metrics is None
                    else {**m,
                          "max_parallel_agents": safe_num(m.get("max_parallel_agents")) or 0})

    # ---- 来源与降级 caveat（显式断点；静默劣化是本项目定义的最危险故障）----
    # 每条都必须在报告里露面：读者得知道这份报告的尺子是哪把、少量了什么、
    # 编排是不是走了降级版。缺任何一条，跨来源的数字就会被当成同一把尺来比。
    # 每条是 {"text": 一句结论, "detail": 可展开的明细或 None}。
    # 结论必须置顶可见（它决定下面所有数字怎么读），**明细可以收起**——把十几个
    # 字段名摊在首屏三行，读者跳过整张卡片的概率比点开一次还高。
    source_notes = []
    if unmeasured:
        names = "、".join(metric_label(f) for f in sorted(unmeasured))
        source_notes.append({
            "text": f"{len(unmeasured)} 项指标在 {source_label} 会话记录里没有对位概念，"
                    f"报告中标为「未测量」，请勿读作 0",
            "detail": names,
        })
    if stage is not None and stage.get("skipped"):
        source_notes.append({
            "text": f"成熟度档位有 {len(stage['skipped'])} 项判据本来源测不到、已跳过，"
                    f"档位与其他来源不直接可比",
            "detail": "；".join(stage["skipped"]),
        })
    # 编排降级由编排端自报（meta.run.degraded）：无子代理的 harness 走单轮顺序版，
    # 分析深度低于并行版，必须让用户知道，不能只在结论上不声不响地缩水。
    run_meta = meta.get("run")
    run_meta = run_meta if isinstance(run_meta, dict) else {}
    degraded = bool(run_meta.get("degraded"))
    if degraded:
        source_notes.append({
            "text": "本次为降级编排（当前 harness 无子代理能力）：分档与专家分析由单轮"
                    "顺序完成，覆盖深度低于并行版",
            "detail": None,
        })
    if metrics is not None and source_name not in POINTER_UUID_SOURCES:
        # 没核过就不能让「没有 ⚠ 标记」被读成「都核过、都对」
        source_notes.append({
            "text": f"{source_label} 的会话记录暂不支持证据指针的 turn 级回看，"
                    "本次只核了文件存在性",
            "detail": "证据条目未出现「⚠ 指针未命中」不等于指针已逐条核实",
        })
    return {
        # 姿势
        "posture_distribution": pd,
        "posture_pct_text": posture_pct_text,
        "posture_segments": posture_segments,
        "posture_diag": posture_diag,
        "posture_state": posture_state,          # 接缝：cli stdout「姿态健康态」
        # 档位
        "stage": stage,
        "stage_no": None if stage is None else int(stage.get("stage", 1)),
        "stage_name": None if stage is None else str(stage.get("name", "")),
        "stage_crit_note": stage_crit_note,
        # 成果口径
        "landed_ratio": landed_ratio,
        "landed_ratio_text": landed_ratio_text,
        "git_landed": git_landed,
        "git_commit_total": git_commit_total,
        "dropped": dropped,
        "edit_count": edit_count,
        "outcome_landed": o_landed,
        "outcome_total": o_total,
        "outcome_ratio": o_ratio,
        "outcome_desc": outcome_desc,
        "landed_disp": landed_disp,
        "dropped_disp": dropped_disp,
        # 成果卡片脚注用的「落地 X · 观测丢弃 Y」——与代表行同源，测不到的那半段
        # 已在此摘掉。两处各拼一次就会漏（曾漏过：代表行改了，卡片还写着「未测量」）。
        "outcome_nums": outcome_nums,
        # 四维
        "radar_labels": ["姿势", "水平", "深度", "成果"],
        "radar_axes": [axis_posture, axis_breadth, axis_depth, axis_outcome],
        "dim_rows": dim_rows,
        # 横幅 / 指标明细
        "hero_nums": hero_nums,
        "families": families,
        # 主网格摘出去的未测量格（含所属族名），由 report 渲染成折叠区块。
        # 与下面的 `unmeasured` 不是一回事：那是**全部**未测量字段（含不上网格的），
        # 供置顶的来源口径卡片列举；这里只是网格里被藏起来的那几格。
        "unmeasured_cells": unmeasured_cells,
        "diff_note_kind": diff_note_kind,
        "diff_summary": diff_summary,
        # 附属派生
        "trend": trend_view(m.get("trend")),
        "timeline_bars": timeline_bars(m.get("daily")),
        "token_items": token_items(m.get("token_usage")),
        "capability_gaps": (None if metrics is None else unused_capabilities(
            tool_counts, customization_signals=caps_cs, metrics=caps_metrics,
            capabilities=source_caps)),
        # 来源口径（多 harness）：报告须标明这份数据的尺子是哪把、少量了什么
        "source": source_name,
        "source_label": source_label,
        "unmeasured": sorted(unmeasured),
        "source_notes": source_notes,
        "degraded": degraded,
        # meta 派生
        # mode 脏值可能不可哈希（dict/list 直接把 .get 炸成 TypeError），先卡成字符串
        "scope_label": _SCOPE_LABELS.get(str(window.get("mode", "")), ""),
        "session_count": safe_int(meta.get("session_count")) or 0,
        "projects": projects,
    }
