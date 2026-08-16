"""「未测量」在报告里的折叠呈现 —— 渲染层降级的守卫。

改造前这一层**零测试覆盖**：`unmeasured` 只在 parser 与契约层被测过，
「报告是否正确渲染降级信息」全靠人工目视一份 codex 报告。这个文件补上它。

折叠的设计意图（BOSS 2026-08-15 定案）：Codex 报告 16 个指标格里 7 格显示
「未测量」，44% 的版面是空的，第一观感是「这工具对我没用」。改为主网格只留
测得到的格，未测量项收进折叠区块——**信息一条不丢，但第一眼全是实数**。

不变约束仍然成立：「未测量 ≠ 0」。折叠只改呈现位置，绝不允许把未测量的格
渲染成 0 或「—」，也绝不允许某一项在两处都不出现（那才是真的丢信息）。
"""

import pytest

from ai_coding_insights.report import render_profile_report
from ai_coding_insights.sources import get_source, unmeasured_fields
from ai_coding_insights.view_model import (
    UNMEASURED_TEXT, build_view, fold_unmeasured,
)

# Codex 的未测量字段集**不手写**——从来源能力集算出来，与规则层同源。
# 手写过一次，把 git 三键也写了进去，而 git 主锚是跨来源可测的（走 git log，
# 与会话记录无关），于是测出来的行为和真实 Codex 报告对不上。
CODEX_UNMEASURED = list(unmeasured_fields(get_source("codex").capabilities))


def _profile(**over):
    p = {
        "posture_distribution": {"L1": 0.18, "L2": 0.07, "L3": 0.57, "L4": 0.18},
        "breadth": {"headline": "工具广度跨 8 类"},
        "depth": {"headline": "多轮打磨"},
        "outcome": {"headline": "落地稳健", "landed": 37, "total": 46},
        "evidence": [],
    }
    p.update(over)
    return p


def _meta(**over):
    m = {"generated_at": "2026-06-09T00:00:00Z", "lookback_days": 30,
         "session_count": 107, "included_projects": ["/r/A"]}
    m.update(over)
    return m


def _metrics(**over):
    m = {"session_count": 107, "human_input_count": 588, "active_days": 20,
         "tool_breadth": 14, "commit_count": 46, "landed_count": 37,
         "edit_count": 886, "landed_ratio": 0.8, "turn_p90": 10,
         "decision_point_count": 100}
    m.update(over)
    return m


def _all_grid_labels(view):
    """主网格里现存的格标签。"""
    return [c["label"] for fam in view["families"] for c in fam["cells"]]


def _folded_labels(view):
    return [c["label"] for c in view["unmeasured_cells"]]


# ---- 基线：CC 路径零回归 ----

def test_无未测量时四族齐全且折叠区为空():
    """CC 走的是这条路。folding 必须完全不改变既有版面。"""
    view = build_view(_profile(), _meta(), _metrics())
    assert [f["name"] for f in view["families"]] == [
        "产出落地", "协作编排", "高阶行为", "节奏投入"]
    assert len(_all_grid_labels(view)) == 16
    assert view["unmeasured_cells"] == []
    assert UNMEASURED_TEXT not in _all_grid_labels(view)


def test_无未测量时主网格不出现未测量字样():
    view = build_view(_profile(), _meta(), _metrics())
    values = [c["value"] for fam in view["families"] for c in fam["cells"]]
    assert UNMEASURED_TEXT not in values


# ---- 折叠本体 ----

def test_未测量的格从主网格移出并进折叠区():
    view = build_view(_profile(), _meta(),
                      _metrics(unmeasured=CODEX_UNMEASURED, source="codex"))
    grid = _all_grid_labels(view)
    folded = _folded_labels(view)
    # 主网格里一格未测量都不许剩
    for fam in view["families"]:
        for c in fam["cells"]:
            assert c["value"] != UNMEASURED_TEXT, f"未测量的格漏在主网格：{c['label']}"
            assert c["unmeasured"] is False
    # 被摘走的确实进了折叠区
    assert "MCP 会话" in folded
    assert "SubAgent 会话" in folded
    assert "观测丢弃" in folded
    # git 主锚走 git log，与会话记录无关——跨来源都可测，不该被折叠
    assert "落地提交" not in folded and "提交总数" not in folded
    # 且不会重复出现在两处
    assert not (set(grid) & set(folded))


def test_折叠不丢格_两处并集等于完整网格():
    """最要命的回归是「某项在两处都不出现」——那是真的把信息删了。"""
    full = build_view(_profile(), _meta(), _metrics())
    degraded = build_view(_profile(), _meta(),
                          _metrics(unmeasured=CODEX_UNMEASURED, source="codex"))
    assert set(_all_grid_labels(full)) == (
        set(_all_grid_labels(degraded)) | set(_folded_labels(degraded)))


def test_折叠项带族名便于定位():
    view = build_view(_profile(), _meta(),
                      _metrics(unmeasured=CODEX_UNMEASURED, source="codex"))
    by_label = {c["label"]: c for c in view["unmeasured_cells"]}
    assert by_label["MCP 会话"]["family"] == "协作编排"
    assert by_label["观测丢弃"]["family"] == "产出落地"


