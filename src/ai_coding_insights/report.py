import math
import re
from html import escape

from .models import InsightsReport
from .stage import decide_stage, diagnose_posture, normalize_posture
from .capabilities import unused_capabilities

# 雷达图满刻度（与 stage.py 阶段阈值无关，仅控制可视化拉伸）：
_RADAR_BREADTH_FULL = 35.0      # 工具广度 35 种打满（≈内置高杠杆能力全集的量级上限）
_RADAR_DEPTH_FULL_TURNS = 20.0  # P90 轮次 20 打满（取 P90 后上限上浮，微会话不再拉低）

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
# 高光序号圆点配色（按条目循环）
_HL_DOT = [("#d7f3fa", "#0e7490"), ("#e3e6fd", "#4338ca"), ("#ede7fc", "#6d28d9")]
# 维度色（详述行 + 卡片角标）
_DIM_COLORS = {"姿势": "#0891b2", "水平": "#4f46e5", "深度": "#7c3aed", "成果": "#0d9488"}


def _fmt_tokens(n) -> str:
    n = int(n or 0)
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


def _trend_arrow(a: float, b: float) -> str:
    if b > a:
        return "↑"
    if b < a:
        return "↓"
    return "→"


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


def _render_trend_section(trend: dict | None, idx: int) -> str:
    """窗口内趋势对比表；trend 为 None / 空则返回空串。值均为硬指标数字，自生成无需 escape。"""
    if not trend:
        return ""
    fh = trend.get("first_half", {}) or {}
    sh = trend.get("second_half", {}) or {}
    fh_n = int(fh.get("sessions") or 0)
    sh_n = int(sh.get("sessions") or 0)
    rows_html = ""
    for name, key, kind in _TREND_ROWS:
        if key in ("commits", "landed_ratio") and not (
                _fnum(fh.get("commits")) or _fnum(sh.get("commits"))):
            # trend 的提交数据来自 transcript 口径：两半全 0 多半是不可观测
            # （如旧版 CC 无 gitOperation 回执），是测不到不是没提交，0% 假行不出。
            continue
        unobserv = False
        if kind == "ratio":
            a_o, b_o = fh.get(key), sh.get(key)
            a, b = _fnum(a_o), _fnum(b_o)
            # 某半 commits=0 → landed_ratio 为 None（不可观测）：记「—」，不画 0% 假值/假箭头
            unobserv = a_o is None or b_o is None
            a_txt = "—" if a_o is None else f"{a:.0%}"
            b_txt = "—" if b_o is None else f"{b:.0%}"
        else:
            a_raw, b_raw = _fnum(fh.get(key)), _fnum(sh.get(key))
            a = a_raw / fh_n if fh_n else 0.0
            b = b_raw / sh_n if sh_n else 0.0
            name = f"{name}（次/会话）"
            a_txt, b_txt = f"{a:.2f}", f"{b:.2f}"
        arrow = "" if unobserv else _trend_arrow(a, b)
        arrow_color = {"↑": "#4f46e5", "↓": "#0e7490"}.get(arrow, "#667085")
        rows_html += (
            f'<tr><td class="t-name">{name}</td><td class="t-a">{a_txt}</td>'
            f'<td class="t-b">{b_txt}</td>'
            f'<td class="t-dir" style="color:{arrow_color}">{arrow}</td></tr>'
        )
    return (
        _sec_header(idx, "窗口内趋势")
        + '<div class="card trend-card">'
        f'<table class="trend"><thead><tr><th>指标</th>'
        f'<th class="num-col">前半段（{fh_n} 会话）</th>'
        f'<th class="num-col">后半段（{sh_n} 会话）</th>'
        '<th class="dir-col">方向</th></tr></thead>'
        f'<tbody>{rows_html}</tbody></table>'
        '<div class="fine-note">前后半按窗口内实际数据的时间中点切分；计数类指标已按每会话密度归一。'
        '落地率为会话内提交观测口径（与头部 git 主锚口径不同），某半提交不可观测时记「—」。'
        '箭头只示变化方向，不评判好坏。</div>'
        '</div>'
    )


def _timeline_bars(daily) -> list:
    """时间线柱：每项 {date, count, height_pct, color}。按会话数分 4 档配色，峰值满高。"""
    rows = []
    for d in (daily or []):
        if isinstance(d, dict) and isinstance(d.get("date"), str) and d["date"]:
            rows.append((d["date"], int(d.get("session_count", 0) or 0)))
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


def _render_daily_timeline(daily, idx: int) -> str:
    """活动热力 → 时间线柱状：横轴日期、纵轴会话数。空则空串（不占章节号）。"""
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


def _bar_items(counts: dict, top_n: int = 15) -> tuple[list, float]:
    """降序取 Top N 项 + 最大值。返回 ([(name, count), ...], mx)。counts 为空返回 ([], 0.0)。"""
    if not counts:
        return [], 0.0
    items = sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:top_n]  # 同计数按名排序，避免跨进程顺序不定
    mx = items[0][1] if items else 1
    return items, float(mx)


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
    return sections


