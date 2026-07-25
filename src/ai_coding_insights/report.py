"""报告渲染层：只做 dict → HTML（像素、配色、转义）。

所有数据派生与判定——雷达轴归一、四维代表值、落地率/落地数/丢弃数口径、
档位与姿态诊断、趋势行、token 明细——住在 view_model.py 的无 IO 纯函数里。
本模块不算数：新增判断逻辑一律加到 view_model 并在那边写字段级测试，
这里只按 view 的字段排版，避免决策逻辑再次埋进渲染只能靠 grep HTML 来测。
"""

import math
import re
from html import escape

from .models import InsightsReport
from .view_model import build_view
# 派生/决策一律住在 view_model（无 IO 纯函数层）；此处按旧名引入，报告只做 dict → HTML。
from .view_model import bar_items as _bar_items
from .view_model import safe_int, safe_num
from .view_model import timeline_bars as _timeline_bars

# ════ 样式约定（与设计稿一一对应）════
# 背景      页面 #f3f5fa · 卡片 #fff · 卡片描边 #e1e5ef · 卡内分隔 #eef0f5
# 横幅      linear-gradient(120deg,#0b1026,#18204a) + 青/紫角部微光 + 底部 3px 渐变 keyline
# 文字      标题 #101828 · 正文 #344054 · 次级 #475467 · 弱化 #667085
# 色彩职责  数据序列(姿势 L1→L4): #c7eaf4 → #76c7e6 → #6e8ef2 → #4640d9
#           指标族: 产出落地 #0d9488 · 协作编排 #4f46e5 · 节奏投入 #7c3aed
#           达标✓: #15803d · 建议动作: #b45309/#fdeac2 · 链接/指针: #0e7490
# 章节号    ui-monospace 12px，按节循环 #0891b2 → #22a3c4 → #4f46e5 → #6366f1 →
#           #7c87f5 → #8b5cf6 → #a78bfa
# 数字      一律 font-variant-numeric: tabular-nums
# 板块顺序  以装配块（render_profile_report 内 `# 0N 段名`）为唯一真相源，本处不复述
# 去重规则  横幅四数 = 四维代表值(成果·落地率 / 姿态·健康态 / 水平·工具广度 / 深度·轮次)，
#           指标明细不重复横幅出现过的数，按族补齐明细(合入、编辑/合入、模型切换等)

_SEC_COLORS = ["#0891b2", "#22a3c4", "#4f46e5", "#6366f1", "#7c87f5", "#8b5cf6", "#a78bfa"]
# 姿势序列 L1→L4（横幅/堆叠条/图例共用）
_POSTURE_COLORS = {"L1": "#c7eaf4", "L2": "#76c7e6", "L3": "#6e8ef2", "L4": "#4640d9"}
# 堆叠条段内文字色按档位身份定（非 DOM 位置）：L1-L3 浅/中底用深字，仅 L4 深底用白字
_BSEG_INK = {"L1": "#0e3a4a", "L2": "#0e3a4a", "L3": "#0e3a4a", "L4": "#ffffff"}
# 高光序号圆点配色（按条目循环）
_HL_DOT = [("#d7f3fa", "#0e7490"), ("#e3e6fd", "#4338ca"), ("#ede7fc", "#6d28d9")]
# 维度色（详述行 + 卡片角标）
_DIM_COLORS = {"姿势": "#0891b2", "水平": "#4f46e5", "深度": "#7c3aed", "成果": "#0d9488"}
# 横幅四数配色（按 view.hero_nums 顺序：成果 / 姿态 / 水平 / 深度）
_HERO_COLORS = ["#67e8f9", "#a5b4fc", "#5eead4", "#fcd34d"]
# 指标族配色（族名 → (色块, 文字)）；族的构成与顺序由 view_model 决定
_FAM_COLORS = {"产出落地": ("#0d9488", "#0f766e"), "协作编排": ("#4f46e5", "#4338ca"),
               "高阶行为": ("#0891b2", "#0e7490"), "节奏投入": ("#7c3aed", "#6d28d9")}
# 方向箭头配色，中性呈现不评判好坏：↑ 靛蓝，↓ 深青，→ 弱化灰（趋势表 / 同比共用）
_ARROW_COLORS = {"↑": "#4f46e5", "↓": "#0e7490"}


def _obj_items(v) -> list:
    """外部 JSON 的「对象列表」字段 → 只留对象项；不是列表按空处理。

    profile / _aggregate.json 里的 frictions、evidence、highlights、drift_flags
    都是这种形态。LLM 偶发把某项写成字符串/数字，少渲染一条好过 `.get` 抛异常
    炸掉整张报告（报告是编排链最终产物，崩在这一步等于前面全白干）。
    """
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def _fmt_tokens(n) -> str:
    """token 量级缩写；取不到数（脏 metrics）→「—」，不伪造 0 也不炸整张报告。
    缺字段仍按 0（bucket 里没这项 = 该项没消耗），沿用既有口径。"""
    v = safe_num(n)
    if v is None:
        return "—" if n is not None else "0"
    n = int(v)
    if n >= 1_000_000_000:
        return f"{n/1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


# ---- L1-L4 图例（静态文案；占比渲染时拼接）----
_LEGEND_ITEMS = [
    ("L1", "跟随", "纯放行 / 跟随确认，未在 AI 已给信息之外增加信息"),
    ("L2", "选择", "只选 AI 给的选项，不加约束（含 AskUserQuestion 选项回答）"),
    ("L3", "引导", "主动给目标 / 约束 / 格式，贴报错追问"),
    ("L4", "主导", "带技术具体性纠错，推翻方案，给 AI 没想到的约束"),
]


def _sec_header(idx: int, title: str, hint: str = "", margin_top: bool = True) -> str:
    """编号章节标题行：编号(等宽,循环色) + 标题 + 可选弱化提示。idx 从 1 起。"""
    color = _SEC_COLORS[(idx - 1) % len(_SEC_COLORS)]
    hint_html = f'<span class="sec-hint">{escape(hint)}</span>' if hint else ""
    mt = "sec" if margin_top else "sec sec-first"
    return (f'<div class="{mt}"><span class="sec-num" style="color:{color}">{idx:02d}</span>'
            f'<span class="sec-title">{escape(title)}</span>{hint_html}</div>')


def _stage_actual(key: str | None, values: dict) -> str:
    """按判据值键取实际值文本。计数键显示「N」带单位，无键返回空串。"""
    if key is None:
        return ""
    units = {"active_days": " 天", "human_input_count": " 条",
             "tool_breadth": " 种", "git_landed_count": " 次",
             "depth_signal": "", "advanced_orchestration": " 项"}
    if key in units:
        return f"{int(values.get(key, 0))}{units[key]}"
    return ""


def _stage_crit_row(crit: dict, values: dict, met: bool) -> str:
    """判据对照行：左判据文案，右实际值 + ✓/✗。无实际值以「—」占位。"""
    actual = _stage_actual(crit.get("key"), values)
    if actual:
        mark = (f'<span class="crit-ok">{actual} ✓</span>' if met
                else f'<span class="crit-miss">{actual} ✗</span>')
    else:
        mark = '<span class="crit-na">—</span>'
    return (f'<div class="crit-row"><span>{escape(str(crit.get("desc", "")))}</span>'
            f'{mark}</div>')


def _render_stage_criteria_inline(st: dict) -> str:
    """判据横向两栏：已达标（绿✓）/ 距下一档（红✗）。复用 _stage_crit_row。"""
    sv = st.get("values", {}) or {}
    met = "".join(_stage_crit_row(c, sv, met=True) for c in (st.get("criteria") or []))
    gaps = st.get("gaps") or []
    miss = "".join(_stage_crit_row(g, sv, met=False) for g in gaps)
    stage_no = int(st.get("stage", 1))
    return (
        '<div class="crit-cols">'
        f'<div class="crit-col"><div class="crit-cap crit-cap-ok">当前第 {stage_no} 档 · 已达标</div>'
        f'<div class="crit-list">{met}</div></div>'
        + (f'<div class="crit-col"><div class="crit-cap crit-cap-miss">距下一档 · 还差</div>'
           f'<div class="crit-list">{miss}</div></div>' if miss else "")
        + '</div>'
    )


