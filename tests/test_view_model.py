"""view_model 字段级测试：直接断言 build_view 返回的数据，不 grep HTML。

这一层是无 IO 纯函数：所有「算数据 / 下判定」的逻辑都在这里，
report.py 只负责 dict → HTML。故边界与降级在此处钉死，渲染层不再重复测。
"""

import pytest

from ai_coding_insights.view_model import (
    RADAR_BREADTH_FULL, RADAR_DEPTH_FULL_TURNS, bar_items, build_view, safe_int,
    safe_num, timeline_bars, token_items, trend_view,
)


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


def _cell(view, family_name, label):
    for fam in view["families"]:
        if fam["name"] == family_name:
            for c in fam["cells"]:
                if c["label"] == label:
                    return c
    raise AssertionError(f"未找到指标格：{family_name}/{label}")


def _dim(view, name):
    for row in view["dim_rows"]:
        if row["name"] == name:
            return row
    raise AssertionError(f"未找到维度行：{name}")


# ---- 雷达归一 ----

def test_radar_axes_normalized_by_full_scale():
    view = build_view(_profile(), _meta(),
                      _metrics(tool_breadth=RADAR_BREADTH_FULL / 2,
                               turn_p90=RADAR_DEPTH_FULL_TURNS / 2,
                               landed_ratio=0.4))
    assert view["radar_labels"] == ["姿势", "水平", "深度", "成果"]
    posture, breadth, depth, outcome = view["radar_axes"]
    assert breadth == pytest.approx(0.5)
    assert depth == pytest.approx(0.5)
    assert outcome == pytest.approx(0.4)
    assert posture == pytest.approx(0.75)   # L3 0.57 + L4 0.18


def test_radar_axes_clamped_at_full_scale():
    """超满分必须截断到 1.0，绝不让多边形冲出外框。"""
    view = build_view(_profile(), _meta(),
                      _metrics(tool_breadth=999, turn_p90=999, landed_ratio=3.5))
    assert view["radar_axes"][1] == 1.0
    assert view["radar_axes"][2] == 1.0
    assert view["radar_axes"][3] == 1.0


def test_radar_posture_axis_clamped_when_distribution_over_one():
    prof = _profile(posture_distribution={"L1": 0, "L2": 0, "L3": 0.9, "L4": 0.9})
    # 和 1.8 > 1.5 → normalize_posture 视为百分数形态并 /100，L3+L4 = 0.018
    assert build_view(prof, _meta(), _metrics())["radar_axes"][0] == pytest.approx(0.018)
    # 直接给和 ≤1.5 但两档相加超 1 的脏分布 → 截断到 1.0
    prof2 = _profile(posture_distribution={"L1": 0, "L2": 0, "L3": 0.7, "L4": 0.7})
    assert build_view(prof2, _meta(), _metrics())["radar_axes"][0] == 1.0


def test_radar_axes_zero_when_metrics_absent():
    view = build_view(_profile(), _meta(), None)
    assert view["radar_axes"][1] == 0.0        # 无 tool_breadth
    assert view["radar_axes"][2] == 0.0        # 无 turn_p90
    # 成果轴退到 LLM outcome 的 landed/total
    assert view["radar_axes"][3] == pytest.approx(37 / 46)


# ---- metrics 缺省时的降级 ----

def test_metrics_none_degrades_all_judgements():
    view = build_view(_profile(), _meta(), None, None)
    assert view["posture_diag"] is None
    assert view["posture_state"] is None
    assert view["stage"] is None
    assert view["stage_name"] is None
    assert view["stage_no"] is None
    assert view["stage_crit_note"] == ""
    assert view["capability_gaps"] is None
    assert view["trend"] is None
    assert view["timeline_bars"] == []
    assert view["token_items"] is None
    # 横幅四数：姿态/广度/深度无判定出「—」，成果退到 LLM outcome 比值
    assert [h["value"] for h in view["hero_nums"]] == ["80%", "—", "—", "—"]


def test_metrics_none_falls_back_to_llm_outcome_numbers():
    view = build_view(_profile(), _meta(), None, None)
    assert view["git_landed"] == 37
    assert view["dropped"] == 9                     # total - landed
    assert view["outcome_desc"] == "落地稳健 · 落地 37 · 观测丢弃 9"
    assert _cell(view, "产出落地", "落地提交")["value"] == "37"
    assert _cell(view, "产出落地", "提交总数")["value"] == "—"