def _render_token_details(token_usage: dict | None, token_total) -> str:
    """附录 A：Token 消耗（默认展开）。条形为 HTML 网格，按各模型 output 最大值归一。"""
    if not token_usage:
        return ""
    items = _token_items(token_usage) or []
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
        b = b or {}
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
    highlights = highlights or []
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


def _render_capabilities_section(tool_session_counts: dict | None, idx: int,
                                  customization_signals: dict | None = None,
                                  metrics: dict | None = None) -> str:
    """能力盲区；label/scene 为内置文案，仍统一 escape 求稳。"""
    gaps_cap = unused_capabilities(tool_session_counts or {},
                                    customization_signals=customization_signals,
                                    metrics=metrics)
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


def _render_health_section(health: dict | None, idx: int) -> str:
    """数据健康（版本漂移雷达）：版本跨度 + 漂移红标 + 未知记录类型。
    health 缺失/为空 → 返回空串（不占章节号）。"""
    if not health:
        return ""
    span = health.get("cc_version_span") or {}
    flags = health.get("drift_flags") or []
    unknown = health.get("unknown_record_types") or []
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
            f'疑似版本漂移：老版本段 {float(f.get("older_rate",0)):.0%} → '
            f'新版本段 {float(f.get("newer_rate",0)):.0%}，提取可能已失效</div>'
            for f in flags)
        body += f'<div class="health-flags">{rows}</div>'
    if unknown:
        body += ('<div class="health-unknown">未编目的新记录类型（parser 暂不处理，留意是否承载新信号）：'
                 + escape("、".join(unknown)) + '</div>')
    return _sec_header(idx, "数据健康") + f'<div class="card health-card">{body}</div>'


def _fnum(v) -> float:
    """容错取数：None/非数→0.0。趋势/Token 条形图共用。"""
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _token_items(token_usage: dict | None) -> list[tuple[str, float]] | None:
    """token_usage → 按 output 降序的 (模型名, output) 列表；空/全零返回 None（图整体降级）。"""
    if not token_usage:
        return None
    items = [(str(name), _fnum((b or {}).get("output"))) for name, b in token_usage.items()]
    if not items or max(o for _, o in items) <= 0:
        return None
    items.sort(key=lambda t: (-t[1], t[0]))  # 同 output 按模型名排序，保证可复现顺序
    return items


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
    run = run or {}
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
    if not w:
        return f"近 {lookback_fallback} 天"
    if w.get("status") == "first":
        return f"首次基线 · 近 {int(w.get('lookback_days', 30))} 天"
    s, u, d = w.get("since_date"), w.get("until_date"), w.get("lookback_days")
    return (f"取数 {escape(str(s))} → {escape(str(u))}（{int(d)} 天）"
            if s and u else f"近 {lookback_fallback} 天")


def _fmt_truncation(w: dict | None) -> str:
    """窗口被本机清理截断时（window.truncated 为真）渲染一枚醒目警示胶囊。

    文案：实际数据起点 <data_start 日期> · 更早记录已被本机清理。
    无 truncated 键 / 为假 / 缺 data_start 时返回空串（旧 _window.json 兼容）。
    """
    if not w or not w.get("truncated"):
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
    delta = d.get("delta", 0)
    # 方向中性呈现，不评判好坏：↑ 靛蓝，↓ 深青，→ 弱化灰（与趋势表同口径）。
    color = {"↑": "#4f46e5", "↓": "#0e7490"}.get(arrow, "#667085")
    mag_val = abs(delta)
    # 按数值（而非类型）区分整数与小数：整数（含 6.0）显示整数，
    # 非整数小数（如 0.046）显示两位小数，避免比率 delta 被截成误导的 0。
    if float(mag_val).is_integer():
        mag = f"{int(round(mag_val))}"
    else:
        mag = f"{mag_val:.2f}"
    sign = "" if arrow == "→" else mag
    return f'<span class="delta" style="color:{color}">{arrow}{sign}</span>'


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
    pts = points or []
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
    for p in (block.get("points") or []):
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


