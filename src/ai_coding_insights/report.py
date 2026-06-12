import math
import re
from html import escape

from .models import InsightsReport
from .stage import decide_stage, normalize_posture
from .capabilities import unused_capabilities

# 雷达图满刻度（与 stage.py 阶段阈值无关，仅控制可视化拉伸）：
_RADAR_BREADTH_FULL = 35.0      # 工具广度 35 种打满（≈内置高杠杆能力全集的量级上限）
_RADAR_DEPTH_FULL_TURNS = 15.0  # 平均 15 轮/会话打满（深度的粗代理）

# ════ 样式约定（与设计稿一一对应）════
# 背景      页面 #f3f5fa · 卡片 #fff · 卡片描边 #e1e5ef · 卡内分隔 #eef0f5
# 横幅      linear-gradient(120deg,#0b1026,#18204a) + 青/紫角部微光 + 底部 3px 渐变 keyline
# 文字      标题 #101828 · 正文 #344054 · 次级 #475467 · 弱化 #7d8694
# 色彩职责  数据序列(姿势 L1→L4): #c7eaf4 → #76c7e6 → #6e8ef2 → #4640d9
#           指标族: 产出落地 #0d9488 · 协作编排 #4f46e5 · 节奏投入 #7c3aed
#           达标✓: #15803d · 建议动作: #b45309/#fdeac2 · 链接/指针: #0e7490
# 章节号    ui-monospace 12px，按节循环 #0891b2 → #22a3c4 → #4f46e5 → #6366f1 →
#           #7c87f5 → #8b5cf6 → #a78bfa
# 数字      一律 font-variant-numeric: tabular-nums
# 板块顺序  横幅结论 → 01指标明细 → 02高光 → 03姿势+判据 → 04画像+详述 →
#           05摩擦建议 → 06能力盲区 → 07趋势 → 附录A Token(默认展开) → 附录B 证据链(折叠)
# 去重规则  横幅四数 = 四维代表值(成果·落地率 / 姿势·L4主导 / 水平·工具广度 / 深度·轮次)，
#           01 指标明细不重复横幅出现过的数，按族补齐明细(合入、编辑/合入、模型切换等)

_SEC_COLORS = ["#0891b2", "#22a3c4", "#4f46e5", "#6366f1", "#7c87f5", "#8b5cf6", "#a78bfa"]
# 姿势序列 L1→L4（横幅/堆叠条/图例共用）
_POSTURE_COLORS = {"L1": "#c7eaf4", "L2": "#76c7e6", "L3": "#6e8ef2", "L4": "#4640d9"}
# 高光序号圆点配色（按条目循环）
_HL_DOT = [("#d7f3fa", "#0e7490"), ("#e3e6fd", "#4338ca"), ("#ede7fc", "#6d28d9")]
# 维度色（04 详述行 + 卡片角标）
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
    """按判据的值键取实际值文本（如「40%」「29 种」）；兜底档 key=None 返回空串。
    key 由 stage.py 的 _STAGES 结构化给出，不做判据文案匹配——文案随便改不会断渲染。"""
    if key == "tool_breadth":
        return f"{int(values.get(key, 0))} 种"
    if key in ("L4", "L3+L4", "landed_ratio"):
        return f"{values.get(key, 0):.0%}"
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