def test_empty_metrics_dict_still_judges():
    """metrics={} 与 metrics=None 语义不同：空 dict 仍要判档/判姿态（探索期 + 样本不足）。"""
    view = build_view(_profile(), _meta(), {}, None)
    assert view["stage_name"] == "探索期"
    assert view["posture_state"] == "样本不足"
    assert view["capability_gaps"] is not None


# ---- landed_ratio / git_landed / dropped 展示口径 ----

def test_landed_ratio_zero_denominator_is_none_not_zero():
    """outcome.total = 0（分母为 0）时落地率不可算：必须出「—」，不能伪造 0%。"""
    prof = _profile(outcome={"headline": "h", "landed": 0, "total": 0})
    view = build_view(prof, _meta(), None, None)
    assert view["landed_ratio"] is None
    assert view["landed_ratio_text"] == "—"
    assert view["radar_axes"][3] == 0.0
    assert view["git_landed"] is None
    assert view["dropped"] is None
    assert view["outcome_desc"] == "h · 落地 — · 观测丢弃 —"


def test_landed_ratio_zero_denominator_dirty_values():
    """脏值（非数）不得炸整张报告：按 0 计并降级成「—」。"""
    prof = _profile(outcome={"headline": "h", "landed": "x", "total": "y"})
    view = build_view(prof, _meta(), None, None)
    assert view["outcome_landed"] == 0.0 and view["outcome_total"] == 0.0
    assert view["landed_ratio"] is None


def test_git_anchor_preferred_over_transcript_and_llm():
    m = _metrics(git_landed_count=8, git_commit_total=11, dropped_count=3,
                 landed_count=7, landed_ratio=8 / 11)
    view = build_view(_profile(), _meta(), m, None)
    assert view["git_landed"] == 8
    assert view["git_commit_total"] == 11
    assert view["dropped"] == 3
    assert view["landed_ratio_text"] == "73%"


def test_git_anchor_degrades_to_transcript_then_llm():
    # 缺 git 键的旧口径 metrics → 退 transcript：landed_count=37、丢弃=46-37
    view = build_view(_profile(), _meta(), _metrics(), None)
    assert view["git_landed"] == 37
    assert view["dropped"] == 9
    assert view["git_commit_total"] is None
    # commit_count/landed_count 也脏 → 丢弃数退 LLM outcome 的 total-landed
    view2 = build_view(_profile(), _meta(),
                       _metrics(commit_count="a", landed_count="b",
                                git_landed_count=4), None)
    assert view2["dropped"] == 9
    assert view2["git_landed"] == 4


def test_outcome_desc_without_headline():
    prof = _profile(outcome={"landed": 3, "total": 4})
    view = build_view(prof, _meta(), None, None)
    assert view["outcome_desc"] == "落地 3 · 观测丢弃 1"


# ---- posture_diag 各分支 ----

@pytest.mark.parametrize("dist,extra,state", [
    ({"L1": 0.1, "L2": 0.1, "L3": 0.5, "L4": 0.3}, {}, "偏对抗"),
    ({"L1": 0.5, "L2": 0.35, "L3": 0.1, "L4": 0.05}, {"plan_mode_sessions": 0}, "偏依赖"),
    ({"L1": 0.5, "L2": 0.35, "L3": 0.1, "L4": 0.05}, {"plan_mode_sessions": 3}, "放手为主"),
    ({"L1": 0.2, "L2": 0.15, "L3": 0.5, "L4": 0.15}, {}, "健康"),
    ({"L1": 0, "L2": 0, "L3": 0, "L4": 0}, {}, "样本不足"),
    ({"L1": 0.2, "L2": 0.15, "L3": 0.5, "L4": 0.15}, {"decision_point_count": 5}, "样本不足"),
])
def test_posture_diag_branches(dist, extra, state):
    view = build_view(_profile(posture_distribution=dist), _meta(),
                      _metrics(**extra), None)
    assert view["posture_state"] == state
    assert view["posture_diag"]["state"] == state
    # 横幅与四维代表行同源，绝不各算各的
    assert view["hero_nums"][1]["value"] == state
    assert _dim(view, "姿势")["value"] == state