def render_profile_report(profile: dict, meta: dict,
                          metrics: dict | None = None,
                          diff: dict | None = None) -> str:
    # posture_distribution 由规则层组装注入（assemble_posture，恒 0-1 比例，
    # 和为 1 或全零）；此处归一是对手喂 dict 的防御性兜底，无百分数形态的正常来源。
    pd = normalize_posture(profile.get("posture_distribution", {}) or {})

    def pct(key: str) -> float:
        return pd.get(key, 0.0)

    outcome = profile.get("outcome", {}) or {}
    try:
        o_landed = float(outcome.get("landed", 0) or 0)
    except (TypeError, ValueError):
        o_landed = 0.0
    try:
        o_total = float(outcome.get("total", 0) or 0)
    except (TypeError, ValueError):
        o_total = 0.0
    o_ratio = (o_landed / o_total) if o_total else 0.0

    m = metrics or {}

    def mval(key, fallback=None):
        v = m.get(key, None)
        return v if v is not None else fallback

    # ---- 核心指标取值（metrics 缺失时按要求兜底到 outcome，再无则 None→"—"）----
    # 成果类数字统一「硬指标优先、LLM outcome 兜底」：落地数/提交数与奖励挂钩，
    # 必须以可独立验证的 metrics 为准，LLM 转抄值只作缺数时的降级显示。
    landed_ratio = mval("landed_ratio", o_ratio if o_total else None)
    edit_count = mval("edit_count", None)
    # git 主锚口径：落地数取 git_landed_count。降级链：旧口径 metrics（缺 git 键，
    # 如旧 _aggregate）退到 transcript 硬证据（landed_count 经 HEAD 验证，是 git 落地
    # 的下界）；metrics 整体缺席才用 LLM 抄值（profile.outcome 的 landed/total 已是
    # 新语义：landed=git 落地、total=落地+观测丢弃）。
    git_landed = mval("git_landed_count",
                      mval("landed_count", o_landed if o_total else None))
    # 落地率分母：窗口内同仓本人提交总数（与 git_landed 同口径）。
    _gct = mval("git_commit_total")
    git_commit_total = None if _gct is None else int(_gct)
    _cc, _lc = mval("commit_count"), mval("landed_count")

    def _dropped_fallback():
        # 硬证据兜底 commit-landed；非数值（脏/漂移 metrics）时退到 LLM outcome，不炸整张报告
        if _cc is not None and _lc is not None:
            try:
                return max(0, int(_cc) - int(_lc))
            except (TypeError, ValueError):
                pass
        return (o_total - o_landed) if o_total else None
    dropped = mval("dropped_count", _dropped_fallback())

    def num(v):
        return "—" if v is None else escape(str(v))

    def pct0(v):
        return "—" if v is None else f"{float(v):.0%}"

    posture_diag = (None if metrics is None else diagnose_posture(
        pd, m.get("decision_point_count", 0),
        m.get("plan_mode_sessions", 0), m.get("thinking_sessions", 0)))

    def diff_html(key: str) -> str:
        if isinstance(diff, dict) and key in diff and isinstance(diff[key], dict):
            return _fmt_delta(diff[key])
        return ""

    def dur(v):
        """时长中位数：None→「—」，有值→「N + min 单位」。"""
        if v is None:
            return "—"
        try:
            return f'{round(float(v))}<span class="unit">min</span>'
        except (TypeError, ValueError):
            return "—"

    # ---- 横幅四数 = 四维代表值 ----
    tp90 = mval("turn_p90")
    hero_nums = [
        ("#67e8f9", pct0(landed_ratio), "成果 · 落地率"),
        ("#a5b4fc", escape(posture_diag["state"]) if posture_diag else "—", "姿态健康"),
        ("#5eead4", num(mval("tool_breadth")), "水平 · 工具广度"),
        ("#fcd34d", num(tp90), "深度 · P90 轮次/会话"),
    ]
    hero_nums_html = "".join(
        f'<div><div class="hnum" style="color:{c}">{v}</div>'
        f'<div class="hlbl">{escape(l)}</div></div>'
        for c, v, l in hero_nums
    )

    # ---- 指标明细：三族，不重复横幅四数 ----
    # 不渲染「编辑/落地」派生比率：edit_count 是全会话编辑量、git_landed 是 git 锚落地数，
    # 跨口径相除（分子分母分属不同总体）无 per-commit 语义。只陈列两个原值。
    token_usage = m.get("token_usage") or {}
    model_count = num(len(token_usage) if token_usage else None)
    families = [
        ("产出落地", "#0d9488", "#0f766e", [
            ("落地提交", num(None if git_landed is None else int(git_landed)),
             diff_html("git_landed_count")),
            ("提交总数", num(git_commit_total),
             diff_html("git_commit_total")),
            ("观测丢弃", num(None if dropped is None else int(dropped)),
             diff_html("dropped_count")),
            ("编辑数", num(edit_count), diff_html("edit_count")),
        ]),
        ("协作编排", "#4f46e5", "#4338ca", [
            ("SubAgent 会话", num(mval("subagent_sessions")), diff_html("subagent_sessions")),
            ("Workflow 会话", num(mval("workflow_sessions")), diff_html("workflow_sessions")),
            ("MCP 会话", num(mval("mcp_sessions")), diff_html("mcp_sessions")),
            ("使用模型数", model_count, ""),
        ]),
        # 高阶行为：三个维度信号均为确定性硬指标（深度推理块 / 后台委托 / 真并行），
        # 由规则层从 transcript 直接计数，不依赖 LLM 判定。真并行峰值=1、轮次=0 表示
        # 「用过子代理但总是顺序派发、从未单轮并发」——是准确信号而非缺数。
        ("高阶行为", "#0891b2", "#0e7490", [
            ("深度推理", num(mval("thinking_block_count")), ""),
            ("后台委托", num(mval("background_task_count")), ""),
            ("真并行峰值", num(mval("max_parallel_agents")), ""),
            ("真并行轮次", num(mval("parallel_agent_turns")), ""),
        ]),
        ("节奏投入", "#7c3aed", "#6d28d9", [
            ("会话数", num(mval("session_count")), diff_html("session_count")),
            ("有效输入", num(mval("human_input_count")), diff_html("human_input_count")),
            ("活跃天数", num(mval("active_days")), diff_html("active_days")),
            ("时长 P90", dur(mval("duration_p90_min")), ""),
        ]),
    ]
    fam_html = ""
    for fi, (fname, fcolor, ftext, cells) in enumerate(families):
        last = " fam-last" if fi == len(families) - 1 else ""
        cell_html = ""
        for ci, (label, value, delta) in enumerate(cells):
            vcolor = ftext if ci == 0 else "#101828"
            d = f" {delta}" if delta else ""
            cell_html += (f'<div><div class="m-num" style="color:{vcolor}">{value}</div>'
                          f'<div class="m-lbl">{escape(label)}{d}</div></div>')
        fam_html += (
            f'<div class="fam{last}">'
            f'<div class="fam-head" style="color:{ftext}">'
            f'<span class="fam-swatch" style="background:{fcolor}"></span>{escape(fname)}</div>'
            f'<div class="m-grid">{cell_html}</div></div>'
        )

    # ---- 姿势分布 + 档位判据 ----
    total_pd = sum(pct(t) for t in ("L1", "L2", "L3", "L4")) or 1.0
    legend_html = "".join(
        f'<div class="lg-row"><span class="lg-swatch" style="background:{_POSTURE_COLORS[code]}"></span>'
        f'<span><b class="ink">{code} {name} {pct(code):.0%}</b> · {desc}</span></div>'
        for code, name, desc in _LEGEND_ITEMS
    )
    # 阶段判定只算一次：横幅大字 / 判据卡共用同一结果
    stage = None if metrics is None else decide_stage(m)
    # 大堆叠条：段内嵌百分比；宽度公式照搬旧 segs（pct(t)/total_pd*100），只是放大+内嵌文字。
    # 段内文字色按档位身份定（非 DOM 位置）：L1-L3 浅/中底用深字，仅 L4 深底用白字
    _BSEG_INK = {"L1": "#0e3a4a", "L2": "#0e3a4a", "L3": "#0e3a4a", "L4": "#ffffff"}
    # 段宽占比低于此阈值放不下「Lx NN%」标签：硬塞会被 min-content 撑出真实宽度、
    # 文字溢进邻段又被 .stack-big 的 overflow:hidden 裁成半字，故窄段留纯色块
    # （占比在下方 lg-grid 图例完整呈现，hover title 仍带 Lx NN%，信息不丢）。
    _BSEG_LABEL_MIN = 0.08
    seg_parts = []
    for t in ("L1", "L2", "L3", "L4"):
        frac = pct(t) / total_pd
        if frac <= 0:
            continue
        label = f"{t} {pct(t):.0%}" if frac >= _BSEG_LABEL_MIN else ""
        seg_parts.append(
            f'<span class="bseg" style="width:{frac*100:.2f}%;'
            f'background:{_POSTURE_COLORS[t]};color:{_BSEG_INK[t]}" '
            f'title="{t} {pct(t):.0%}">{label}</span>'
        )
    big_segs = "".join(seg_parts)
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

    # ---- 四维雷达 + 维度详述 ----
    axis_posture = max(0.0, min(1.0, pct("L3") + pct("L4")))
    tb = mval("tool_breadth")
    axis_breadth = min(float(tb) / _RADAR_BREADTH_FULL, 1.0) if tb is not None else 0.0
    tp90 = mval("turn_p90")
    axis_depth = min(float(tp90) / _RADAR_DEPTH_FULL_TURNS, 1.0) if tp90 is not None else 0.0
    if landed_ratio is not None:
        axis_outcome = max(0.0, min(1.0, float(landed_ratio)))
    elif o_total:
        axis_outcome = max(0.0, min(1.0, o_ratio))
    else:
        axis_outcome = 0.0
    radar_svg = _render_radar(
        [axis_posture, axis_breadth, axis_depth, axis_outcome],
        ["姿势", "水平", "深度", "成果"],
    )

    breadth = profile.get("breadth", {}) or {}
    depth = profile.get("depth", {}) or {}

    def _headline(block: dict) -> str:
        # 返回原文，不在此预转义：唯一消费者是雷达 dim_rows 的 desc，统一过 _hl_nums
        # （单次 escape）。此处再 escape 会与 _hl_nums 叠加成双重转义。
        return str(block.get("headline") or block.get("summary") or "")

    # 成果代表行附「落地 X · 观测丢弃 Y」（git 主锚口径）。与横幅同源：硬指标优先。
    landed_disp = "—" if git_landed is None else f"{int(git_landed)}"
    dropped_disp = "—" if dropped is None else f"{int(dropped)}"
    outcome_desc = f"落地 {landed_disp} · 观测丢弃 {dropped_disp}"
    if _headline(outcome):
        outcome_desc = f"{_headline(outcome)} · {outcome_desc}"
    dim_rows = [
        ("姿势", escape(posture_diag["state"]) if posture_diag else "—", "姿态",
         (posture_diag["reason"] if posture_diag
          else f"L3+L4 合计 {axis_posture:.0%}")),
        ("水平", num(tb), "种工具", _headline(breadth)),
        ("深度", num(tp90), "P90 轮/会话", _headline(depth)),
        ("成果", pct0(landed_ratio), "落地率", outcome_desc),
    ]
    dim_rows_html = ""
    for i, (name, value, unit, desc) in enumerate(dim_rows):
        last = " dim-last" if i == len(dim_rows) - 1 else ""
        dim_rows_html += (
            f'<div class="dim-row{last}">'
            f'<span class="dim-name" style="color:{_DIM_COLORS[name]}">{name}</span>'
            f'<span class="dim-val n" style="color:{_DIM_COLORS[name]}">{value}'
            f'<span class="dim-unit">{escape(unit)}</span></span>'
            f'<span class="dim-desc">{_hl_nums(desc, _DIM_COLORS[name])}</span></div>'
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
    projects = meta.get("included_projects", []) or []
    frictions = profile.get("frictions", []) or []
    fr_items = ""
    for f in frictions:
        ptr_chips = "".join(_ptr_chip(p, projects) for p in (f.get("pointers") or [])
                            if isinstance(p, dict))
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
    evidence = profile.get("evidence", []) or []
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
    window_label = _fmt_window(window, int(meta.get("lookback_days", 30) or 30))
    scope_label = {
        "all": "个人模式（全部本机会话）",
        "include": "团队模式（仅配置纳入的项目）",
    }.get((window or {}).get("mode"), "")
    kicker = " · ".join(x for x in ["AI 驾驭力评估", window_label, scope_label] if x)
    trunc_pill = _fmt_truncation(window)
    gen_local = _fmt_local(str(meta.get("generated_at", "")))
    hero_meta = (f"{escape(gen_local[:10])} · {int(meta.get('session_count', 0) or 0)} 会话"
                 f" · {len(projects)} 项目")
    if stage is not None:
        stage_big = f'第 {int(stage.get("stage", 1))} 档 · {escape(str(stage.get("name", "")))}'
        n_crit = len(stage.get("criteria") or [])
        gaps = stage.get("gaps") or []
        crit_note = (f"{_cn_num(n_crit)}项判据全部达标" if not gaps
                     else f"距下一档还差 {len(gaps)} 项判据")
    else:
        stage_big, crit_note = "AI 协作画像", ""
    if diff is None:
        diff_note = ""
    elif diff.get("baseline"):
        diff_note = "首次基线，暂无同比"
    else:
        labels = {"landed_ratio": "落地率", "git_landed_count": "落地提交",
                  "git_commit_total": "提交总数",
                  "dropped_count": "观测丢弃", "commit_count": "会话内提交",
                  "edit_count": "编辑数", "session_count": "会话数",
                  "human_input_count": "有效输入", "tool_breadth": "工具广度",
                  "active_days": "活跃天数"}
        parts = []
        for k, lname in labels.items():
            d = diff.get(k)
            if isinstance(d, dict):
                delta = _fmt_delta(d)
                # 无基线（no_base / arrow=None）→ _fmt_delta 返回空串，跳过该项
                if delta:
                    parts.append(f"{escape(lname)} {delta}")
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
    trend_html = _render_trend_section(m.get("trend"), idx + 1)
    if trend_html:
        idx += 1
        sections.append(trend_html)
    # 08 能力盲区：与可空段统一用「idx+1 偷看、命中才提交」。capabilities 当前恒非空
    # （unused_capabilities 为空时有「已覆盖✓」兜底），占号行为与旧版逐字节一致；
    # 改成同构只为消掉「这一段特殊」的记忆负担——未来若改成可早退空串也不会烧号留洞。
    if metrics is not None:
        cap_html = _render_capabilities_section(m.get("tool_session_counts"), idx + 1,
                                                customization_signals=m.get("customization_signals"),
                                                metrics=m)
        if cap_html:
            idx += 1
            sections.append(cap_html)
    # 09 数据健康
    health_html = _render_health_section(m.get("parse_health"), idx + 1)
    if health_html:
        idx += 1
        sections.append(health_html)

    # ---- 附录（不编号）----
    token_block = _render_token_details(m.get("token_usage"), m.get("token_total"))
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
*{{box-sizing:border-box;margin:0;padding:0}}
html{{-webkit-text-size-adjust:100%}}
body{{font-family:system-ui,-apple-system,'PingFang SC','Segoe UI',sans-serif;
  color:#344054;background:#f3f5fa;min-height:100vh;line-height:1.5}}
b{{font-weight:700}}
.ink{{color:#1b2440}}
.dim2{{color:#9aa3b2}}
.n{{font-family:ui-monospace,'SF Mono',Menlo,monospace;font-weight:600;font-variant-numeric:tabular-nums}}
.mono{{font-family:ui-monospace,'SF Mono',Menlo,monospace}}
/* ---- 横幅 ---- */
.hero{{background:
  radial-gradient(620px 300px at 92% -60px,rgba(34,211,238,.16),transparent 70%),
  radial-gradient(520px 280px at 4% 120%,rgba(139,92,246,.18),transparent 70%),
  linear-gradient(120deg,#0b1026 0%,#18204a 100%);
  color:#fff;padding:36px 40px 32px}}
.hero-inner{{max-width:960px;margin:0 auto}}
.hero-top{{display:flex;align-items:baseline;justify-content:space-between;gap:16px;flex-wrap:wrap}}
.kicker{{font-size:14px;font-weight:600;color:#9aa6c8;letter-spacing:1px}}
.hero-meta{{font-family:ui-monospace,'SF Mono',Menlo,monospace;font-size:12px;color:#8893b8}}
.hero-bottom{{display:flex;align-items:flex-end;justify-content:space-between;gap:24px;
  flex-wrap:wrap;margin-top:22px}}
.stage-big{{font-size:38px;font-weight:700;letter-spacing:-.5px;line-height:1.1}}
.stage-sub{{font-size:13px;color:#9aa6c8;margin-top:9px}}
.hero-nums{{display:flex;gap:30px}}
.hnum{{font-size:25px;font-weight:700;font-variant-numeric:tabular-nums}}
.hlbl{{font-size:11.5px;color:#9aa6c8;margin-top:2px}}
.pill-warn{{display:inline-block;font-size:11px;font-weight:700;color:#7c2d12;
  background:#fdeac2;border:1px solid #f0c674;border-radius:999px;
  padding:2px 10px;margin-top:10px;white-space:nowrap}}
.keyline{{height:3px;background:linear-gradient(90deg,#22d3ee,#6366f1 50%,#a78bfa)}}
/* ---- 主体 ---- */
.main{{max-width:960px;margin:0 auto;padding:34px 40px 64px}}
.sec{{display:flex;align-items:baseline;gap:10px;margin:30px 0 12px}}
.sec-first{{margin-top:0}}
.sec-num{{font-family:ui-monospace,'SF Mono',Menlo,monospace;font-size:12px;font-weight:700}}
.sec-title{{font-size:15px;font-weight:700;color:#101828}}
.sec-hint{{font-size:12.5px;color:#667085}}
.card{{background:linear-gradient(180deg,#fff,#fcfdff);border:1px solid #ebeef6;border-radius:14px;box-shadow:0 1px 2px rgba(20,28,52,.04),0 6px 16px rgba(20,28,52,.05)}}
.card-title{{font-size:13px;font-weight:700;color:#101828}}
.fine-note{{font-size:11.5px;color:#667085;line-height:1.6;margin-top:10px}}
.sec-note{{margin-top:10px}}
.tag{{font-size:11px;font-weight:700;border-radius:5px;padding:2px 8px;
  height:fit-content;white-space:nowrap;flex:0 0 auto}}
.tag-advice{{color:#b45309;background:#fdeac2}}
.tag-unused{{color:#6d28d9;background:#ede7fc;transform:translateY(2px)}}
.tag-ok{{color:#15803d;background:#dcfce7;transform:translateY(2px)}}
/* ---- metrics ---- */
.fam-card{{padding:4px 22px}}
.fam{{padding:16px 0;border-bottom:1px solid #eef0f5}}
.fam-last{{border-bottom:none}}
.fam-head{{display:flex;align-items:center;gap:7px;font-size:12px;font-weight:700;
  margin-bottom:12px}}
.fam-swatch{{width:8px;height:8px;border-radius:3px;flex:0 0 auto}}
.m-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}
.m-num{{font-size:24px;font-weight:700;font-variant-numeric:tabular-nums;letter-spacing:-.5px}}
.m-num .unit{{font-size:15px;color:#667085}}
.m-lbl{{font-size:12px;color:#667085;margin-top:2px}}
.delta{{font-weight:700;font-size:.95em;font-family:ui-monospace,'SF Mono',Menlo,monospace;
  font-variant-numeric:tabular-nums}}
/* ---- highlights ---- */
.hl-card{{padding:8px 22px}}
.hl-row{{display:flex;align-items:baseline;gap:12px;padding:13px 0;border-bottom:1px solid #eef0f5}}
.hl-last{{border-bottom:none}}
.hl-dot{{flex:0 0 auto;width:22px;height:22px;border-radius:50%;font-size:11px;font-weight:700;
  display:inline-flex;align-items:center;justify-content:center;transform:translateY(4px)}}
.hl-text{{font-size:13.5px;color:#54607a;line-height:1.65;flex:1}}
.hl-link{{font-size:12px;color:#0e7490;white-space:nowrap;font-weight:500;cursor:help}}
.ptr-miss{{color:#b45309;font-size:11px;font-weight:700;white-space:nowrap}}
/* ---- posture ---- */
.posture-full{{padding:24px 26px}}
.stack-big{{display:flex;width:100%;height:44px;border-radius:9px;overflow:hidden;font-size:13px;font-weight:700;margin-top:6px}}
.bseg{{display:flex;align-items:center;justify-content:center;min-width:0;overflow:hidden;white-space:nowrap}}
.lg-grid{{display:grid;grid-template-columns:1fr 1fr;gap:8px 28px;margin-top:16px;font-size:12.5px;color:#54607a;line-height:1.5}}
.crit-cols{{display:flex;gap:30px;margin-top:20px;padding-top:16px;border-top:1px solid #eef0f5;flex-wrap:wrap}}
.crit-col{{flex:1;min-width:200px}}
.crit-cap{{font-size:11px;font-weight:700;letter-spacing:.5px;margin-bottom:8px}}
.crit-cap-ok{{color:#15803d}}
.crit-cap-miss{{color:#b42318}}
.lg-row{{display:flex;gap:8px}}
.lg-swatch{{flex:0 0 auto;width:10px;height:10px;border-radius:3px;transform:translateY(4px)}}
.crit-list{{display:grid;gap:10px;font-size:13px;color:#475467}}
.crit-row{{display:flex;justify-content:space-between;gap:10px}}
.crit-ok{{color:#15803d;font-weight:700;font-variant-numeric:tabular-nums}}
.crit-miss{{color:#b42318;font-weight:700;font-variant-numeric:tabular-nums}}
.crit-na{{color:#667085}}
/* ---- dimensions ---- */
.radar-card{{padding:22px;display:grid;grid-template-columns:320px 1fr;gap:8px 26px;
  align-items:center}}
.radar{{margin:0 auto;display:block}}
.dim-rows{{display:grid;align-content:center}}
.dim-row{{display:grid;grid-template-columns:46px 120px 1fr;gap:14px;align-items:baseline;
  padding:12px 0;border-bottom:1px solid #eef0f5}}
.dim-last{{border-bottom:none}}
.dim-name{{font-size:12px;font-weight:700}}
.dim-val{{font-size:20px;font-weight:700;color:#101828;font-variant-numeric:tabular-nums;
  letter-spacing:-.4px}}
.dim-unit{{font-size:11.5px;font-weight:500;color:#667085;margin-left:5px}}
.dim-desc{{font-size:12.5px;color:#54607a;line-height:1.55}}
.dim-cards{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px}}
.dim-card{{padding:18px 22px 14px}}
.dim-card-head{{display:flex;align-items:baseline;gap:8px}}
.dim-swatch{{width:8px;height:8px;border-radius:3px;flex:0 0 auto;transform:translateY(-1px)}}
.dim-card-title{{font-size:13.5px;font-weight:700;color:#101828}}
.dim-card-sub{{font-size:12.5px;color:#667085;margin:4px 0 6px 16px}}
.pt-list{{display:grid}}
.pt-row{{padding:10px 0;border-bottom:1px solid #eef0f5}}
.pt-last{{border-bottom:none}}
.pt-line{{font-size:13px;color:#54607a;line-height:1.85}}
.pt-desc{{font-size:12.5px;line-height:1.55;margin-top:2px}}
.depth-card{{padding:18px 22px 22px;margin-top:16px}}
.depth-sub{{margin-bottom:14px}}
.depth-grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
.depth-cell{{background:#f8f9fc;border-radius:10px;padding:14px 16px}}
.depth-desc{{font-size:12.5px;line-height:1.65;margin-top:4px}}
/* ---- friction ---- */
.fr-list{{display:grid;gap:12px}}
.fr-card{{padding:18px 22px}}
.fr-obs{{font-size:13.5px;color:#344054;line-height:1.7}}
.fr-ptrs{{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}}
/* ---- timeline ---- */
.tl-card{{padding:20px 22px 16px}}
.tl-wrap{{display:flex;align-items:flex-end;gap:3px;height:120px;border-bottom:1px solid #e1e5ef;margin-top:8px}}
.tl-bar{{flex:1;border-radius:3px 3px 0 0;position:relative;min-height:2px}}
.tl-val{{position:absolute;top:-16px;left:50%;transform:translateX(-50%);font-size:10px;font-family:ui-monospace,Menlo,monospace;color:#1a6b5a;font-weight:700}}
.tl-axis{{display:flex;justify-content:space-between;font-size:11px;color:#8a93a8;margin-top:6px}}
/* ---- 工具/技能/MCP 附录 ---- */
.tok-block{{margin-bottom:8px}}
.tok-block summary{{cursor:pointer;font-size:13px;font-weight:600;color:#475467;padding:4px 0;list-style:none}}
.tok-block summary::-webkit-details-marker{{display:none}}
.tok-chart{{margin-top:8px;display:grid;gap:5px}}
.tok-row{{display:grid;grid-template-columns:160px 1fr 48px;gap:9px 12px;align-items:center;font-size:12.5px}}
.tok-label{{font-family:ui-monospace,'SF Mono',Menlo,monospace;color:#54607a;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.tok-bar-wrap{{height:9px;border-radius:4px;background:#eef1f6;overflow:hidden}}
.tok-bar{{display:block;height:100%;border-radius:4px;background:linear-gradient(90deg,#6e8ef2,#4f46e5)}}
.tok-val{{font-family:ui-monospace,'SF Mono',Menlo,monospace;color:#1b2440;font-weight:600;text-align:right;font-variant-numeric:tabular-nums}}
.fr-box{{display:flex;gap:10px;margin-top:10px;background:#fffaeb;border:1px solid #fdeac2;
  border-radius:8px;padding:10px 14px}}
.fr-sug{{font-size:13px;color:#57534e;line-height:1.7}}
/* ---- capabilities ---- */
.cap-card{{padding:18px 22px;display:grid;gap:10px}}
.cap-row{{display:flex;gap:12px;font-size:13.5px;color:#344054;line-height:1.7}}
/* ---- health ---- */
.health-card{{padding:16px 20px;font-size:13px;line-height:1.7}}
.health-span{{color:#54607a}}
.health-flag{{margin-top:6px;padding:8px 12px;background:#fef2f2;border-left:3px solid #dc2626;border-radius:4px;color:#991b1b}}
.health-unknown{{margin-top:8px;color:#667085}}
/* ---- trend ---- */
.trend-card{{padding:8px 22px 16px}}
table.trend{{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}}
table.trend th{{padding:12px 8px 9px;text-align:left;font-size:12px;color:#667085;
  font-weight:600;border-bottom:1px solid #e1e5ef}}
table.trend th:first-child{{padding-left:0}}
table.trend th.num-col{{text-align:right}}
table.trend th.dir-col{{text-align:center;padding-right:0}}
table.trend td{{padding:10px 8px;font-size:13px;border-bottom:1px solid #eef0f5}}
table.trend td:first-child{{padding-left:0}}
table.trend tbody tr:last-child td{{border-bottom:none}}
.t-name{{color:#344054}}
.t-a{{color:#475467;text-align:right}}
.t-b{{color:#101828;font-weight:600;text-align:right}}
.t-dir{{font-weight:700;text-align:center;padding-right:0}}
/* ---- 附录 ---- */
.appendix{{padding:16px 22px;margin-bottom:12px}}
.appendix summary{{cursor:pointer;font-size:13.5px;font-weight:700;color:#0e7490;list-style:none}}
.appendix summary::-webkit-details-marker{{display:none}}
.tok-grid{{display:grid;grid-template-columns:auto 1fr auto;gap:9px 14px;align-items:center;
  margin-top:18px;font-size:12.5px}}
.tok-name{{font-family:ui-monospace,'SF Mono',Menlo,monospace;color:#475467}}
.tok-track{{height:12px;border-radius:4px;background:#eef1f6;overflow:hidden}}
.tok-fill{{height:100%;border-radius:4px}}
.tok-note{{margin-top:8px}}
.tok-table{{margin-top:14px}}
.tok-table td{{font-size:12.5px}}
.tok-cell{{font-family:ui-monospace,'SF Mono',Menlo,monospace;color:#344054}}
.tok-total{{font-weight:700;color:#101828}}
.ev-list{{display:grid;margin-top:10px}}
.ev-row{{display:flex;align-items:baseline;gap:14px;padding:11px 0;border-bottom:1px solid #eef0f5}}
.ev-last{{border-bottom:none}}
.ev-text{{font-size:13px;color:#344054;line-height:1.65;flex:1}}
.ptr-chip{{font-family:ui-monospace,'SF Mono',Menlo,monospace;font-size:11px;color:#0e7490;
  background:#ecf9fc;border:1px solid #c9ecf4;border-radius:5px;padding:2px 8px;
  white-space:nowrap;cursor:help}}
/* ---- 页脚 ---- */
.footer{{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-top:34px;
  padding-top:16px;border-top:1px solid #e1e5ef;font-size:11.5px;color:#667085}}
.footer-id{{font-family:ui-monospace,'SF Mono',Menlo,monospace}}
/* ---- 窄屏 / 打印 ---- */
@media (max-width:720px){{
  .hero,.main{{padding-left:20px;padding-right:20px}}
  .radar-card,.dim-cards,.depth-grid,.lg-grid{{grid-template-columns:1fr}}
  .m-grid{{grid-template-columns:repeat(2,1fr)}}
  .hero-nums{{gap:20px;flex-wrap:wrap}}
  /* 窄屏：固定大像素列会撑破容器/触发横滚——首列可缩 */
  .tok-row{{grid-template-columns:minmax(80px,140px) 1fr 44px;gap:8px}}
}}
@media print{{
  .hero{{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
}}
</style>
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


def _cn_num(n: int) -> str:
    """1-10 的中文数字（横幅判据句「N项判据全部达标」用）；超界回退阿拉伯数字。"""
    table = "零一二三四五六七八九十"
    return table[n] if 0 <= n <= 10 else str(n)


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