def _render_trend_section(trend: dict | None, idx: int) -> str:
    """窗口内趋势对比表；trend 为 view_model.trend_view 的产物，None 则返回空串。
    值均为硬指标数字，自生成无需 escape。"""
    if not trend:
        return ""
    rows_html = ""
    for r in trend["rows"]:
        arrow_color = _ARROW_COLORS.get(r["arrow"], "#667085")
        rows_html += (
            f'<tr><td class="t-name">{r["name"]}</td><td class="t-a">{r["a_text"]}</td>'
            f'<td class="t-b">{r["b_text"]}</td>'
            f'<td class="t-dir" style="color:{arrow_color}">{r["arrow"]}</td></tr>'
        )
    return (
        _sec_header(idx, "窗口内趋势")
        + '<div class="card trend-card">'
        f'<table class="trend"><thead><tr><th>指标</th>'
        f'<th class="num-col">前半段（{trend["first_sessions"]} 会话）</th>'
        f'<th class="num-col">后半段（{trend["second_sessions"]} 会话）</th>'
        '<th class="dir-col">方向</th></tr></thead>'
        f'<tbody>{rows_html}</tbody></table>'
        '<div class="fine-note">前后半按窗口内实际数据的时间中点切分；计数类指标已按每会话密度归一。'
        '落地率为会话内提交观测口径（与头部 git 主锚口径不同），某半提交不可观测时记「—」。'
        '箭头只示变化方向，不评判好坏。</div>'
        '</div>'
    )


def _render_daily_timeline(daily, idx: int) -> str:
    """活动热力 → 时间线柱状：横轴日期、纵轴会话数。空则空串（不占章节号）。

    收原始 daily 而非柱数据：分档/高度的派生在 view_model.timeline_bars（同一份
    结果也挂在 view["timeline_bars"] 供别的消费方取），此处只负责画。"""
    bars = _timeline_bars(daily)
    if not bars:
        return ""
    mx = max(b["count"] for b in bars)
    cols = ""
    for b in bars:
        val = f'<span class="tl-val">{b["count"]}</span>' if b["count"] == mx and mx > 0 else ''
        cols += (f'<div class="tl-bar" style="height:{max(b["height_pct"],2):.1f}%;'
                 f'background:{b["color"]}" title="{escape(b["date"])}：{b["count"]} 会话">{val}</div>')
    first, last = bars[0]["date"], bars[-1]["date"]
    return (
        _sec_header(idx, "活动热力")
        + '<div class="card tl-card"><div class="tl-wrap">' + cols + '</div>'
        + f'<div class="tl-axis"><span>{escape(first)}</span><span>{escape(last)}</span></div>'
        + '<div class="fine-note">每柱为当日会话数，色深按当日活跃分档；峰值标数值。'
        '讲节奏趋势，与下文「窗口趋势」互证。</div></div>'
    )


def _bar_section(counts: dict, title: str, top_n: int = 15) -> str:
    """渲染一组降序条形图：标题 + 条形列表。counts 为空返回空串。
    每行三子元素对齐三列：名 | 彩条轨道(只含填充) | 数值(右对齐)。"""
    items, mx = _bar_items(counts, top_n=top_n)
    if not items:
        return ""
    bars = ""
    for name, cnt in items:
        w = (cnt / mx * 100.0) if mx else 0.0
        bars += (
            f'<div class="tok-row"><span class="tok-label" title="{escape(name)}">'
            f'{escape(name)}</span>'
            f'<span class="tok-bar-wrap"><span class="tok-bar" style="width:{w:.1f}%"></span></span>'
            f'<span class="tok-val">{cnt}</span></div>'
        )
    return (
        f'<details class="tok-block" open><summary><b>{escape(title)}</b></summary>'
        f'<div class="tok-chart">{bars}</div></details>'
    )


def _render_tool_skill_mcp_appendix(tool_session_counts: dict | None,
                                      skill_freq_counts: dict | None,
                                      mcp_server_counts: dict | None) -> str:
    """工具/技能/MCP 分布附录（默认折叠）。三组降序条形图。"""
    if not tool_session_counts and not skill_freq_counts and not mcp_server_counts:
        return ""

    sections = ""
    sections += _bar_section(tool_session_counts or {}, "高频工具 Top 10", top_n=10)
    sections += _bar_section(skill_freq_counts or {}, "技能频次 Top 8（调用次数）", top_n=8)
    sections += _bar_section(mcp_server_counts or {}, "MCP Server Top 8", top_n=8)
    sections += ('<div class="fine-note">广度看分布不看总数：头部断层＝你的工作流主轴，'
                 '长尾低频项可按需保留或卸载。</div>')
    return sections


def _render_token_details(token_usage: dict | None, token_total, items: list | None) -> str:
    """附录 A：Token 消耗（默认展开）。条形为 HTML 网格，按各模型 output 最大值归一。
    items 为 view_model.token_items 的产物（None = 全零，图降级为空但明细表照出）。"""
    if not token_usage or not isinstance(token_usage, dict):
        return ""
    items = items or []
    mx = max((o for _, o in items), default=0.0)
    bars = ""
    for name, out in items:
        w = (out / mx * 100.0) if mx else 0.0
        fill = ('background:linear-gradient(90deg,#22a3c4,#4640d9)' if w >= 3
                else 'background:#22a3c4')
        bars += (
            f'<span class="tok-name">{escape(name)}</span>'
            f'<div class="tok-track"><div class="tok-fill" style="width:{max(w, 0.3):.1f}%;{fill}"></div></div>'
            f'<span class="tok-val">{_fmt_tokens(out)}</span>'
        )
    tu_rows = ""
    for model_name, b in token_usage.items():
        b = b if isinstance(b, dict) else {}   # 桶不是对象（脏 metrics）→ 该行按空桶出
        tu_rows += (
            f'<tr><td class="tok-cell">{escape(str(model_name))}</td>'
            f'<td class="t-a">{_fmt_tokens(b.get("input"))}</td>'
            f'<td class="t-a">{_fmt_tokens(b.get("output"))}</td>'
            f'<td class="t-a">{_fmt_tokens(b.get("cache_read"))}</td>'
            f'<td class="t-a">{_fmt_tokens(b.get("cache_creation"))}</td></tr>'
        )
    total_row = (f'<tr><td class="tok-total">总计</td>'
                 f'<td colspan="4" class="tok-total t-a">{_fmt_tokens(token_total)}</td></tr>')
    return (
        '<details class="card appendix" open>'
        f'<summary>A · Token 消耗（含缓存读写，非计费口径，仅作量级参考 · 总计 {_fmt_tokens(token_total)}）</summary>'
        f'<div class="tok-grid">{bars}</div>'
        '<div class="fine-note tok-note">条形为各模型 output token</div>'
        '<table class="trend tok-table"><thead><tr><th>模型</th><th class="num-col">input</th>'
        '<th class="num-col">output</th><th class="num-col">cache_read</th>'
        '<th class="num-col">cache_creation</th></tr></thead>'
        f'<tbody>{tu_rows}{total_row}</tbody></table>'
        '</details>'
    )


def _encode_cwd(path: str) -> str:
    """复刻 CC 会话目录名编码（/ 与 . 等替换为 -），用于指针→项目名反查。"""
    return re.sub(r"[/.]", "-", str(path))


def _ptr_label(pointer, projects: list) -> str:
    """指针的脱敏标签：「项目N · 会话ID前8位」，匹配不到项目只出会话短 ID。

    绝不含真实项目名 / 绝对路径——可见文本与 title 同取此标签，悬停与查看源码均不泄露
    业务标识（项目目录名含产品/客户名，属敏感信息，快照同样剥离）。溯源由本地按序号另查。
    """
    path_part = str(pointer).split("#", 1)[0]
    stem = path_part.rsplit("/", 1)[-1]
    stem = stem[:-6] if stem.endswith(".jsonl") else stem
    sid = stem[:8]
    parent = path_part.rsplit("/", 2)[-2] if "/" in path_part else ""
    for i, p in enumerate(projects or []):
        if parent and parent == _encode_cwd(p):
            return f"项目{i + 1} · {sid}"
    return sid