def test_posture_dim_desc_falls_back_when_no_diag():
    """无 metrics → 无诊断：姿势行描述退到 L3+L4 合计，值出「—」。"""
    view = build_view(_profile(), _meta(), None, None)
    assert _dim(view, "姿势")["value"] == "—"
    assert _dim(view, "姿势")["desc"] == "L3+L4 合计 75%"


def test_posture_segments_label_threshold_and_order():
    prof = _profile(posture_distribution={"L1": 0.95, "L2": 0.03, "L3": 0.01, "L4": 0.01})
    segs = build_view(prof, _meta(), None, None)["posture_segments"]
    assert [s["code"] for s in segs] == ["L1", "L2", "L3", "L4"]
    assert segs[0]["label"] == "L1 95%"          # 宽段内嵌标签
    assert segs[1]["label"] == ""                 # 窄段（< 8%）不塞标签
    assert segs[1]["title"] == "L2 3%"            # 但 hover 信息不丢
    assert segs[0]["width_pct"] == pytest.approx(95.0)


def test_posture_segments_skip_zero():
    prof = _profile(posture_distribution={"L1": 0.0, "L2": 0.0, "L3": 0.6, "L4": 0.4})
    segs = build_view(prof, _meta(), None, None)["posture_segments"]
    assert [s["code"] for s in segs] == ["L3", "L4"]


def test_posture_segments_all_zero_is_empty():
    prof = _profile(posture_distribution={})
    view = build_view(prof, _meta(), None, None)
    assert view["posture_segments"] == []
    assert view["posture_pct_text"]["L1"] == "0%"


# ---- 档位（cli stdout 接缝的真相源）----

def test_stage_decided_from_metrics():
    m = _metrics(active_days=20, human_input_count=588, tool_breadth=28,
                 subagent_sessions=12)
    view = build_view(_profile(), _meta(), m, None)
    assert view["stage_name"] == "精通期"
    assert view["stage_no"] == 3
    assert "距下一档还差" in view["stage_crit_note"]


def test_stage_crit_note_all_met():
    m = _metrics(active_days=30, human_input_count=1000, tool_breadth=30,
                 subagent_sessions=12, max_parallel_agents=3, git_landed_count=9)
    view = build_view(_profile(), _meta(), m, None)
    assert view["stage_name"] == "引领期"
    assert view["stage_crit_note"] == "四项判据全部达标"


# ---- diff ----

def test_diff_none_yields_no_delta_anywhere():
    view = build_view(_profile(), _meta(), _metrics(), None)
    assert view["diff_note_kind"] == "none"
    assert view["diff_summary"] == []
    assert all(c["diff"] is None for fam in view["families"] for c in fam["cells"])


def test_diff_baseline_flag():
    view = build_view(_profile(), _meta(), _metrics(), {"baseline": True})
    assert view["diff_note_kind"] == "baseline"
    assert view["diff_summary"] == []


def test_diff_items_skip_no_base_keys():
    diff = {"edit_count": {"now": 886, "prev": 900, "delta": -14, "arrow": "↓"},
            "session_count": {"arrow": None, "delta": None, "no_base": True},
            "tool_breadth": "脏值不是 dict"}
    view = build_view(_profile(), _meta(), _metrics(), diff)
    assert view["diff_note_kind"] == "items"
    assert [lab for lab, _ in view["diff_summary"]] == ["编辑数"]
    assert _cell(view, "产出落地", "编辑数")["diff"] == diff["edit_count"]
    # no_base 键仍挂在格上（由渲染层决定不出箭头），脏值不挂
    assert _cell(view, "节奏投入", "会话数")["diff"] == diff["session_count"]


# ---- 指标明细族 ----

def test_families_shape_and_duration_unit():
    view = build_view(_profile(), _meta(), _metrics(duration_p90_min=50.4), None)
    assert [f["name"] for f in view["families"]] == ["产出落地", "协作编排", "高阶行为", "节奏投入"]
    dur = _cell(view, "节奏投入", "时长 P90")
    assert dur["value"] == "50" and dur["unit"] == "min"
    assert _cell(view, "节奏投入", "会话数")["unit"] is None