def _render_stage_panel(st: dict) -> str:
    """档位判据卡（03 节右栏）：当前档判据全部达标（匹配即满足），
    距下一档缺口以 ✗ 行列在下方。st 为 decide_stage 返回 dict。"""
    sv = st.get("values", {}) or {}
    rows = "".join(_stage_crit_row(c, sv, met=True)
                   for c in (st.get("criteria") or []))
    gaps = st.get("gaps") or []
    if gaps:
        rows += ('<div class="crit-gap">距下一阶段还差</div>'
                 + "".join(_stage_crit_row(g, sv, met=False) for g in gaps))
    return (
        '<div class="card stage-panel">'
        f'<div class="card-title">档位判据 · 第 {int(st.get("stage", 1))} 档</div>'
        f'<div class="crit-list">{rows}</div>'
        '<div class="fine-note stage-note">阶段判定为软信号自我定位，判据透明可验，不用于考核。</div>'
        '</div>'
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
    ("落地率", "landed_ratio", "ratio"),
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
        a_raw, b_raw = _fnum(fh.get(key)), _fnum(sh.get(key))
        if kind == "ratio":
            a, b = a_raw, b_raw
            a_txt, b_txt = f"{a:.0%}", f"{b:.0%}"
        else:
            a = a_raw / fh_n if fh_n else 0.0
            b = b_raw / sh_n if sh_n else 0.0
            name = f"{name}（次/会话）"
            a_txt, b_txt = f"{a:.2f}", f"{b:.2f}"
        arrow = _trend_arrow(a, b)
        arrow_color = {"↑": "#4f46e5", "↓": "#0e7490"}.get(arrow, "#7d8694")
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
        '箭头只示变化方向，不评判好坏。</div>'
        '</div>'
    )


def _render_daily_heatmap(daily: list | None, idx: int) -> str:
    """活动热力图：CSS Grid 7 列（周一到周日），颜色深度按 session_count 分 4 档。"""
    if not daily:
        return ""
    # 构建日期→数据映射
    from datetime import date as date_type, timedelta
    data_map = {}
    for d in daily:
        data_map[d["date"]] = d["session_count"]

    if not data_map:
        return ""

    dates = sorted(data_map)
    first = date_type.fromisoformat(dates[0])
    last = date_type.fromisoformat(dates[-1])

    # 从 first 所在的周一开始，到 last 所在的周日结束
    start = first - timedelta(days=first.weekday())
    end = last - timedelta(days=last.weekday()) + timedelta(days=6)

    cells = ""
    cur = start
    month_labels = []
    week = []
    weeks = []
    while cur <= end:
        iso = cur.isoformat()
        val = data_map.get(iso, 0)
        if val == 0:
            level, color = 0, "#e8ebf0"
        elif val == 1:
            level, color = 1, "#a5d6f9"
        elif val <= 3:
            level, color = 2, "#4f9ed4"
        else:
            level, color = 3, "#1a5f8a"
        title = f"{iso}：{val} 会话" if val else iso
        cells += f'<div class="h-cell h-lv{level}" style="background:{color}" title="{title}"></div>'
        # 月份标签：每月第一天，记录周内列位置（1-7）
        if cur.day == 1 or cur == start:
            month_labels.append((len(week) + 1, cur.strftime("%m月")))
        week.append(cur)
        if cur.weekday() == 6:
            weeks.append(week)
            week = []
        cur += timedelta(days=1)
    if week:
        weeks.append(week)

    # 星期表头
    day_names = ["一", "二", "三", "四", "五", "六", "日"]
    header = "".join(f"<div class='h-dow'>{d}</div>" for d in day_names)

    # 月份标注行
    month_row = ""
    for pos, label in month_labels:
        month_row += f"<div class='h-mo' style='grid-column:{pos}'>{label}</div>"

    # 行数
    n_rows = len(weeks)

    return (
        _sec_header(idx, "活动热力")
        + '<div class="card">'
        + f'<div class="heatmap" style="--h-cols:7;--h-rows:{n_rows};--h-row-start:2">'
        + f'<div class="h-mo-row">{month_row}</div>'
        + f'<div class="h-dow-row">{header}</div>'
        + f'<div class="h-grid">{cells}</div>'
        + '<div class="h-legend">'
        + '<span class="h-leg-swatch" style="background:#e8ebf0"></span> 0'
        + '<span class="h-leg-swatch" style="background:#a5d6f9"></span> 1'
        + '<span class="h-leg-swatch" style="background:#4f9ed4"></span> 2–3'
        + '<span class="h-leg-swatch" style="background:#1a5f8a"></span> 4+ 会话/日'
        + '</div></div></div>'
    )