def _ptr_chip(entry: dict, projects: list) -> str:
    """证据/高光指针胶囊：「项目N · 会话ID前8位 ↗」。

    title 与可见文本同为脱敏标签——绝不把绝对路径/真实项目名渲染进 HTML（含 title 悬停）；
    渲染前指针经规则层核验，未命中的明示警示而非装作可回看。
    """
    label = _ptr_label(entry.get("pointer", ""), projects)
    miss = ' <span class="ptr-miss">⚠ 指针未命中</span>' if entry.get("pointer_missing") else ""
    return (f'<span class="ptr-chip" title="{escape(label)}">{escape(label)} ↗</span>{miss}')


def _render_highlights_section(highlights: list | None, projects: list, idx: int) -> str:
    """高光时刻；空则返回空串。behavior/pointer 来自 LLM，escape。"""
    highlights = _obj_items(highlights)
    if not highlights:
        return ""
    rows = ""
    for i, h in enumerate(highlights):
        bg, fg = _HL_DOT[i % len(_HL_DOT)]
        last = " hl-last" if i == len(highlights) - 1 else ""
        miss = ' <span class="ptr-miss">⚠ 指针未命中</span>' if h.get("pointer_missing") else ""
        rows += (
            f'<div class="hl-row{last}">'
            f'<span class="hl-dot" style="background:{bg};color:{fg}">{i + 1}</span>'
            f'<span class="hl-text">{_hl_nums(h.get("behavior", ""), "#0e7490")}</span>'
            f'<span class="hl-link" title="{escape(_ptr_label(h.get("pointer", ""), projects))}">原会话 ↗</span>{miss}'
            '</div>'
        )
    return (_sec_header(idx, "高光时刻")
            + f'<div class="card hl-card">{rows}</div>')


def _render_capabilities_section(gaps_cap: list, idx: int) -> str:
    """能力盲区；gaps_cap 为 view_model 判定的未用能力列表。
    label/scene 为内置文案，仍统一 escape 求稳。"""
    if not gaps_cap:
        inner = ('<div class="cap-row"><span class="tag tag-ok">已覆盖</span>'
                 '<span>高杠杆能力全部用过 ✓</span></div>')
    else:
        inner = "".join(
            '<div class="cap-row"><span class="tag tag-unused">未使用</span>'
            f'<span><b class="ink">{escape(str(c.get("label", "")))}</b>'
            f' —— {escape(str(c.get("scene", "")))}</span></div>'
            for c in gaps_cap
        )
    return (_sec_header(idx, "能力盲区")
            + f'<div class="card cap-card">{inner}</div>')


# 与奖惩挂钩的硬指标信号名：这两类被虚高/量级污染时要额外点名人工复核
_硬指标信号 = {"edit", "gitop"}

# 未分支 kind 的兜底文案（**无方向**：既不说漏数也不说虚高，因为确实不知道）。
# 它只服务一种正当情形：缺 `kind` 的老 `_aggregate.json`。
# 抽成常量是为了给契约守卫一个哨兵——tests/test_skill_contract.py 断言「没有任何 kind
# 落到它」，从而挡住「新增 kind 只同步了 SKILL.md、漏同步渲染层」这条静默出错的路径。
# 措辞可以改，但别把它内联回字面量：内联即拆掉守卫。
_DRIFT_FALLBACK = "提取可能已失效"


def _num_txt(v) -> str:
    """中位数出数：3.0 → 「3」，3.5 → 「3.5」；非数值回退空串（调用方据此降级）。"""
    x = safe_num(v)      # NaN/inf 也归非数值：int(nan) 会抛，一条脏中位数不该炸整张报告
    if x is None:
        return ""
    return str(int(x)) if x == int(x) else f"{x:g}"


def _drift_flag_text(f: dict) -> str:
    """一条 drift flag 的结论文案（纯函数，不含 HTML、不含信号名）。

    必须按 `kind` 分方向——三类的结论正好不同，一律套 drop 文案等于对用户撒谎：
    - drop ：老段有、新段几乎消失 → 可能漏数，该维度偏低要打折看。
    - surge：老段绝迹、新段普遍   → 可能虚高（也可能是真的开始用了），方向与 drop 相反。
    - shift：存在率没变、每会话量级变了 → 印存在率毫无信息量（两端几乎相等），
             必须改印 older_median/newer_median/median_ratio 这个真证据。
    `kind` 缺失（旧 _aggregate.json）时回退 `_DRIFT_FALLBACK`，保持向后兼容。
    **新增 kind 必须在这里补显式分支**——落到兜底等于给用户一句没有方向的话；
    契约守卫按 `_DRIFT_FALLBACK` 哨兵检测漏分支，别指望「文案两两不同」兜得住。
    与 SKILL.md 第 5 步的三类措辞同口径，改一侧须改另一侧。
    """
    def _rate(key: str) -> str:
        """存在率文本：脏值→「—」（不伪造 0%），缺键沿用既有的 0 口径。"""
        n = safe_num(f.get(key, 0) or 0)
        return "—" if n is None else f"{n:.0%}"

    kind = f.get("kind")
    老 = f'老版本段 {_rate("older_rate")}'
    新 = f'新版本段 {_rate("newer_rate")}'
    硬 = "，与奖惩挂钩的硬指标口径可能失真，落地相关数字请人工复核" \
        if str(f.get("signal")) in _硬指标信号 else ""   # str()：脏 signal 可能不可哈希
    if kind == "surge":
        return (f"{老} → {新}，新版本段突然普遍出现，可能虚高"
                "（提取规则把一条记成多条，也可能是你真的开始用了）" + 硬)
    if kind == "shift":
        老中, 新中 = _num_txt(f.get("older_median")), _num_txt(f.get("newer_median"))
        倍 = _num_txt(f.get("median_ratio"))
        if 老中 and 新中:
            倍段 = f"（约 {safe_num(f['median_ratio']):.1f} 倍）" if 倍 else ""
            return (f"存在率没变但每会话中位数 {老中} → {新中}{倍段}，量级口径可能被污染" + 硬)
        return "存在率没变但每会话量级偏移，量级口径可能被污染" + 硬   # 中位数缺失时降级
    if kind == "drop":
        return f"{老} → {新}，新版本段几乎消失，可能漏数，该维度偏低要打折看"
    return f"{老} → {新}，{_DRIFT_FALLBACK}"


def _render_health_section(health: dict | None, idx: int) -> str:
    """数据健康（版本漂移雷达）：版本跨度 + 漂移红标 + 未知记录类型。
    health 缺失/为空/形态不对 → 返回空串（不占章节号）。"""
    if not health or not isinstance(health, dict):
        return ""
    span = health.get("cc_version_span")
    span = span if isinstance(span, dict) else {}
    flags = _obj_items(health.get("drift_flags"))
    _u = health.get("unknown_record_types") or []
    unknown = [u for u in _u if isinstance(u, str)] if isinstance(_u, (list, dict)) else []
    # parse_health 恒为非空骨架 dict，单凭 `if not health` 不足以省略空段：无版本信息、
    # 无漂移、无未知类型时整段无内容可言，按 docstring「为空→空串」不占章节号。
    if not span.get("min") and not flags and not unknown:
        return ""
    span_txt = (f'数据横跨 {_hl_nums(str(span.get("distinct", 0)), "#7c3aed")} 个 CC 版本'
                f'（{escape(str(span.get("min")))}–{escape(str(span.get("max")))}）'
                + ("，跨度内未见解析漂移" if not flags else "")
                if span.get("min") and span.get("max") else "版本信息缺失")
    body = f'<div class="health-span">{span_txt}</div>'
    if flags:
        rows = "".join(
            f'<div class="health-flag">⚠ 信号 <b>{escape(str(f.get("signal")))}</b> '
            f'疑似版本漂移：{escape(_drift_flag_text(f))}</div>'
            for f in flags)
        body += f'<div class="health-flags">{rows}</div>'
    if unknown:
        body += ('<div class="health-unknown">未编目的新记录类型（parser 暂不处理，留意是否承载新信号）：'
                 + escape("、".join(unknown)) + '</div>')
    return _sec_header(idx, "数据健康") + f'<div class="card health-card">{body}</div>'