def test_families_duration_missing_and_dirty():
    # unmeasured=False：时长任何来源都测得到，取不到只是本窗口没数据（见 dur_cell）
    assert _cell(build_view(_profile(), _meta(), _metrics(), None),
                 "节奏投入", "时长 P90") == {"label": "时长 P90", "value": "—",
                                          "unit": None, "diff": None,
                                          "unmeasured": False}
    dirty = _cell(build_view(_profile(), _meta(), _metrics(duration_p90_min="z"), None),
                  "节奏投入", "时长 P90")
    assert dirty["value"] == "—" and dirty["unit"] is None


def test_model_count_from_token_usage():
    view = build_view(_profile(), _meta(),
                      _metrics(token_usage={"a": {}, "b": {}, "c": {}}), None)
    assert _cell(view, "协作编排", "使用模型数")["value"] == "3"
    assert _cell(build_view(_profile(), _meta(), _metrics(), None),
                 "协作编排", "使用模型数")["value"] == "—"


# ---- 趋势 / 时间线 / token 派生 ----

def test_trend_view_per_session_density_and_ratio():
    trend = {"first_half": {"sessions": 10, "commits": 5, "landed_ratio": 0.4,
                            "override": 30, "error": 9, "short_ratio": 0.2},
             "second_half": {"sessions": 12, "commits": 8, "landed_ratio": 0.75,
                             "override": 40, "error": 5, "short_ratio": 0.1}}
    tv = trend_view(trend)
    assert tv["first_sessions"] == 10 and tv["second_sessions"] == 12
    rows = {r["name"]: r for r in tv["rows"]}
    assert rows["提交（次/会话）"]["a_text"] == "0.50"
    assert rows["提交（次/会话）"]["b_text"] == "0.67"
    assert rows["纠偏锚点（次/会话）"]["arrow"] == "↑"
    assert rows["落地率(观测)"]["a_text"] == "40%"
    assert rows["报错锚点（次/会话）"]["arrow"] == "↓"
    assert rows["极短输入占比"]["arrow"] == "↓"


def test_trend_view_unobservable_commits_drop_rows():
    trend = {"first_half": {"sessions": 2, "commits": 0, "landed_ratio": 0.0,
                            "override": 1, "error": 0, "short_ratio": 0.1},
             "second_half": {"sessions": 2, "commits": 0, "landed_ratio": 0.0,
                             "override": 0, "error": 1, "short_ratio": 0.2}}
    names = [r["name"] for r in trend_view(trend)["rows"]]
    assert not any("提交" in n or "落地率" in n for n in names)


def test_trend_view_none_ratio_shows_dash_without_arrow():
    trend = {"first_half": {"sessions": 10, "commits": 0, "landed_ratio": None,
                            "override": 2, "error": 1, "short_ratio": 0.1},
             "second_half": {"sessions": 12, "commits": 8, "landed_ratio": 0.75,
                             "override": 3, "error": 2, "short_ratio": 0.2}}
    row = next(r for r in trend_view(trend)["rows"] if r["name"] == "落地率(观测)")
    assert row["a_text"] == "—" and row["b_text"] == "75%" and row["arrow"] == ""


def test_trend_view_zero_sessions_no_zero_division():
    trend = {"first_half": {"sessions": 0, "commits": 3, "override": 2,
                            "error": 0, "short_ratio": 0.0, "landed_ratio": 0.1},
             "second_half": {"sessions": 4, "commits": 3, "override": 2,
                             "error": 0, "short_ratio": 0.0, "landed_ratio": 0.1}}
    tv = trend_view(trend)
    assert tv["first_sessions"] == 0
    assert next(r for r in tv["rows"] if r["name"] == "提交（次/会话）")["a_text"] == "0.00"


def test_trend_view_empty():
    assert trend_view(None) is None
    assert trend_view({}) is None


def test_token_items_sorted_desc_and_none_when_all_zero():
    assert token_items({"a": {"output": 10}, "b": {"output": 30}}) == [("b", 30.0), ("a", 10.0)]
    assert token_items({"a": {"output": 0}}) is None
    assert token_items(None) is None


def test_timeline_bars_in_view():
    view = build_view(_profile(), _meta(),
                      _metrics(daily=[{"date": "2026-06-13", "session_count": 4}]), None)
    assert view["timeline_bars"] == timeline_bars([{"date": "2026-06-13", "session_count": 4}])