def test_整族全未测量时该族不留空壳():
    """协作编排四格里三格靠 unmeasured，第四格「使用模型数」由 token_usage 决定。
    token_usage 为空时该格出「—」仍是实测值，故族不会全空；这里构造真全空的族——
    高阶行为四格全部可被 unmeasured 覆盖。"""
    um = ["thinking_block_count", "background_task_count",
          "max_parallel_agents", "parallel_agent_turns"]
    view = build_view(_profile(), _meta(), _metrics(unmeasured=um, source="codex"))
    assert "高阶行为" not in [f["name"] for f in view["families"]]
    folded = _folded_labels(view)
    for lbl in ("深度推理", "后台委托", "真并行峰值", "真并行轮次"):
        assert lbl in folded


def test_族全空被移除后剩余族仍完整():
    um = ["thinking_block_count", "background_task_count",
          "max_parallel_agents", "parallel_agent_turns"]
    view = build_view(_profile(), _meta(), _metrics(unmeasured=um, source="codex"))
    assert [f["name"] for f in view["families"]] == ["产出落地", "协作编排", "节奏投入"]
    assert all(len(f["cells"]) == 4 for f in view["families"])


def test_全部未测量时兜底不返回空网格():
    """极端防御：真出现「一格都测不到」，宁可退回原版面显示满屏未测量，
    也不能给用户一个空白的指标区（空白无从解释，满屏未测量至少自证了原因）。

    直接测纯函数：当前 16 格里「使用模型数」「时长 P90」不受 unmeasured 驱动，
    走 build_view 构造不出全空的网格；但来源能力表将来放宽/收紧都可能碰到，
    兜底得有守卫，不能是无人看管的死代码。
    """
    families = [
        {"name": "甲", "cells": [{"label": "A", "value": UNMEASURED_TEXT,
                                  "unmeasured": True}]},
        {"name": "乙", "cells": [{"label": "B", "value": UNMEASURED_TEXT,
                                  "unmeasured": True}]},
    ]
    kept, folded = fold_unmeasured(families)
    assert kept == families, "兜底失效：指标区被折叠成空"
    assert folded == []


def test_纯函数_部分未测量时正常拆分():
    families = [
        {"name": "甲", "cells": [
            {"label": "A", "value": "3", "unmeasured": False},
            {"label": "B", "value": UNMEASURED_TEXT, "unmeasured": True},
        ]},
    ]
    kept, folded = fold_unmeasured(families)
    assert [c["label"] for c in kept[0]["cells"]] == ["A"]
    assert folded == [{"label": "B", "value": UNMEASURED_TEXT,
                       "unmeasured": True, "family": "甲"}]


def test_纯函数_不改入参():
    """build_view 全程不改入参，这条纪律延伸到拆分函数——原 families 得原样可用。"""
    families = [
        {"name": "甲", "cells": [
            {"label": "A", "value": "3", "unmeasured": False},
            {"label": "B", "value": UNMEASURED_TEXT, "unmeasured": True},
        ]},
    ]
    fold_unmeasured(families)
    assert len(families[0]["cells"]) == 2


# ---- 未测量的语义不许被折叠稀释 ----

def test_折叠区的项不许显示为零():
    view = build_view(_profile(), _meta(),
                      _metrics(unmeasured=CODEX_UNMEASURED, source="codex",
                               mcp_sessions=0, subagent_sessions=0))
    for c in view["unmeasured_cells"]:
        assert c["value"] == UNMEASURED_TEXT
        assert c["value"] not in ("0", "—")


def test_横幅四数不参与折叠():
    """横幅是四维代表值，位置固定且 report 用 strict zip 配色。
    代表值测不到就得当场说测不到，不能藏进折叠区。"""
    view = build_view(_profile(), _meta(),
                      _metrics(unmeasured=["tool_breadth"], source="codex"))
    assert len(view["hero_nums"]) == 4
    assert any(h["value"] == UNMEASURED_TEXT for h in view["hero_nums"])


def test_置顶来源口径卡片仍列全部未测量字段():
    """折叠区只管网格里的 7 格，caveat 卡片管全部 16 个字段——两者不是一回事，
    卡片不许因为有了折叠区就缩水。"""
    view = build_view(_profile(), _meta(),
                      _metrics(unmeasured=CODEX_UNMEASURED, source="codex"))
    assert view["unmeasured"] == sorted(CODEX_UNMEASURED)


# ---- HTML 落地 ----

def _html(unmeasured=None):
    m = _metrics()
    if unmeasured:
        m = _metrics(unmeasured=unmeasured, source="codex")
    return render_profile_report(_profile(), _meta(), m)


def test_html_折叠区块渲染为可展开元素():
    html = _html(CODEX_UNMEASURED)
    assert "<details" in html
    assert "本来源测不到" in html


def test_html_指标格里不再出现未测量字样():
    """折叠后主网格全是实数。折叠区块不重复写 7 遍「未测量」——标题「本来源测不到
    的 N 项」已经承担了这个语义，逐项再写一遍只是把噪音搬了个地方。"""
    html = _html(CODEX_UNMEASURED)
    assert f'class="m-num" style="color:#101828">{UNMEASURED_TEXT}' not in html
    assert f'class="m-num" style="color:#4338ca">{UNMEASURED_TEXT}' not in html