def render_count_report(report: InsightsReport) -> str:
    rows = "".join(
        f"<tr><td>{escape(s.session_id[:8])}</td><td>{escape(s.cwd)}</td>"
        f"<td>{s.turn_count}</td><td>{s.short_turn_ratio:.0%}</td></tr>"
        for s in report.sessions
    )
    return f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>AI Coding Insights · 数量版</title></head><body>
<h1>AI Coding Insights · 数量版</h1>
<p>生成 {escape(report.generated_at)}｜回看 {report.lookback_days} 天｜纳入 {len(report.sessions)} 会话｜项目 {len(report.included_projects)} 个</p>
<table border="1" cellpadding="4"><thead><tr><th>会话</th><th>项目(cwd)</th><th>轮次</th><th>极短占比</th></tr></thead>
<tbody>{rows}</tbody></table>
</body></html>"""


def _fmt_local(iso: str) -> str:
    """ISO UTC 串转本机时区的 %Y-%m-%d %H:%M；解析失败回退原串（escape）。"""
    from datetime import datetime
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%Y-%m-%d %H:%M")
    except Exception:
        return escape(str(iso))


def format_run_meta(run: dict | None, generated_at: str) -> str:
    """运行元信息行：「本报告由 X 生成 · 运行约 N 分钟 · 编排 N 个 agent」。

    model 的唯一写入方是 cli 的确定性识别（从当前 CC 会话 transcript 读 CC 写入的
    model 字段），可信；编排端 LLM 自报的模型 ID 不收（实测会编造）。
    各段独立可缺省：耗时由 started_at 与 generated_at 求差取整分钟（至少 1），
    解析失败或时间倒挂只丢耗时段；全部缺省返回空串。返回纯文本，转义由渲染端负责。
    """
    from datetime import datetime
    run = run if isinstance(run, dict) else {}
    parts = []
    if run.get("model"):
        parts.append(f"由 {run['model']} 生成")
    if run.get("started_at"):
        try:
            secs = (datetime.fromisoformat(str(generated_at))
                    - datetime.fromisoformat(str(run["started_at"]))).total_seconds()
            if secs >= 0:
                parts.append(f"运行约 {max(1, round(secs / 60))} 分钟")
        except (ValueError, TypeError):
            pass
    agents = run.get("agents")
    if isinstance(agents, int) and agents > 0:
        parts.append(f"编排 {agents} 个 agent")
    return "本报告" + " · ".join(parts) if parts else ""


def _fmt_window(w: dict | None, lookback_fallback: int) -> str:
    """取数窗口短语：首次基线 / 取数区间；进横幅 kicker。"""
    if not w or not isinstance(w, dict):
        return f"近 {lookback_fallback} 天"
    # 天数取不到数（脏 _window.json）→ 退到调用方给的兜底天数，不炸整张报告
    if w.get("status") == "first":
        d0 = safe_int(w.get("lookback_days", 30))
        return f"首次基线 · 近 {lookback_fallback if d0 is None else d0} 天"
    s, u, d = w.get("since_date"), w.get("until_date"), safe_int(w.get("lookback_days"))
    return (f"取数 {escape(str(s))} → {escape(str(u))}（{d} 天）"
            if s and u and d is not None else f"近 {lookback_fallback} 天")


def _fmt_truncation(w: dict | None) -> str:
    """窗口被本机清理截断时（window.truncated 为真）渲染一枚醒目警示胶囊。

    文案：实际数据起点 <data_start 日期> · 更早记录已被本机清理。
    无 truncated 键 / 为假 / 缺 data_start 时返回空串（旧 _window.json 兼容）。
    """
    if not w or not isinstance(w, dict) or not w.get("truncated"):
        return ""
    ds = w.get("data_start")
    if not ds:
        return ""
    day = escape(str(ds)[:10])  # 只取日期部分
    return (f'<span class="pill-warn">实际数据起点 {day} · '
            f'更早记录已被本机清理</span>')


def _fmt_delta(d: dict) -> str:
    """把 diff 单键 {now,prev,delta,arrow} 渲染成带色的小箭头+变化量。

    delta 是数值（落地率为小数，其余为整数），已格式化故安全；不嵌任何外部串。
    无基线（no_base 或 arrow 为 None）时不渲染箭头，返回空串，避免崩在 abs(None)。
    """
    if d.get("no_base") or d.get("arrow") is None:
        return ""
    arrow = d.get("arrow", "→")
    delta = safe_num(d.get("delta", 0))
    # 脏 delta（非数）/ 脏 arrow（非字符串，还会把 dict 直接拼进 HTML）：
    # 只是不出这枚箭头，不炸整张报告
    if delta is None or not isinstance(arrow, str):
        return ""
    color = _ARROW_COLORS.get(arrow, "#667085")
    mag_val = abs(delta)
    # 按数值（而非类型）区分整数与小数：整数（含 6.0）显示整数，
    # 非整数小数（如 0.046）显示两位小数，避免比率 delta 被截成误导的 0。
    if float(mag_val).is_integer():
        mag = f"{int(round(mag_val))}"
    else:
        mag = f"{mag_val:.2f}"
    sign = "" if arrow == "→" else mag
    return f'<span class="delta" style="color:{color}">{escape(arrow)}{sign}</span>'


def _lead_rest(text: str) -> tuple:
    """「导语 —— 展开」拆分约定的单一定义：按首个全角破折号拆两段并 strip；
    无分隔符时 rest 为 None。只拆不转义，HTML 结构由各调用方自行组装。"""
    s = str(text or "")
    if "——" in s:
        lead, rest = s.split("——", 1)
        return lead.strip(), rest.strip()
    return s, None


def _split_lead(text: str) -> str:
    """观察/分点文案的「加粗导语 —— 展开」呈现；无分隔符整句普通字重。
    LLM 文本，两段均 escape。"""
    lead, rest = _lead_rest(text)
    if rest is None:
        return escape(lead)
    return f'<b class="ink">{escape(lead)}</b> —— {escape(rest)}'


# 独立数字 token：整数/带千分逗号/单一小数/可带尾 %；前后不得紧邻 ASCII 字母数字或 . - / : _
# （从而跳过版本号 2.1.141、日期 2026-06-14、标识符 opus-4-8、档位码 L3 内的数字）。
# 界用 [A-Za-z0-9_] 而非 \w：\w 在 Python 下含 CJK，会把「43种」这类紧贴中文的数字
# 误当标识符内部而漏掉高亮——中文不是单词边界，紧贴中文的量级数字应与带空格写法一致高亮。
_NUM_RE = re.compile(r'(?<![A-Za-z0-9_./:-])(\d+(?:,\d{3})*(?:\.\d+)?%?)(?![A-Za-z0-9_./:-])')


def _hl_nums(raw, color: str) -> str:
    """转义后把独立数字 token 包成主题色等宽 span（.n）。只着色既有数字，不生成数字。"""
    safe = escape(str(raw or ""))
    return _NUM_RE.sub(
        lambda mo: f'<span class="n" style="color:{color}">{mo.group(1)}</span>', safe)


def _dim_points_rows(points: list, color: str) -> str:
    """维度卡分点：每条「洞察导语 —— 展开」。导语正常墨色+数字主题色高亮，展开走淡灰。"""
    rows = ""
    # points 是 LLM 产物：不是列表就按无分点渲染（_lead_rest 内部 str() 兜住单项脏值）
    pts = points if isinstance(points, list) else []
    for i, p in enumerate(pts):
        lead, rest = _lead_rest(p)
        last = " pt-last" if i == len(pts) - 1 else ""
        desc = f' <span class="pt-desc dim2">{_hl_nums(rest, color)}</span>' if rest is not None else ""
        rows += f'<div class="pt-row{last}"><div class="pt-line">{_hl_nums(lead, color)}{desc}</div></div>'
    return rows


def _dim_card(dim: str, title: str, block: dict, extra_rows: str = "") -> str:
    """维度详述卡（水平/成果）：色标 + 标题 + headline 副题 + 分点列表。"""
    head = escape(str(block.get("headline") or block.get("summary") or ""))
    rows = _dim_points_rows(block.get("points") or [], _DIM_COLORS[dim])
    sub = f'<div class="dim-card-sub">{head}</div>' if head else ""
    return (
        '<div class="card dim-card">'
        f'<div class="dim-card-head"><span class="dim-swatch" style="background:{_DIM_COLORS[dim]}"></span>'
        f'<span class="dim-card-title">{escape(title)}</span></div>'
        f'{sub}<div class="pt-list">{rows}{extra_rows}</div>'
        '</div>'
    )


def _depth_card(block: dict) -> str:
    """深度卡（通栏）：色标 + headline 副题 + 分点子卡网格（浅灰底）。"""
    head = escape(str(block.get("headline") or block.get("summary") or ""))
    color = _DIM_COLORS["深度"]
    cells = ""
    _pts = block.get("points")
    for p in (_pts if isinstance(_pts, list) else []):
        lead, rest = _lead_rest(p)
        desc = f'<div class="depth-desc dim2">{_hl_nums(rest, color)}</div>' if rest is not None else ""
        cells += f'<div class="depth-cell"><div class="pt-line">{_hl_nums(lead, color)}</div>{desc}</div>'
    sub = f'<div class="dim-card-sub depth-sub">{head}</div>' if head else ""
    return (
        '<div class="card depth-card">'
        f'<div class="dim-card-head"><span class="dim-swatch" style="background:{_DIM_COLORS["深度"]}"></span>'
        '<span class="dim-card-title">深度 · 多轮打磨</span></div>'
        f'{sub}<div class="depth-grid">{cells}</div>'
        '</div>'
    )


# ════ 页面样式表（纯排版常量：只描述像素，不含任何数据派生）════
# 与顶部「样式约定」注释一一对应；作为普通字符串抽出，让渲染函数只剩装配。
_CSS = """*{box-sizing:border-box;margin:0;padding:0}
html{-webkit-text-size-adjust:100%}
body{font-family:system-ui,-apple-system,'PingFang SC','Segoe UI',sans-serif;
  color:#344054;background:#f3f5fa;min-height:100vh;line-height:1.5}
b{font-weight:700}
.ink{color:#1b2440}
.dim2{color:#9aa3b2}
.n{font-family:ui-monospace,'SF Mono',Menlo,monospace;font-weight:600;font-variant-numeric:tabular-nums}
.mono{font-family:ui-monospace,'SF Mono',Menlo,monospace}
/* ---- 横幅 ---- */
.hero{background:
  radial-gradient(620px 300px at 92% -60px,rgba(34,211,238,.16),transparent 70%),
  radial-gradient(520px 280px at 4% 120%,rgba(139,92,246,.18),transparent 70%),
  linear-gradient(120deg,#0b1026 0%,#18204a 100%);
  color:#fff;padding:36px 40px 32px}
.hero-inner{max-width:960px;margin:0 auto}
.hero-top{display:flex;align-items:baseline;justify-content:space-between;gap:16px;flex-wrap:wrap}
.kicker{font-size:14px;font-weight:600;color:#9aa6c8;letter-spacing:1px}
.hero-meta{font-family:ui-monospace,'SF Mono',Menlo,monospace;font-size:12px;color:#8893b8}
.hero-bottom{display:flex;align-items:flex-end;justify-content:space-between;gap:24px;
  flex-wrap:wrap;margin-top:22px}
.stage-big{font-size:38px;font-weight:700;letter-spacing:-.5px;line-height:1.1}
.stage-sub{font-size:13px;color:#9aa6c8;margin-top:9px}
.hero-nums{display:flex;gap:30px}
.hnum{font-size:25px;font-weight:700;font-variant-numeric:tabular-nums}
.hlbl{font-size:11.5px;color:#9aa6c8;margin-top:2px}
.pill-warn{display:inline-block;font-size:11px;font-weight:700;color:#7c2d12;
  background:#fdeac2;border:1px solid #f0c674;border-radius:999px;
  padding:2px 10px;margin-top:10px;white-space:nowrap}
.keyline{height:3px;background:linear-gradient(90deg,#22d3ee,#6366f1 50%,#a78bfa)}
/* ---- 主体 ---- */
.main{max-width:960px;margin:0 auto;padding:34px 40px 64px}
.sec{display:flex;align-items:baseline;gap:10px;margin:30px 0 12px}
.sec-first{margin-top:0}
.sec-num{font-family:ui-monospace,'SF Mono',Menlo,monospace;font-size:12px;font-weight:700}
.sec-title{font-size:15px;font-weight:700;color:#101828}
.sec-hint{font-size:12.5px;color:#667085}
.card{background:linear-gradient(180deg,#fff,#fcfdff);border:1px solid #ebeef6;border-radius:14px;box-shadow:0 1px 2px rgba(20,28,52,.04),0 6px 16px rgba(20,28,52,.05)}
.card-title{font-size:13px;font-weight:700;color:#101828}
.fine-note{font-size:11.5px;color:#667085;line-height:1.6;margin-top:10px}
.sec-note{margin-top:10px}
.tag{font-size:11px;font-weight:700;border-radius:5px;padding:2px 8px;
  height:fit-content;white-space:nowrap;flex:0 0 auto}
.tag-advice{color:#b45309;background:#fdeac2}
.tag-unused{color:#6d28d9;background:#ede7fc;transform:translateY(2px)}
.tag-ok{color:#15803d;background:#dcfce7;transform:translateY(2px)}
/* ---- metrics ---- */
.fam-card{padding:4px 22px}
.fam{padding:16px 0;border-bottom:1px solid #eef0f5}
.fam-last{border-bottom:none}
.fam-head{display:flex;align-items:center;gap:7px;font-size:12px;font-weight:700;
  margin-bottom:12px}
.fam-swatch{width:8px;height:8px;border-radius:3px;flex:0 0 auto}
.m-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
.m-num{font-size:24px;font-weight:700;font-variant-numeric:tabular-nums;letter-spacing:-.5px}
.m-num .unit{font-size:15px;color:#667085}
.m-lbl{font-size:12px;color:#667085;margin-top:2px}
.delta{font-weight:700;font-size:.95em;font-family:ui-monospace,'SF Mono',Menlo,monospace;
  font-variant-numeric:tabular-nums}
/* ---- highlights ---- */
.hl-card{padding:8px 22px}
.hl-row{display:flex;align-items:baseline;gap:12px;padding:13px 0;border-bottom:1px solid #eef0f5}
.hl-last{border-bottom:none}
.hl-dot{flex:0 0 auto;width:22px;height:22px;border-radius:50%;font-size:11px;font-weight:700;
  display:inline-flex;align-items:center;justify-content:center;transform:translateY(4px)}
.hl-text{font-size:13.5px;color:#54607a;line-height:1.65;flex:1}
.hl-link{font-size:12px;color:#0e7490;white-space:nowrap;font-weight:500;cursor:help}
.ptr-miss{color:#b45309;font-size:11px;font-weight:700;white-space:nowrap}
/* ---- posture ---- */
.posture-full{padding:24px 26px}
.stack-big{display:flex;width:100%;height:44px;border-radius:9px;overflow:hidden;font-size:13px;font-weight:700;margin-top:6px}
.bseg{display:flex;align-items:center;justify-content:center;min-width:0;overflow:hidden;white-space:nowrap}
.lg-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px 28px;margin-top:16px;font-size:12.5px;color:#54607a;line-height:1.5}
.crit-cols{display:flex;gap:30px;margin-top:20px;padding-top:16px;border-top:1px solid #eef0f5;flex-wrap:wrap}
.crit-col{flex:1;min-width:200px}
.crit-cap{font-size:11px;font-weight:700;letter-spacing:.5px;margin-bottom:8px}
.crit-cap-ok{color:#15803d}
.crit-cap-miss{color:#b42318}
.lg-row{display:flex;gap:8px}
.lg-swatch{flex:0 0 auto;width:10px;height:10px;border-radius:3px;transform:translateY(4px)}
.crit-list{display:grid;gap:10px;font-size:13px;color:#475467}
.crit-row{display:flex;justify-content:space-between;gap:10px}
.crit-ok{color:#15803d;font-weight:700;font-variant-numeric:tabular-nums}
.crit-miss{color:#b42318;font-weight:700;font-variant-numeric:tabular-nums}
.crit-na{color:#667085}
/* ---- dimensions ---- */
.radar-card{padding:22px;display:grid;grid-template-columns:320px 1fr;gap:8px 26px;
  align-items:center}
.radar{margin:0 auto;display:block}
.dim-rows{display:grid;align-content:center}
.dim-row{display:grid;grid-template-columns:46px 120px 1fr;gap:14px;align-items:baseline;
  padding:12px 0;border-bottom:1px solid #eef0f5}
.dim-last{border-bottom:none}
.dim-name{font-size:12px;font-weight:700}
.dim-val{font-size:20px;font-weight:700;color:#101828;font-variant-numeric:tabular-nums;
  letter-spacing:-.4px}
.dim-unit{font-size:11.5px;font-weight:500;color:#667085;margin-left:5px}
.dim-desc{font-size:12.5px;color:#54607a;line-height:1.55}
.dim-cards{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px}
.dim-card{padding:18px 22px 14px}
.dim-card-head{display:flex;align-items:baseline;gap:8px}
.dim-swatch{width:8px;height:8px;border-radius:3px;flex:0 0 auto;transform:translateY(-1px)}
.dim-card-title{font-size:13.5px;font-weight:700;color:#101828}
.dim-card-sub{font-size:12.5px;color:#667085;margin:4px 0 6px 16px}
.pt-list{display:grid}
.pt-row{padding:10px 0;border-bottom:1px solid #eef0f5}
.pt-last{border-bottom:none}
.pt-line{font-size:13px;color:#54607a;line-height:1.85}
.pt-desc{font-size:12.5px;line-height:1.55;margin-top:2px}
.depth-card{padding:18px 22px 22px;margin-top:16px}
.depth-sub{margin-bottom:14px}
.depth-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.depth-cell{background:#f8f9fc;border-radius:10px;padding:14px 16px}
.depth-desc{font-size:12.5px;line-height:1.65;margin-top:4px}
/* ---- friction ---- */
.fr-list{display:grid;gap:12px}
.fr-card{padding:18px 22px}
.fr-obs{font-size:13.5px;color:#344054;line-height:1.7}
.fr-ptrs{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}
/* ---- timeline ---- */
.tl-card{padding:20px 22px 16px}
.tl-wrap{display:flex;align-items:flex-end;gap:3px;height:120px;border-bottom:1px solid #e1e5ef;margin-top:8px}
.tl-bar{flex:1;border-radius:3px 3px 0 0;position:relative;min-height:2px}
.tl-val{position:absolute;top:-16px;left:50%;transform:translateX(-50%);font-size:10px;font-family:ui-monospace,Menlo,monospace;color:#1a6b5a;font-weight:700}
.tl-axis{display:flex;justify-content:space-between;font-size:11px;color:#8a93a8;margin-top:6px}
/* ---- 工具/技能/MCP 附录 ---- */
.tok-block{margin-bottom:8px}
.tok-block summary{cursor:pointer;font-size:13px;font-weight:600;color:#475467;padding:4px 0;list-style:none}
.tok-block summary::-webkit-details-marker{display:none}
.tok-chart{margin-top:8px;display:grid;gap:5px}
.tok-row{display:grid;grid-template-columns:160px 1fr 48px;gap:9px 12px;align-items:center;font-size:12.5px}
.tok-label{font-family:ui-monospace,'SF Mono',Menlo,monospace;color:#54607a;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tok-bar-wrap{height:9px;border-radius:4px;background:#eef1f6;overflow:hidden}
.tok-bar{display:block;height:100%;border-radius:4px;background:linear-gradient(90deg,#6e8ef2,#4f46e5)}
.tok-val{font-family:ui-monospace,'SF Mono',Menlo,monospace;color:#1b2440;font-weight:600;text-align:right;font-variant-numeric:tabular-nums}
.fr-box{display:flex;gap:10px;margin-top:10px;background:#fffaeb;border:1px solid #fdeac2;
  border-radius:8px;padding:10px 14px}
.fr-sug{font-size:13px;color:#57534e;line-height:1.7}
/* ---- capabilities ---- */
.cap-card{padding:18px 22px;display:grid;gap:10px}
.cap-row{display:flex;gap:12px;font-size:13.5px;color:#344054;line-height:1.7}
/* ---- health ---- */
.health-card{padding:16px 20px;font-size:13px;line-height:1.7}
.health-span{color:#54607a}
.health-flag{margin-top:6px;padding:8px 12px;background:#fef2f2;border-left:3px solid #dc2626;border-radius:4px;color:#991b1b}
.health-unknown{margin-top:8px;color:#667085}
/* ---- trend ---- */
.trend-card{padding:8px 22px 16px}
table.trend{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
table.trend th{padding:12px 8px 9px;text-align:left;font-size:12px;color:#667085;
  font-weight:600;border-bottom:1px solid #e1e5ef}
table.trend th:first-child{padding-left:0}
table.trend th.num-col{text-align:right}
table.trend th.dir-col{text-align:center;padding-right:0}
table.trend td{padding:10px 8px;font-size:13px;border-bottom:1px solid #eef0f5}
table.trend td:first-child{padding-left:0}
table.trend tbody tr:last-child td{border-bottom:none}
.t-name{color:#344054}
.t-a{color:#475467;text-align:right}
.t-b{color:#101828;font-weight:600;text-align:right}
.t-dir{font-weight:700;text-align:center;padding-right:0}
/* ---- 附录 ---- */
.appendix{padding:16px 22px;margin-bottom:12px}
.appendix summary{cursor:pointer;font-size:13.5px;font-weight:700;color:#0e7490;list-style:none}
.appendix summary::-webkit-details-marker{display:none}
.tok-grid{display:grid;grid-template-columns:auto 1fr auto;gap:9px 14px;align-items:center;
  margin-top:18px;font-size:12.5px}
.tok-name{font-family:ui-monospace,'SF Mono',Menlo,monospace;color:#475467}
.tok-track{height:12px;border-radius:4px;background:#eef1f6;overflow:hidden}
.tok-fill{height:100%;border-radius:4px}
.tok-note{margin-top:8px}
.tok-table{margin-top:14px}
.tok-table td{font-size:12.5px}
.tok-cell{font-family:ui-monospace,'SF Mono',Menlo,monospace;color:#344054}
.tok-total{font-weight:700;color:#101828}
.ev-list{display:grid;margin-top:10px}
.ev-row{display:flex;align-items:baseline;gap:14px;padding:11px 0;border-bottom:1px solid #eef0f5}
.ev-last{border-bottom:none}
.ev-text{font-size:13px;color:#344054;line-height:1.65;flex:1}
.ptr-chip{font-family:ui-monospace,'SF Mono',Menlo,monospace;font-size:11px;color:#0e7490;
  background:#ecf9fc;border:1px solid #c9ecf4;border-radius:5px;padding:2px 8px;
  white-space:nowrap;cursor:help}
/* ---- 页脚 ---- */
.footer{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-top:34px;
  padding-top:16px;border-top:1px solid #e1e5ef;font-size:11.5px;color:#667085}
.footer-id{font-family:ui-monospace,'SF Mono',Menlo,monospace}
/* ---- 窄屏 / 打印 ---- */
@media (max-width:720px){
  .hero,.main{padding-left:20px;padding-right:20px}
  .radar-card,.dim-cards,.depth-grid,.lg-grid{grid-template-columns:1fr}
  .m-grid{grid-template-columns:repeat(2,1fr)}
  .hero-nums{gap:20px;flex-wrap:wrap}
  /* 窄屏：固定大像素列会撑破容器/触发横滚——首列可缩 */
  .tok-row{grid-template-columns:minmax(80px,140px) 1fr 44px;gap:8px}
}
@media print{
  .hero{-webkit-print-color-adjust:exact;print-color-adjust:exact}
}
"""


def render_profile_report(profile: dict, meta: dict,
                          metrics: dict | None = None,
                          diff: dict | None = None) -> str:
    """渲染画像 HTML：先由 view_model.build_view 算出全部派生数据与判定，
    本函数只做 dict → HTML 拼装（转义在此层的插值点统一做，view 内不含 HTML）。

    需要 posture_state / stage_name 的调用方（如 cli 打 stdout 接缝）直接调
    build_view 取，不再靠出参回填——build_view 是纯函数，两处取值必然一致。"""
    view = build_view(profile, meta, metrics, diff)
    # 与 build_view 同口径的形态兜底：三份都是外部 JSON，整份形态错了按「这份没有」走
    profile = profile if isinstance(profile, dict) else {}
    meta = meta if isinstance(meta, dict) else {}
    m = metrics if isinstance(metrics, dict) else {}

    # ---- 横幅四数 = 四维代表值 ----
    hero_nums_html = "".join(
        f'<div><div class="hnum" style="color:{c}">{escape(h["value"])}</div>'
        f'<div class="hlbl">{escape(h["label"])}</div></div>'
        # strict：配色与四数一一对应，view 若增删了数就该在这里炸出来，不静默少画
        for c, h in zip(_HERO_COLORS, view["hero_nums"], strict=True)
    )

    # ---- 指标明细：四族，构成与取值由 view_model 定，这里只配色排版 ----
    fams = view["families"]
    fam_html = ""
    for fi, fam in enumerate(fams):
        fcolor, ftext = _FAM_COLORS[fam["name"]]
        last = " fam-last" if fi == len(fams) - 1 else ""
        cell_html = ""
        for ci, c in enumerate(fam["cells"]):
            vcolor = ftext if ci == 0 else "#101828"
            value = escape(c["value"])
            if c["unit"]:
                value += f'<span class="unit">{escape(c["unit"])}</span>'
            delta = _fmt_delta(c["diff"]) if c["diff"] else ""
            d = f" {delta}" if delta else ""
            cell_html += (f'<div><div class="m-num" style="color:{vcolor}">{value}</div>'
                          f'<div class="m-lbl">{escape(c["label"])}{d}</div></div>')
        fam_html += (
            f'<div class="fam{last}">'
            f'<div class="fam-head" style="color:{ftext}">'
            f'<span class="fam-swatch" style="background:{fcolor}"></span>'
            f'{escape(fam["name"])}</div>'
            f'<div class="m-grid">{cell_html}</div></div>'
        )

    # ---- 姿势分布 + 档位判据 ----
    legend_html = "".join(
        f'<div class="lg-row"><span class="lg-swatch" style="background:{_POSTURE_COLORS[code]}"></span>'
        f'<span><b class="ink">{code} {name} {view["posture_pct_text"][code]}</b> · {desc}</span></div>'
        for code, name, desc in _LEGEND_ITEMS
    )
    stage = view["stage"]
    # 大堆叠条：段宽/标签取舍已在 view_model 定，这里只贴色（段内文字色按档位身份定）。
    big_segs = "".join(
        f'<span class="bseg" style="width:{s["width_pct"]:.2f}%;'
        f'background:{_POSTURE_COLORS[s["code"]]};color:{_BSEG_INK[s["code"]]}" '
        f'title="{s["title"]}">{s["label"]}</span>'
        for s in view["posture_segments"]
    )
    crit_html = _render_stage_criteria_inline(stage) if stage is not None else ""
    posture_sec_title = "姿势分布与档位判据" if stage is not None else "姿势分布"
    posture_section_body = (
        '<div class="card posture-full">'
        '<div class="card-title">姿势分布（主导性）</div>'
        f'<div class="stack-big">{big_segs}</div>'
        f'<div class="lg-grid">{legend_html}</div>'
        f'{crit_html}'
        '<div class="fine-note">四档由 LLM 对每条真人输入逐条语义分档、规则层聚合组装；'
        'AskUserQuestion 选项回答按协议硬信号计入 L2。'
        'L4 健康带约 5-20%，过高表示过度对抗、过低引导力不足；档位由绝对用量硬指标判定，与姿态分布解耦。</div>'
        '</div>'
    )

    # ---- 四维雷达 + 维度详述（轴归一与代表值口径均出自 view_model）----
    radar_svg = _render_radar(view["radar_axes"], view["radar_labels"])

    # 维度块必须是对象才拿得到 headline/points；LLM 写成字符串等形态时按空块渲染，
    # 少一段文字好过整张报告崩掉（校验层通常已拦下，这里是渲染侧的兜底）。
    outcome, breadth, depth = (
        profile.get(k) if isinstance(profile.get(k), dict) else {}
        for k in ("outcome", "breadth", "depth"))
    landed_disp, dropped_disp = view["landed_disp"], view["dropped_disp"]
    dim_rows = view["dim_rows"]
    dim_rows_html = ""
    for i, row in enumerate(dim_rows):
        name = row["name"]
        last = " dim-last" if i == len(dim_rows) - 1 else ""
        # desc 在此单次转义（_hl_nums 内部已 escape），view 层不得预转义
        dim_rows_html += (
            f'<div class="dim-row{last}">'
            f'<span class="dim-name" style="color:{_DIM_COLORS[name]}">{name}</span>'
            f'<span class="dim-val n" style="color:{_DIM_COLORS[name]}">{escape(row["value"])}'
            f'<span class="dim-unit">{escape(row["unit"])}</span></span>'
            f'<span class="dim-desc">{_hl_nums(row["desc"], _DIM_COLORS[name])}</span></div>'
        )
    radar_panel = (
        '<div class="card radar-card">'
        f'{radar_svg}'
        f'<div class="dim-rows">{dim_rows_html}</div>'
        '</div>'
    )
    dim_cards = (
        '<div class="dim-cards">'
        + _dim_card("水平", "水平 · 工具广度", breadth)
        + _dim_card("成果", "成果 · 落地", outcome,
                    extra_rows=(f'<div class="pt-row pt-last"><div class="pt-line">'
                                f'{_hl_nums(f"落地 {landed_disp} · 观测丢弃 {dropped_disp}", _DIM_COLORS["成果"])}'
                                f'</div></div>'
                                '<div class="fine-note">落地率 = 改动文件命中 AI 编辑的提交'
                                ' ÷ 窗口同仓本人提交总数（同口径）。</div>'))
        + '</div>'
        + _depth_card(depth)
    )

    # ---- 摩擦 + 建议 ----
    projects = view["projects"]
    frictions = _obj_items(profile.get("frictions"))
    fr_items = ""
    for f in frictions:
        ptr_chips = "".join(_ptr_chip(p, projects) for p in _obj_items(f.get("pointers")))
        ptr_html = f'<div class="fr-ptrs">{ptr_chips}</div>' if ptr_chips else ""
        fr_items += (
            '<div class="card fr-card">'
            f'<div class="fr-obs">{_split_lead(f.get("observation", ""))}</div>'
            f'{ptr_html}'
            '<div class="fr-box"><span class="tag tag-advice">建议</span>'
            f'<span class="fr-sug">{escape(str(f.get("suggestion", "")))}</span></div>'
            '</div>'
        )

    # ---- 附录 B 证据链（默认折叠）----
    evidence = _obj_items(profile.get("evidence"))
    ev_rows = ""
    for i, e in enumerate(evidence):
        last = " ev-last" if i == len(evidence) - 1 else ""
        ev_rows += (
            f'<div class="ev-row{last}">'
            f'<span class="ev-text">{escape(str(e.get("behavior", "")))}</span>'
            f'{_ptr_chip(e, projects)}</div>'
        )
    evidence_block = (
        '<details class="card appendix">'
        f'<summary>B · 证据链（{len(evidence)} 条 · 悬停指针查看原会话路径）</summary>'
        f'<div class="ev-list">{ev_rows}</div>'
        '</details>'
    )

    # ---- 横幅文案 ----
    window = meta.get("window")
    window_label = _fmt_window(window, safe_int(meta.get("lookback_days", 30)) or 30)
    kicker = " · ".join(x for x in ["AI 驾驭力评估", window_label, view["scope_label"]] if x)
    trunc_pill = _fmt_truncation(window)
    gen_local = _fmt_local(str(meta.get("generated_at", "")))
    hero_meta = (f"{escape(gen_local[:10])} · {view['session_count']} 会话"
                 f" · {len(projects)} 项目")
    if stage is not None:
        stage_big = f'第 {view["stage_no"]} 档 · {escape(view["stage_name"])}'
        crit_note = view["stage_crit_note"]
    else:
        stage_big, crit_note = "AI 协作画像", ""
    if view["diff_note_kind"] == "none":
        diff_note = ""
    elif view["diff_note_kind"] == "baseline":
        diff_note = "首次基线，暂无同比"
    else:
        # view.diff_summary 已剔掉无基线项（_fmt_delta 对它们返回空串），此处只排版
        parts = [f"{escape(lname)} {_fmt_delta(d)}" for lname, d in view["diff_summary"]]
        diff_note = ("较上次：" + " · ".join(parts)) if parts else ""
    stage_sub = " · ".join(x for x in [crit_note, diff_note, "右侧为四维各自的代表值"] if x)

    # ---- 章节按出场顺序连续编号（空板块跳过不占号）----
    sections: list[str] = []
    idx = 1
    # 01 指标明细
    sections.append(_sec_header(idx, "指标明细", "横幅四数为四维代表值，此处不再重复",
                                margin_top=False)
                    + f'<div class="card fam-card">{fam_html}</div>')
    # 02 姿势分布 + 判据
    idx += 1
    sections.append(_sec_header(idx, posture_sec_title) + posture_section_body)
    # 03 四维画像与维度详述
    idx += 1
    sections.append(
        _sec_header(idx, "四维画像与维度详述") + radar_panel + dim_cards
        + '<div class="fine-note sec-note">维度详述、摩擦建议与证据描述的文字由 LLM 解读生成；'
        '数字以指标卡与表格的硬指标为准。</div>')
    # 04 摩擦 + 建议
    if fr_items:
        idx += 1
        sections.append(_sec_header(idx, "摩擦 + 建议") + f'<div class="fr-list">{fr_items}</div>')
    # 05 高光时刻
    hl_html = _render_highlights_section(profile.get("highlights"), projects, idx + 1)
    if hl_html:
        idx += 1
        sections.append(hl_html)
    # 06 活动热力
    timeline_html = _render_daily_timeline(m.get("daily"), idx + 1)
    if timeline_html:
        idx += 1
        sections.append(timeline_html)
    # 07 窗口内趋势
    trend_html = _render_trend_section(view["trend"], idx + 1)
    if trend_html:
        idx += 1
        sections.append(trend_html)
    # 08 能力盲区：与可空段统一用「idx+1 偷看、命中才提交」。capabilities 当前恒非空
    # （盲区列表为空时有「已覆盖✓」兜底），占号行为与旧版逐字节一致；
    # 改成同构只为消掉「这一段特殊」的记忆负担——未来若改成可早退空串也不会烧号留洞。
    if view["capability_gaps"] is not None:
        cap_html = _render_capabilities_section(view["capability_gaps"], idx + 1)
        if cap_html:
            idx += 1
            sections.append(cap_html)
    # 09 数据健康
    health_html = _render_health_section(m.get("parse_health"), idx + 1)
    if health_html:
        idx += 1
        sections.append(health_html)

    # ---- 附录（不编号）----
    token_block = _render_token_details(m.get("token_usage"), m.get("token_total"),
                                        view["token_items"])
    tsm_appendix = _render_tool_skill_mcp_appendix(
        m.get("tool_session_counts"), m.get("skill_total_counts"), m.get("mcp_server_counts"))
    appendix = (
        '<div class="sec"><span class="sec-num" style="color:#667085">附录</span>'
        '<span class="sec-title">明细数据</span>'
        '<span class="sec-hint">默认折叠，点开查看</span></div>'
        + token_block + tsm_appendix + evidence_block
    )

    # ---- 页脚 ----
    run_line = format_run_meta(meta.get("run"), str(meta.get("generated_at", "")))
    footer = (
        '<div class="footer">'
        + (f'<span>{escape(run_line)}</span>' if run_line else "<span></span>")
        + f'<span class="footer-id">aci-report · {escape(gen_local)}</span>'
        '</div>'
    )

    body_sections = "".join(sections)
    return f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI 驾驭力评估报告</title>
<style>
{_CSS}</style>
</head><body>
<div class="hero">
<div class="hero-inner">
<div class="hero-top">
<div class="kicker">{kicker}</div>
<div class="hero-meta">{hero_meta}</div>
</div>
<div class="hero-bottom">
<div>
<div class="stage-big">{stage_big}</div>
<div class="stage-sub">{stage_sub}</div>
{trunc_pill}
</div>
<div class="hero-nums">{hero_nums_html}</div>
</div>
</div>
</div>
<div class="keyline"></div>
<div class="main">
{body_sections}
{appendix}
{footer}
</div>
</body></html>"""


def _render_radar(values: list[float], labels: list[str],
                  cx: float = 160.0, cy: float = 160.0, R: float = 110.0) -> str:
    """四轴雷达 SVG：轴角从正上方起每 90°。values/labels 各 4 项，values∈[0,1]。

    纯坐标计算，无 JS、无外部资源。文字经 escape。
    """
    def point(v: float, i: int) -> tuple[float, float]:
        rad = math.radians(-90 + 90 * i)
        return (cx + R * v * math.cos(rad), cy + R * v * math.sin(rad))

    # 外框（v=1 轴端连线）作网格
    frame_pts = [point(1.0, i) for i in range(4)]
    frame = " ".join(f"{x:.1f},{y:.1f}" for x, y in frame_pts)
    # 半幅网格
    mid_pts = [point(0.5, i) for i in range(4)]
    mid = " ".join(f"{x:.1f},{y:.1f}" for x, y in mid_pts)
    # 轴线（中心到端点）
    axes = "".join(
        f'<line x1="{cx:.1f}" y1="{cy:.1f}" x2="{x:.1f}" y2="{y:.1f}" stroke="rgba(16,24,40,.12)" stroke-width="1"/>'
        for x, y in frame_pts
    )
    # 数据多边形
    data_pts = [point(max(0.0, min(1.0, v)), i) for i, v in enumerate(values)]
    data = " ".join(f"{x:.1f},{y:.1f}" for x, y in data_pts)
    # 轴标签（端点外侧）
    label_html = ""
    for i, lab in enumerate(labels):
        lx, ly = point(1.18, i)
        anchor = "middle"
        if i == 1:
            anchor = "start"
        elif i == 3:
            anchor = "end"
        label_html += (
            f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anchor}" '
            f'font-size="13" fill="#475467">{escape(lab)}</text>'
        )
    # 数据顶点小圆点
    dots = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="#0891b2"/>'
        for x, y in data_pts
    )
    return (
        f'<svg class="radar" width="300" height="300" viewBox="0 0 320 320" '
        f'role="img" aria-label="四维画像雷达图">'
        f'<polygon points="{frame}" fill="none" stroke="rgba(16,24,40,.12)" stroke-width="1"/>'
        f'<polygon points="{mid}" fill="none" stroke="rgba(16,24,40,.08)" stroke-width="1"/>'
        f'{axes}'
        f'<polygon points="{data}" fill="rgba(70,64,217,0.10)" '
        f'stroke="#4640d9" stroke-width="2.5" stroke-linejoin="round"/>'
        f'{dots}'
        f'{label_html}'
        f'</svg>'
    )