# ---- 能力盲区 ----

def test_capability_gaps_computed_from_metrics():
    view = build_view(_profile(), _meta(),
                      _metrics(tool_session_counts={"Bash": 10}), None)
    labels = [c["label"] for c in view["capability_gaps"]]
    assert labels                                  # 只用过 Bash → 必有盲区
    assert all(isinstance(x, str) for x in labels)


# ---- 脏输入不炸报告 ----
# metrics 来自 _aggregate.json、profile 由 LLM 生成、window/meta 来自编排端：
# 全是外部 JSON，任何一处 int()/float() 直接吃脏值都会炸掉整张报告——
# 而报告是整条编排链的最终产物，崩在这一步等于前面所有工作作废。
# 降级口径：取不到数就出「—」，绝不静默填 0（0 在本项目是实测真值）。

_脏值形态 = ["n/a", "", "  ", None, True, False, [], {}, [1], {"a": 1},
             float("nan"), float("inf"), float("-inf"), 10 ** 400]


def test_安全取数拒绝非数值且保留零与负数():
    assert safe_num(0) == 0 and safe_num(-3) == -3 and safe_num(2.5) == 2.5
    assert safe_num("14") == 14 and safe_num("2.5") == 2.5      # 数字串仍按数字读
    for 脏 in _脏值形态:
        assert safe_num(脏) is None, 脏
    assert safe_int("14.9") == 14 and safe_int(None) is None


def test_脏落地数不再炸掉整张报告():
    """既有崩溃点：缺 git_landed_count 且 landed_count 为非数字串 → int() 抛 ValueError。"""
    view = build_view(_profile(), _meta(), _metrics(landed_count="n/a"), None)
    assert view["git_landed"] == 37          # 主锚缺、transcript 脏 → 继续退到 LLM outcome
    assert view["dropped"] == 9
    assert _cell(view, "产出落地", "落地提交")["value"] == "37"


def test_脏落地数且无llm兜底时出破折号():
    prof = _profile(outcome={"headline": "h", "landed": 0, "total": 0})
    view = build_view(prof, _meta(), _metrics(landed_count="n/a", commit_count="x"), None)
    assert view["git_landed"] is None and view["dropped"] is None
    assert _cell(view, "产出落地", "落地提交")["value"] == "—"


@pytest.mark.parametrize("脏", _脏值形态)
def test_脏计数值降级成破折号而不是零(脏):
    """0 是实测真值（max_parallel_agents=0 意为「从未并发」），测不到只能出「—」。"""
    view = build_view(_profile(), _meta(),
                      _metrics(max_parallel_agents=脏, tool_breadth=脏,
                               session_count=脏, edit_count=脏,
                               subagent_sessions=脏, duration_p90_min=脏), None)
    assert _cell(view, "高阶行为", "真并行峰值")["value"] == "—"
    assert _cell(view, "节奏投入", "会话数")["value"] == "—"
    assert _cell(view, "产出落地", "编辑数")["value"] == "—"
    assert _cell(view, "协作编排", "SubAgent 会话")["value"] == "—"
    assert _cell(view, "节奏投入", "时长 P90")["value"] == "—"
    assert view["hero_nums"][2]["value"] == "—"


def test_零不被当脏值吞掉():
    view = build_view(_profile(), _meta(),
                      _metrics(max_parallel_agents=0, edit_count=0), None)
    assert _cell(view, "高阶行为", "真并行峰值")["value"] == "0"
    assert _cell(view, "产出落地", "编辑数")["value"] == "0"


def test_脏落地率退回llm口径全脏时出破折号():
    view = build_view(_profile(), _meta(), _metrics(landed_ratio="高"), None)
    assert view["landed_ratio_text"] == f"{37 / 46:.0%}"
    view2 = build_view(_profile(outcome={"landed": "x", "total": "y"}), _meta(),
                       _metrics(landed_ratio="高"), None)
    assert view2["landed_ratio"] is None and view2["landed_ratio_text"] == "—"
    assert view2["radar_axes"][3] == 0.0