def _render_tool_skill_mcp_appendix(tool_session_counts: dict | None,
                                      skill_counts: dict | None,
                                      mcp_server_counts: dict | None) -> str:
    """工具/技能/MCP 分布附录（默认折叠）。三组降序条形图。"""
    if not tool_session_counts and not skill_counts and not mcp_server_counts:
        return ""

    def _bar_items(counts: dict, top_n: int = 15) -> tuple[list, float]:
        if not counts:
            return [], 0.0
        items = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:top_n]
        mx = items[0][1] if items else 1
        return items, float(mx)

    sections = ""
    # 高频工具 Top 15
    if tool_session_counts:
        items, mx = _bar_items(tool_session_counts)
        bars = ""
        for name, cnt in items:
            w = (cnt / mx * 100.0) if mx else 0.0
            bars += (
                f'<div class="tok-row"><span class="tok-label" title="{escape(name)}">'
                f'{escape(name)}</span><span class="tok-bar-wrap">'
                f'<span class="tok-bar" style="width:{w:.1f}%"></span>'
                f'<span class="tok-val">{cnt}</span></span></div>'
            )
        sections += (
            '<details class="tok-block"><summary><b>高频工具 Top 15</b></summary>'
            f'<div class="tok-chart">{bars}</div></details>'
        )

    # 技能频次
    if skill_counts:
        items, mx = _bar_items(skill_counts)
        bars = ""
        for name, cnt in items:
            w = (cnt / mx * 100.0) if mx else 0.0
            bars += (
                f'<div class="tok-row"><span class="tok-label" title="{escape(name)}">'
                f'{escape(name)}</span><span class="tok-bar-wrap">'
                f'<span class="tok-bar" style="width:{w:.1f}%"></span>'
                f'<span class="tok-val">{cnt}</span></span></div>'
            )
        sections += (
            '<details class="tok-block"><summary><b>技能频次</b></summary>'
            f'<div class="tok-chart">{bars}</div></details>'
        )

    # MCP Server 频次
    if mcp_server_counts:
        items, mx = _bar_items(mcp_server_counts)
        bars = ""
        for name, cnt in items:
            w = (cnt / mx * 100.0) if mx else 0.0
            bars += (
                f'<div class="tok-row"><span class="tok-label" title="{escape(name)}">'
                f'{escape(name)}</span><span class="tok-bar-wrap">'
                f'<span class="tok-bar" style="width:{w:.1f}%"></span>'
                f'<span class="tok-val">{cnt}</span></span></div>'
            )
        sections += (
            '<details class="tok-block"><summary><b>MCP Server 频次</b></summary>'
            f'<div class="tok-chart">{bars}</div></details>'
        )

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


def _ptr_chip(entry: dict, projects: list) -> str:
    """证据/高光指针胶囊：「项目名 · 会话ID前8位 ↗」，完整指针放 title 悬停可见。

    项目名经 meta.included_projects 反查（编码目录名匹配），匹配不到只出会话短 ID；
    渲染前指针经规则层核验，未命中的明示警示而非装作可回看。
    """
    pointer = str(entry.get("pointer", ""))
    path_part = pointer.split("#", 1)[0]
    stem = path_part.rsplit("/", 1)[-1]
    stem = stem[:-6] if stem.endswith(".jsonl") else stem
    sid = stem[:8]
    parent = path_part.rsplit("/", 2)[-2] if "/" in path_part else ""
    label = sid
    for p in projects or []:
        if parent and parent == _encode_cwd(p):
            label = f"{str(p).rstrip('/').rsplit('/', 1)[-1]} · {sid}"
            break
    miss = ' <span class="ptr-miss">⚠ 指针未命中</span>' if entry.get("pointer_missing") else ""
    return (f'<span class="ptr-chip" title="{escape(pointer)}">{escape(label)} ↗</span>{miss}')