def test_html_折叠区列出每一项的名字与所属族():
    html = _html(CODEX_UNMEASURED)
    _, _, folded = html.partition("<details")
    for label in ("MCP 会话", "SubAgent 会话", "观测丢弃"):
        assert label in folded, f"折叠区漏了：{label}"
    assert "协作编排" in folded


def test_html_无未测量时不渲染折叠区块():
    html = _html()
    assert "本来源测不到" not in html


# ---- 同一件事只说一次：测不到的项不许在别处又渲染成 0 ----

def test_成果代表行里测不到的那半段整段不写():
    """曾经的 bug：`dropped_count` 在 unmeasured 里，成果行却照写「观测丢弃 0」，
    旁边的解释又说「标为未测量，不是 0」——同一份报告自相矛盾，比少一个数更伤。"""
    view = build_view(_profile(), _meta(),
                      _metrics(unmeasured=CODEX_UNMEASURED, source="codex",
                               dropped_count=0, git_landed_count=0))
    desc = [r for r in view["dim_rows"] if r["name"] == "成果"][0]["desc"]
    assert "观测丢弃" not in desc


def test_成果代表行可测时照常写():
    view = build_view(_profile(), _meta(),
                      _metrics(git_landed_count=37, dropped_count=8))
    desc = [r for r in view["dim_rows"] if r["name"] == "成果"][0]["desc"]
    assert "落地 37" in desc and "观测丢弃 8" in desc


def test_成果卡片脚注与代表行同源():
    """漏过一次：代表行改了降级，成果卡片脚注还各拼各的、照写「观测丢弃 未测量」。"""
    view = build_view(_profile(), _meta(),
                      _metrics(unmeasured=CODEX_UNMEASURED, source="codex",
                               dropped_count=0, git_landed_count=19))
    assert "观测丢弃" not in view["outcome_nums"]
    assert view["outcome_nums"] == "落地 19"


def test_html_成果卡片不写测不到的那半段():
    html = _html(CODEX_UNMEASURED)
    assert "观测丢弃 未测量" not in html
    assert "观测丢弃 0" not in html


def test_html_落地率公式说明两条路径都在():
    """改这块时踩过 Python 运算符优先级：`A if c else "" + B` 解析成
    `A if c else ("" + B)`，于是**正常路径**反而丢了公式说明，且无测试发现。"""
    for html in (_html(), _html(CODEX_UNMEASURED)):
        assert "落地率 = 改动文件命中 AI 编辑的提交" in html


def test_两半都测不到时不留孤立分隔点():
    view = build_view(_profile(), _meta(),
                      _metrics(unmeasured=["git_landed_count", "dropped_count"],
                               source="codex"))
    desc = [r for r in view["dim_rows"] if r["name"] == "成果"][0]["desc"]
    assert not desc.endswith("·") and " ·  " not in desc


# ---- 能力盲区：本来源没有的能力不许报「你没用过」 ----

def test_能力集缺失时按来源名回填而非退全集():
    """`_aggregate.json` 少了 `capabilities` 键（旧文件 / 手工造的数据）时，
    退到「全集」就会给 Codex 用户报一串它压根没有的能力盲区。必须按来源回填。"""
    m = _metrics(source="codex", unmeasured=CODEX_UNMEASURED, tool_session_counts={})
    m.pop("capabilities", None)
    gaps = {g["label"] for g in build_view(_profile(), _meta(), m)["capability_gaps"]}
    for absent in ("SubAgent 委派", "Workflow 编排", "MCP 外部工具", "计划模式"):
        assert absent not in gaps, f"Codex 没有这个概念，不该报「没用过」：{absent}"
    # 反向：过滤必须是选择性的，不能整节清空——Codex 确有 Web 检索与任务清单
    assert {"Web 联网", "任务清单"} & gaps, "过滤过头了，连 Codex 真有的能力也不报"


def test_来源不认识时仍退回全集():
    """真·旧文件（连 source 都没有/不认识）保持改造前行为，不因为这条防御变严。"""
    m = _metrics(source="某个未来来源", tool_session_counts={})
    m.pop("capabilities", None)
    gaps = {g["label"] for g in build_view(_profile(), _meta(), m)["capability_gaps"]}
    assert "SubAgent 委派" in gaps


def test_html_降级时指标卡走紧凑排布():
    """摘掉格子后族内可能只剩 1 格，固定 4 列会在右边留下大片空洞——比显示
    「未测量」更难看。紧凑 class 让族按内容宽度横向流动。"""
    assert "fam-card fam-compact" in _html(CODEX_UNMEASURED)


def test_html_满格时不加紧凑class():
    """CC 路径版面必须一像素不动。（`.fam-compact` 的 CSS 定义始终在，
    这里查的是它有没有被挂到元素上。）"""
    html = _html()
    assert 'class="card fam-card"' in html
    assert "fam-card fam-compact" not in html