def test_git主锚脏值不打断降级链():
    """主锚脏 → 继续退 transcript 硬证据，而不是直接「—」：降级链语义不因防崩被打乱。"""
    view = build_view(_profile(), _meta(),
                      _metrics(git_landed_count="?", landed_count=7,
                               git_commit_total="?"), None)
    assert view["git_landed"] == 7
    assert view["git_commit_total"] is None
    assert _cell(view, "产出落地", "提交总数")["value"] == "—"
    assert view["dropped"] == 39                      # commit_count 46 - landed_count 7


def test_脏丢弃数退回硬证据兜底():
    view = build_view(_profile(), _meta(), _metrics(dropped_count="?"), None)
    assert view["dropped"] == 9                       # 46 - 37


def test_非有限数与超大数按取不到数处理():
    view = build_view(_profile(), _meta(),
                      _metrics(turn_p90=float("inf"), tool_breadth="1e400"), None)
    assert view["hero_nums"][3]["value"] == "—" and view["radar_axes"][2] == 0.0
    assert view["hero_nums"][2]["value"] == "—" and view["radar_axes"][1] == 0.0


def test_负数不炸且雷达轴不越界():
    view = build_view(_profile(), _meta(),
                      _metrics(tool_breadth=-5, turn_p90=-3, landed_ratio=-0.5), None)
    assert view["radar_axes"][1] == 0.0 and view["radar_axes"][2] == 0.0
    assert view["radar_axes"][3] == 0.0
    assert view["hero_nums"][2]["value"] == "-5"      # 原值照实呈现，不粉饰成 0/—


def test_数字字符串仍按数字读():
    view = build_view(_profile(), _meta(), _metrics(tool_breadth="14", turn_p90="10"), None)
    assert view["hero_nums"][2]["value"] == "14"
    assert view["hero_nums"][3]["value"] == "10"


def test_脏姿势分布不炸():
    prof = _profile(posture_distribution={"L1": "高", "L2": None, "L3": [1], "L4": 0.5})
    view = build_view(prof, _meta(), _metrics(), None)
    assert view["posture_pct_text"]["L1"] == "0%"
    assert view["posture_pct_text"]["L4"] == "50%"


def test_姿势分布不是对象时按空处理():
    view = build_view(_profile(posture_distribution=["L3"]), _meta(), _metrics(), None)
    assert view["posture_segments"] == []
    assert view["posture_state"] == "样本不足"


def test_脏决策点数不炸姿态诊断():
    view = build_view(_profile(), _meta(),
                      _metrics(decision_point_count="很多", plan_mode_sessions="三"), None)
    assert view["posture_state"] == "样本不足"        # 数不可读 → 不判，不编结论


def test_metrics不是对象时按缺席降级():
    view = build_view(_profile(), _meta(), ["脏"], None)
    assert view["stage"] is None and view["posture_diag"] is None
    assert view["git_landed"] == 37                   # 退 LLM outcome


def test_profile与meta与diff不是对象时不炸():
    view = build_view("脏", "脏", _metrics(), ["脏"])
    assert view["session_count"] == 0 and view["projects"] == []
    assert view["landed_ratio_text"] == "80%"
    assert view["diff_note_kind"] == "none"


def test_落地率分母为零时不渲染成百分之零():
    """`git_commit_total`=0 时 `landed_ratio` 这个 property 退化成 0.0（0÷0）。

    照 0% 渲染就是把「窗口内没有可归属的提交、落地锚压根建不起来」说成「做了没落地」——
    正是本项目定义的错导。真实场景：会话不在 git 仓库里（opencode 实测就是这样），
    或本人这段时间没往同仓提交。
    """
    m = _metrics(git_landed_count=0, git_commit_total=0, landed_ratio=0.0)
    view = build_view(_profile(), _meta(), m, None)
    assert view["landed_ratio_text"] == "—", "分母为 0 时不得渲染成 0%"
    assert view["landed_ratio"] is None
    # 分母正常时照常出百分比（别把这条守卫改成「永远不出落地率」）
    正常 = build_view(_profile(), _meta(),
                    _metrics(git_landed_count=3, git_commit_total=10, landed_ratio=0.3), None)
    assert 正常["landed_ratio_text"] == "30%"