def _render_highlights_section(highlights: list | None, projects: list, idx: int) -> str:
    """02 高光时刻；空则返回空串。behavior/pointer 来自 LLM，escape。"""
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
            f'<span class="hl-text">{escape(str(h.get("behavior", "")))}</span>'
            f'<span class="hl-link" title="{escape(str(h.get("pointer", "")))}">原会话 ↗</span>{miss}'
            '</div>'
        )
    return (_sec_header(idx, "高光时刻")
            + f'<div class="card hl-card">{rows}</div>')


def _render_capabilities_section(tool_session_counts: dict | None, idx: int,
                                  customization_signals: dict | None = None) -> str:
    """06 能力盲区；label/scene 为内置文案，仍统一 escape 求稳。"""
    gaps_cap = unused_capabilities(tool_session_counts or {},
                                    customization_signals=customization_signals)
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
    items.sort(key=lambda t: t[1], reverse=True)
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
        return f"首次基线 · 近 {int(w.get('lookback_days', 45))} 天"
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
    color = {"↑": "#4f46e5", "↓": "#0e7490"}.get(arrow, "#7d8694")
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


def _dim_points_rows(points: list) -> str:
    """维度卡分点列表：每行「标题（加粗）+ 次级描述」，按「——」拆分。"""
    rows = ""
    for i, p in enumerate(points or []):
        t, d = _lead_rest(p)
        last = " pt-last" if i == len(points) - 1 else ""
        desc = f'<div class="pt-desc">{escape(d)}</div>' if d is not None else ""
        rows += f'<div class="pt-row{last}"><div class="pt-title">{escape(t)}</div>{desc}</div>'
    return rows


def _dim_card(dim: str, title: str, block: dict, extra_rows: str = "") -> str:
    """维度详述卡（水平/成果）：色标 + 标题 + headline 副题 + 分点列表。"""
    head = escape(str(block.get("headline") or block.get("summary") or ""))
    rows = _dim_points_rows(block.get("points") or [])
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
    cells = ""
    for p in (block.get("points") or []):
        t, d = _lead_rest(p)
        desc = f'<div class="depth-desc">{escape(d)}</div>' if d is not None else ""
        cells += f'<div class="depth-cell"><div class="pt-title">{escape(t)}</div>{desc}</div>'
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
    _cc, _lc = mval("commit_count"), mval("landed_count")
    dropped = mval("dropped_count",
                   max(0, int(_cc) - int(_lc)) if _cc is not None and _lc is not None
                   else ((o_total - o_landed) if o_total else None))

    def num(v):
        return "—" if v is None else escape(str(v))

    def pct0(v):
        return "—" if v is None else f"{float(v):.0%}"

    l4 = pct("L4")

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
    avg_turns = mval("avg_turns")
    hero_nums = [
        ("#67e8f9", pct0(landed_ratio), "成果 · 落地率"),
        ("#a5b4fc", f"{l4:.0%}", "姿势 · L4 主导"),
        ("#5eead4", num(mval("tool_breadth")), "水平 · 工具广度"),
        ("#fcd34d", "—" if avg_turns is None else f"{float(avg_turns):.1f}", "深度 · 轮次/会话"),
    ]
    hero_nums_html = "".join(
        f'<div><div class="hnum" style="color:{c}">{v}</div>'
        f'<div class="hlbl">{escape(l)}</div></div>'
        for c, v, l in hero_nums
    )

    # ---- 01 指标明细：三族，不重复横幅四数 ----
    edits_per_landed = ("—" if not (edit_count and git_landed)
                        else f"≈{round(float(edit_count) / float(git_landed))}")
    token_usage = m.get("token_usage") or {}
    model_switch = num(len(token_usage) if token_usage else None)
    families = [
        ("产出落地", "#0d9488", "#0f766e", [
            ("落地提交", num(None if git_landed is None else int(git_landed)),
             diff_html("git_landed_count")),
            ("观测丢弃", num(None if dropped is None else int(dropped)),
             diff_html("dropped_count")),
            ("编辑数", num(edit_count), diff_html("edit_count")),
            ("编辑/落地", edits_per_landed, ""),
        ]),
        ("协作编排", "#4f46e5", "#4338ca", [
            ("SubAgent 会话", num(mval("subagent_sessions")), diff_html("subagent_sessions")),
            ("Workflow 会话", num(mval("workflow_sessions")), diff_html("workflow_sessions")),
            ("MCP 会话", num(mval("mcp_sessions")), diff_html("mcp_sessions")),
            ("模型切换", model_switch, ""),
        ]),
        ("节奏投入", "#7c3aed", "#6d28d9", [
            ("会话数", num(mval("session_count")), diff_html("session_count")),
            ("有效输入", num(mval("human_input_count")), diff_html("human_input_count")),
            ("活跃天数", num(mval("active_days")), diff_html("active_days")),
            ("时长中位数", dur(mval("duration_median_min")), diff_html("duration_median_min")),
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

    # ---- 03 姿势分布 + 档位判据 ----
    total_pd = sum(pct(t) for t in ("L1", "L2", "L3", "L4")) or 1.0
    segs = "".join(
        f'<span style="width:{pct(t)/total_pd*100:.2f}%;background:{_POSTURE_COLORS[t]}"'
        f' title="{t} {pct(t):.0%}"></span>'
        for t in ("L1", "L2", "L3", "L4")
    )
    legend_html = "".join(
        f'<div class="lg-row"><span class="lg-swatch" style="background:{_POSTURE_COLORS[code]}"></span>'
        f'<span><b class="ink">{code} {name} {pct(code):.0%}</b> · {desc}</span></div>'
        for code, name, desc in _LEGEND_ITEMS
    )
    # 阶段判定只算一次：横幅大字 / 判据卡共用同一结果
    stage = (None if metrics is None
             else decide_stage(pd, m.get("tool_breadth", 0), m.get("landed_ratio", 0.0)))
    posture_card = (
        '<div class="card posture-card">'
        '<div class="card-title">姿势分布（主导性）</div>'
        f'<div class="stack">{segs}</div>'
        f'<div class="lg-list">{legend_html}</div>'
        '<div class="fine-note">四档由 LLM 对每条真人输入逐条语义分档、规则层聚合组装；'
        'AskUserQuestion 选项回答按协议硬信号计入 L2。</div>'
        '</div>'
    )
    if stage is not None:
        posture_sec_title = "姿势分布与档位判据"
        posture_section_body = (f'<div class="posture-grid">{posture_card}'
                                f'{_render_stage_panel(stage)}</div>')
    else:
        posture_sec_title = "姿势分布"
        posture_section_body = posture_card

    # ---- 04 四维雷达 + 维度详述 ----
    axis_posture = max(0.0, min(1.0, pct("L3") + pct("L4")))
    tb = mval("tool_breadth")
    axis_breadth = min(float(tb) / _RADAR_BREADTH_FULL, 1.0) if tb is not None else 0.0
    at = mval("avg_turns")
    axis_depth = min(float(at) / _RADAR_DEPTH_FULL_TURNS, 1.0) if at is not None else 0.0
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
        return escape(str(block.get("headline") or block.get("summary") or ""))

    # 成果代表行附「落地 X · 观测丢弃 Y」（git 主锚口径）。与横幅同源：硬指标优先。
    landed_disp = "—" if git_landed is None else f"{int(git_landed)}"
    dropped_disp = "—" if dropped is None else f"{int(dropped)}"
    outcome_desc = f"落地 {landed_disp} · 观测丢弃 {dropped_disp}"
    if _headline(outcome):
        outcome_desc = f"{_headline(outcome)} · {outcome_desc}"
    dim_rows = [
        ("姿势", f"{l4:.0%}", "L4 主导", f"以引导和主导为主，L3+L4 合计 {axis_posture:.0%}"),
        ("水平", num(tb), "种工具", _headline(breadth)),
        ("深度", "—" if at is None else f"{float(at):.1f}", "轮/会话", _headline(depth)),
        ("成果", pct0(landed_ratio), "落地率", outcome_desc),
    ]
    dim_rows_html = ""
    for i, (name, value, unit, desc) in enumerate(dim_rows):
        last = " dim-last" if i == len(dim_rows) - 1 else ""
        dim_rows_html += (
            f'<div class="dim-row{last}">'
            f'<span class="dim-name" style="color:{_DIM_COLORS[name]}">{name}</span>'
            f'<span class="dim-val">{value}<span class="dim-unit">{escape(unit)}</span></span>'
            f'<span class="dim-desc">{desc}</span></div>'
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
                    extra_rows=(f'<div class="pt-row pt-last"><div class="pt-title">'
                                f'落地 {landed_disp} · 观测丢弃 {dropped_disp}</div></div>'))
        + '</div>'
        + _depth_card(depth)
    )

    # ---- 05 摩擦 + 建议 ----
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
    sections.append(_sec_header(idx, "指标明细", "横幅四数为四维代表值，此处不再重复",
                                margin_top=False)
                    + f'<div class="card fam-card">{fam_html}</div>')
    hl_html = _render_highlights_section(profile.get("highlights"), projects, idx + 1)
    if hl_html:
        idx += 1
        sections.append(hl_html)
    idx += 1
    sections.append(_sec_header(idx, posture_sec_title) + posture_section_body)
    idx += 1
    sections.append(
        _sec_header(idx, "四维画像与维度详述") + radar_panel + dim_cards
        + '<div class="fine-note sec-note">维度详述、摩擦建议与证据描述的文字由 LLM 解读生成；'
        '数字以指标卡与表格的硬指标为准。</div>')
    if fr_items:
        idx += 1
        sections.append(_sec_header(idx, "摩擦 + 建议") + f'<div class="fr-list">{fr_items}</div>')
    if metrics is not None:
        idx += 1
        sections.append(_render_capabilities_section(m.get("tool_session_counts"), idx,
                                                      customization_signals=m.get("customization_signals")))
    heatmap_html = _render_daily_heatmap(m.get("daily"), idx + 1)
    if heatmap_html:
        idx += 1
        sections.append(heatmap_html)
    trend_html = _render_trend_section(m.get("trend"), idx + 1)
    if trend_html:
        idx += 1
        sections.append(trend_html)

    # ---- 附录（不编号）----
    token_block = _render_token_details(m.get("token_usage"), m.get("token_total"))
    tsm_appendix = _render_tool_skill_mcp_appendix(
        m.get("tool_session_counts"), m.get("skill_counts"), m.get("mcp_server_counts"))
    appendix = (
        '<div class="sec"><span class="sec-num" style="color:#7d8694">附录</span>'
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
.ink{{color:#101828}}
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
.hero-meta{{font-family:ui-monospace,'SF Mono',Menlo,monospace;font-size:12px;color:#5f6b8f}}
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
.sec-hint{{font-size:12.5px;color:#7d8694}}
.card{{background:#fff;border:1px solid #e1e5ef;border-radius:12px}}
.card-title{{font-size:13px;font-weight:700;color:#101828}}
.fine-note{{font-size:11.5px;color:#98a2b3;line-height:1.6;margin-top:10px}}
.sec-note{{margin-top:10px}}
.tag{{font-size:11px;font-weight:700;border-radius:5px;padding:2px 8px;
  height:fit-content;white-space:nowrap;flex:0 0 auto}}
.tag-advice{{color:#b45309;background:#fdeac2}}
.tag-unused{{color:#6d28d9;background:#ede7fc;transform:translateY(2px)}}
.tag-ok{{color:#15803d;background:#dcfce7;transform:translateY(2px)}}
/* ---- 01 指标明细 ---- */
.fam-card{{padding:4px 22px}}
.fam{{padding:16px 0;border-bottom:1px solid #eef0f5}}
.fam-last{{border-bottom:none}}
.fam-head{{display:flex;align-items:center;gap:7px;font-size:12px;font-weight:700;
  margin-bottom:12px}}
.fam-swatch{{width:8px;height:8px;border-radius:3px;flex:0 0 auto}}
.m-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}
.m-num{{font-size:24px;font-weight:700;font-variant-numeric:tabular-nums;letter-spacing:-.5px}}
.m-num .unit{{font-size:15px;color:#7d8694}}
.m-lbl{{font-size:12px;color:#7d8694;margin-top:2px}}
.delta{{font-weight:700;font-size:.95em;font-family:ui-monospace,'SF Mono',Menlo,monospace;
  font-variant-numeric:tabular-nums}}
/* ---- 02 高光时刻 ---- */
.hl-card{{padding:8px 22px}}
.hl-row{{display:flex;align-items:baseline;gap:12px;padding:13px 0;border-bottom:1px solid #eef0f5}}
.hl-last{{border-bottom:none}}
.hl-dot{{flex:0 0 auto;width:22px;height:22px;border-radius:50%;font-size:11px;font-weight:700;
  display:inline-flex;align-items:center;justify-content:center;transform:translateY(4px)}}
.hl-text{{font-size:13.5px;color:#344054;line-height:1.65;flex:1}}
.hl-link{{font-size:12px;color:#0e7490;white-space:nowrap;font-weight:500;cursor:help}}
.ptr-miss{{color:#b45309;font-size:11px;font-weight:700;white-space:nowrap}}
/* ---- 03 姿势 + 判据 ---- */
.posture-grid{{display:grid;grid-template-columns:3fr 2fr;gap:16px}}
.posture-card{{padding:20px 22px}}
.posture-card .card-title{{margin-bottom:14px}}
.stack{{display:flex;width:100%;height:26px;border-radius:7px;overflow:hidden}}
.lg-list{{display:grid;gap:8px;margin-top:14px;font-size:12.5px;color:#475467;line-height:1.55}}
.lg-row{{display:flex;gap:8px}}
.lg-swatch{{flex:0 0 auto;width:10px;height:10px;border-radius:3px;transform:translateY(4px)}}
.stage-panel{{padding:20px 22px;display:flex;flex-direction:column}}
.stage-panel .card-title{{margin-bottom:12px}}
.crit-list{{display:grid;gap:10px;font-size:13px;color:#475467}}
.crit-row{{display:flex;justify-content:space-between;gap:10px}}
.crit-ok{{color:#15803d;font-weight:700;font-variant-numeric:tabular-nums}}
.crit-miss{{color:#b42318;font-weight:700;font-variant-numeric:tabular-nums}}
.crit-na{{color:#98a2b3}}
.crit-gap{{color:#7d8694;font-size:11.5px;font-weight:700;margin-top:4px}}
.stage-note{{margin-top:auto;padding-top:12px}}
/* ---- 04 画像 + 详述 ---- */
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
.dim-unit{{font-size:11.5px;font-weight:500;color:#98a2b3;margin-left:5px}}
.dim-desc{{font-size:12.5px;color:#7d8694;line-height:1.55}}
.dim-cards{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px}}
.dim-card{{padding:18px 22px 14px}}
.dim-card-head{{display:flex;align-items:baseline;gap:8px}}
.dim-swatch{{width:8px;height:8px;border-radius:3px;flex:0 0 auto;transform:translateY(-1px)}}
.dim-card-title{{font-size:13.5px;font-weight:700;color:#101828}}
.dim-card-sub{{font-size:12.5px;color:#7d8694;margin:4px 0 6px 16px}}
.pt-list{{display:grid}}
.pt-row{{padding:9px 0;border-bottom:1px solid #eef0f5}}
.pt-last{{border-bottom:none}}
.pt-title{{font-size:13px;font-weight:700;color:#101828}}
.pt-desc{{font-size:12.5px;color:#7d8694;line-height:1.55;margin-top:2px}}
.depth-card{{padding:18px 22px 22px;margin-top:16px}}
.depth-sub{{margin-bottom:14px}}
.depth-grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
.depth-cell{{background:#f8f9fc;border-radius:10px;padding:14px 16px}}
.depth-desc{{font-size:12.5px;color:#475467;line-height:1.65;margin-top:4px}}
/* ---- 05 摩擦建议 ---- */
.fr-list{{display:grid;gap:12px}}
.fr-card{{padding:18px 22px}}
.fr-obs{{font-size:13.5px;color:#344054;line-height:1.7}}
.fr-ptrs{{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}}
/* ---- 07 活动热力 ---- */
.heatmap{{display:grid;grid-template-rows:auto auto auto;gap:4px;margin-top:8px}}
.h-mo-row{{display:grid;grid-template-columns:repeat(var(--h-cols),1fr);grid-column:2}}
.h-mo{{font-size:11px;color:#7d8694;font-weight:600}}
.h-dow-row{{display:flex;gap:4px;margin-top:2px;font-size:11px;color:#7d8694;font-weight:600}}
.h-dow{{width:28px;text-align:center}}
.h-grid{{display:grid;grid-template-columns:repeat(var(--h-cols),28px);gap:4px;margin-top:2px}}
.h-cell{{width:28px;height:28px;border-radius:4px;cursor:help}}
.h-legend{{display:flex;gap:14px;align-items:center;margin-top:10px;font-size:11px;color:#7d8694}}
.h-leg-swatch{{display:inline-block;width:14px;height:14px;border-radius:3px}}
/* ---- 工具/技能/MCP 附录 ---- */
.tok-block{{margin-bottom:8px}}
.tok-block summary{{cursor:pointer;font-size:12.5px;font-weight:600;color:#475467;padding:4px 0;list-style:none}}
.tok-block summary::-webkit-details-marker{{display:none}}
.tok-chart{{margin-top:8px;display:grid;gap:5px}}
.tok-row{{display:grid;grid-template-columns:160px 1fr 50px;gap:10px;align-items:center;font-size:12px}}
.tok-label{{font-family:ui-monospace,'SF Mono',Menlo,monospace;color:#344054;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.tok-bar-wrap{{height:10px;border-radius:3px;background:#eef1f6;overflow:hidden}}
.tok-bar{{height:100%;border-radius:3px;background:linear-gradient(90deg,#22a3c4,#6366f1)}}
.fr-box{{display:flex;gap:10px;margin-top:10px;background:#fffaeb;border:1px solid #fdeac2;
  border-radius:8px;padding:10px 14px}}
.fr-sug{{font-size:13px;color:#57534e;line-height:1.7}}
/* ---- 06 能力盲区 ---- */
.cap-card{{padding:18px 22px;display:grid;gap:10px}}
.cap-row{{display:flex;gap:12px;font-size:13.5px;color:#344054;line-height:1.7}}
/* ---- 07 趋势 / 附录表格 ---- */
.trend-card{{padding:8px 22px 16px}}
table.trend{{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}}
table.trend th{{padding:12px 8px 9px;text-align:left;font-size:12px;color:#7d8694;
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
.tok-val{{font-family:ui-monospace,'SF Mono',Menlo,monospace;color:#101828;font-weight:600}}
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
  padding-top:16px;border-top:1px solid #e1e5ef;font-size:11.5px;color:#98a2b3}}
.footer-id{{font-family:ui-monospace,'SF Mono',Menlo,monospace}}
/* ---- 窄屏 / 打印 ---- */
@media (max-width:720px){{
  .hero,.main{{padding-left:20px;padding-right:20px}}
  .posture-grid,.radar-card,.dim-cards,.depth-grid{{grid-template-columns:1fr}}
  .m-grid{{grid-template-columns:repeat(2,1fr)}}
  .hero-nums{{gap:20px;flex-wrap:wrap}}
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