def test_旧格式缺分母字段时仍信任已给的落地率():
    """分母**整个缺席**是旧 `_aggregate.json`（那时还没有 git 主锚字段）。

    这种情况必须走既有降级链、照常显示，否则历史快照会集体退化成「—」。
    与上一条的区别只有一个：分母是「明确为 0」还是「根本没有」。
    """
    m = _metrics(landed_ratio=0.8)          # 不含 git_commit_total
    assert "git_commit_total" not in m
    assert build_view(_profile(), _meta(), m, None)["landed_ratio_text"] == "80%"


def test_脏outcome块不炸():
    view = build_view(_profile(outcome="落地不错"), _meta(), _metrics(), None)
    assert view["outcome_landed"] == 0.0 and view["outcome_total"] == 0.0
    assert view["landed_ratio_text"] == "80%"         # metrics 口径照常


def test_趋势脏会话数与脏比率不炸():
    trend = {"first_half": {"sessions": "十", "commits": 5, "landed_ratio": "高",
                            "override": "多", "error": 9, "short_ratio": 0.2},
             "second_half": {"sessions": 12, "commits": 8, "landed_ratio": 0.75,
                             "override": 40, "error": 5, "short_ratio": 0.1}}
    tv = trend_view(trend)
    assert tv["first_sessions"] == 0
    row = next(r for r in tv["rows"] if r["name"] == "落地率(观测)")
    assert row["a_text"] == "—" and row["arrow"] == ""   # 脏比率按不可观测，不伪造 0%


def test_趋势半段不是对象时不炸():
    assert trend_view(["脏"]) is None
    assert trend_view({"first_half": "脏", "second_half": ["脏"]}) is not None


def test_时间线跳过脏会话数与负数():
    bars = timeline_bars([{"date": "2026-06-13", "session_count": "四"},
                          {"date": "2026-06-14", "session_count": -3},
                          {"date": "2026-06-15", "session_count": 4}])
    assert [b["date"] for b in bars] == ["2026-06-15"]


def test_token明细脏值降级():
    assert token_items(["脏"]) is None
    assert token_items({"a": "脏", "b": {"output": "多"}}) is None
    assert token_items({"a": "脏", "b": {"output": 5}}) == [("b", 5.0)]


def test_使用模型数只认对象形态的token_usage():
    view = build_view(_profile(), _meta(), _metrics(token_usage="脏"), None)
    assert _cell(view, "协作编排", "使用模型数")["value"] == "—"


def test_条形图跳过非数值计数():
    items, mx = bar_items({"Bash": 10, "Read": "多", "Edit": 3})
    assert items == [("Bash", 10), ("Edit", 3)] and mx == 10.0
    assert bar_items(["脏"]) == ([], 0.0)


def test_能力盲区吃脏metrics不炸():
    view = build_view(_profile(), _meta(),
                      _metrics(tool_session_counts={"Agent": 3},
                               max_parallel_agents="很多",
                               customization_signals={"claude_md_sessions": "几次"}), None)
    labels = [c["label"] for c in view["capability_gaps"]]
    assert "并行 SubAgent" in labels      # 数值不可读 → 按未激活报盲区，不炸


def test_能力盲区的定制信号不是对象时按无依据跳过():
    view = build_view(_profile(), _meta(),
                      _metrics(tool_session_counts=["Bash"],
                               customization_signals="脏"), None)
    labels = [c["label"] for c in view["capability_gaps"]]
    assert "自建 Skill" not in labels     # 无判定依据 → 不报假阳性


# ---- 纯函数纪律 ----

def test_build_view_does_not_mutate_inputs():
    prof, meta, m = _profile(), _meta(), _metrics()
    import copy
    p0, m0, mm0 = copy.deepcopy(prof), copy.deepcopy(meta), copy.deepcopy(m)
    build_view(prof, meta, m, None)
    assert prof == p0 and meta == m0 and m == mm0


def test_build_view_has_no_html():
    """view 里不得出现 HTML 片段——像素归 report.py，这层只出数据。"""
    view = build_view(_profile(), _meta(), _metrics(duration_p90_min=50.4),
                      {"edit_count": {"now": 1, "prev": 0, "delta": 1, "arrow": "↑"}})
    def walk(v):
        if isinstance(v, str):
            assert "<" not in v and "&" not in v, v
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
    walk(view)
