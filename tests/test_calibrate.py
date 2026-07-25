"""calibrate：把阈值从「拍脑袋」变成「有据可依」的校准通道。

承重取向（违反即 bug）：
- 诚实第一——样本不足必须出声，不得静默给出看起来很确定的分位数字；
  快照里根本没记的指标必须标「不可测」，不得用别的指标顶包。
- 隐私——只吃已脱敏的标量快照，输出里不出现任何项目名/路径。
- 纯函数——全部决策逻辑无 IO，直接喂 dict 测。
"""
import json

import math

from ai_coding_insights.calibrate import (
    DIR_CEILING, DIR_FLOOR, DIR_SCALE, DIR_TEXT,
    MIN_PERCENTILE_SAMPLES, MIN_RELIABLE_SAMPLES,
    _fmt, calibrate, describe, extract_series, format_report, locate_threshold,
    percentile, read_percentile, sample_caveat, threshold_specs,
)
from ai_coding_insights.cli import main
from ai_coding_insights.snapshot import CURRENT_POSTURE_RUBRIC, save_snapshot
from ai_coding_insights.stage import DEFAULT_POSTURE_BANDS, DEFAULT_STAGE_THRESHOLDS


def _snap(metrics=None, posture=None, rubric=CURRENT_POSTURE_RUBRIC,
          generated_at="2026-06-10T00:00:00+00:00"):
    return {"generated_at": generated_at, "window": {"days": 30},
            "metrics": metrics or {}, "posture_distribution": posture or {},
            "posture_rubric": rubric, "outcome": {}}


# ---------- 分位数：小样本边界 ----------

def test_percentile_空样本返回None():
    assert percentile([], 0.5) is None


def test_percentile_单样本恒返回该值():
    for q in (0.0, 0.25, 0.5, 0.75, 1.0):
        assert percentile([7.0], q) == 7.0


def test_percentile_双样本线性插值():
    # n=2 时 p25 落在两点之间：0 + 0.25*(1-0) * (10-0) = 2.5
    assert percentile([0.0, 10.0], 0.25) == 2.5
    assert percentile([0.0, 10.0], 0.5) == 5.0
    assert percentile([10.0, 0.0], 1.0) == 10.0   # 输入乱序也先排序


def test_percentile_五样本取整数位():
    vals = [1, 2, 3, 4, 5]
    assert percentile(vals, 0.0) == 1
    assert percentile(vals, 0.25) == 2
    assert percentile(vals, 0.5) == 3
    assert percentile(vals, 0.75) == 4
    assert percentile(vals, 1.0) == 5


def test_describe_给出五数概括与样本数():
    d = describe([1, 2, 3, 4, 5])
    assert d["n"] == 5
    assert (d["min"], d["p25"], d["median"], d["p75"], d["max"]) == (1, 2, 3, 4, 5)


def test_describe_零样本各统计量为None且带警告():
    d = describe([])
    assert d["n"] == 0
    assert d["min"] is d["median"] is d["max"] is None
    assert d["caveat"] and "样本" in d["caveat"]


# ---------- 阈值定位 ----------

def test_locate_threshold_空样本返回None():
    assert locate_threshold([], 5) is None


def test_locate_threshold_低于全部观测为零分位():
    assert locate_threshold([10, 20, 30], 1) == 0.0


def test_locate_threshold_高于全部观测为满分位():
    assert locate_threshold([10, 20, 30], 99) == 1.0


def test_locate_threshold_居中给出比例():
    # 4 个观测中 1 个严格小于 15 → 0.25
    assert locate_threshold([10, 20, 30, 40], 15) == 0.25


def test_locate_threshold_并列取中位秩():
    # 2 个小于、2 个等于 → (2 + 2/2) / 5 = 0.6，避免全等时假装满分位
    assert locate_threshold([1, 2, 5, 5, 9], 5) == 0.6
    assert locate_threshold([5, 5], 5) == 0.5


# ---------- 样本量出声 ----------

def test_sample_caveat_分级出声():
    assert sample_caveat(0) is not None
    assert str(MIN_PERCENTILE_SAMPLES - 1) in sample_caveat(MIN_PERCENTILE_SAMPLES - 1)
    assert sample_caveat(MIN_RELIABLE_SAMPLES - 1) is not None
    assert sample_caveat(MIN_RELIABLE_SAMPLES) is None


def test_sample_caveat_不可靠时不许静默():
    # 3 个快照必须明确说「不可靠」，且把样本数摆出来
    msg = sample_caveat(3)
    assert "3" in msg and "不可靠" in msg


# ---------- 序列提取 ----------

def test_extract_series_只取白名单标量():
    series = extract_series([_snap({"active_days": 4, "tool_breadth": 9})])
    assert series["active_days"] == [4.0]
    assert series["tool_breadth"] == [9.0]


def test_extract_series_跳过None与布尔与字符串():
    series = extract_series([_snap({"active_days": None, "tool_breadth": True,
                                    "human_input_count": "80"})])
    assert series["active_days"] == []
    assert series["tool_breadth"] == []
    assert series["human_input_count"] == []


def test_extract_series_忽略非白名单键():
    # 白名单外的键不进序列——防新数据源从这里悄悄溜进来
    series = extract_series([_snap({"project_breakdown": 3, "active_days": 2})])
    assert "project_breakdown" not in series


def test_extract_series_姿态派生三条序列():
    series = extract_series([_snap(posture={"L1": 0.4, "L2": 0.2, "L3": 0.3, "L4": 0.1})])
    assert series["posture_L3"] == [0.3]
    assert series["posture_L4"] == [0.1]
    assert series["posture_L3+L4"] == [0.4]


def test_extract_series_百分数形态的姿态先归一():
    series = extract_series([_snap(posture={"L1": 40, "L2": 20, "L3": 30, "L4": 10})])
    assert series["posture_L3"] == [0.3]


def test_extract_series_空姿态不当作零观测():
    # 全零/缺失姿态代表「没有观测」，计成 0.0 会把分布往下拽 → 必须整条跳过
    series = extract_series([_snap(posture={}),
                             _snap(posture={"L1": 0, "L2": 0, "L3": 0, "L4": 0}),
                             _snap(posture={"L3": 0.5, "L4": 0.1})])
    assert series["posture_L3"] == [0.5]


def test_extract_series_旧口径样本剔除口径敏感键():
    old = _snap({"landed_ratio": 0.9, "git_landed_count": 7, "active_days": 5},
                rubric=CURRENT_POSTURE_RUBRIC - 1,
                generated_at="2026-05-01T00:00:00+00:00")
    new = _snap({"landed_ratio": 0.3, "git_landed_count": 2, "active_days": 6})
    series = extract_series([old, new])
    assert series["landed_ratio"] == [0.3]        # 旧口径落地率不可比，剔除
    assert series["git_landed_count"] == [2.0]
    assert series["active_days"] == [5.0, 6.0]    # 口径无关的键照收


def test_extract_series_旧口径样本剔除姿态序列():
    old = _snap(posture={"L3": 0.9, "L4": 0.05}, rubric=1)
    new = _snap(posture={"L3": 0.2, "L4": 0.1})
    series = extract_series([old, new])
    assert series["posture_L3"] == [0.2]


# ---------- 快照扩键后的新序列（向后兼容 + 派生合成信号）----------

def test_extract_series_覆盖新扩的纯标量键():
    series = extract_series([_snap({"decision_point_count": 120, "plan_mode_sessions": 3,
                                    "turn_p90": 18, "custom_skill_count": 4,
                                    "background_sessions": 2, "max_parallel_agents": 3})])
    assert series["decision_point_count"] == [120.0]
    assert series["plan_mode_sessions"] == [3.0]
    assert series["turn_p90"] == [18.0]
    assert series["custom_skill_count"] == [4.0]
    assert series["background_sessions"] == [2.0]
    assert series["max_parallel_agents"] == [3.0]


def test_extract_series_旧快照缺新键不当作零观测():
    # 老快照根本没记这些键，计成 0 会把分布整体往下拽、造出「新指标一路下滑」的伪像
    old = _snap({"active_days": 5})
    new = _snap({"active_days": 6, "turn_p90": 20, "decision_point_count": 90})
    series = extract_series([old, new])
    assert series["turn_p90"] == [20.0]
    assert series["decision_point_count"] == [90.0]
    assert series["active_days"] == [5.0, 6.0]


# ---------- 脏快照：合法 JSON、值语义脏（单个脏样本不得炸掉整次校准） ----------

def test_extract_series_非有限数不当观测():
    """JSON 允许 Infinity/NaN 字面量，超大整数也合法——它们进了序列会污染整张分布表。

    与 view_model.safe_num 同口径：取不到数就是取不到，不补 0、不放行。
    """
    脏 = _snap({"active_days": float("inf"), "tool_breadth": float("nan"),
              "human_input_count": 10 ** 400})
    series = extract_series([脏, _snap({"active_days": 5, "tool_breadth": 3,
                                       "human_input_count": 7})])
    assert series["active_days"] == [5.0]
    assert series["tool_breadth"] == [3.0]
    assert series["human_input_count"] == [7.0]


def test_extract_series_姿态脏值不炸并整条跳过():
    """posture_distribution 原来是裸 float() 进 normalize_posture，一个脏档就抛 ValueError。"""
    脏 = _snap(posture={"L1": "脏", "L2": 0.2, "L3": 0.3, "L4": 0.1})
    好 = _snap(posture={"L1": 0.4, "L2": 0.2, "L3": 0.3, "L4": 0.1})
    series = extract_series([脏, 好])
    assert series["posture_L3"] == [0.3]     # 脏样本整条跳过，不半信半疑地补 0


def test_extract_series_姿态非有限数也整条跳过():
    脏 = _snap(posture={"L1": float("inf"), "L2": 0.0, "L3": 0.3, "L4": 0.1})
    assert extract_series([脏])["posture_L3"] == []


def test_fmt_非有限值显示为破折号():
    # round(inf) / round(nan) 直接抛，会把整张报告炸掉；缺就是缺，出「—」
    assert _fmt(float("inf")) == "—"
    assert _fmt(float("nan")) == "—"
    assert _fmt(10 ** 400) == "—"
    assert _fmt("脏") == "—"
    assert _fmt(3) == "3"


def test_calibrate_单个脏快照只少一条样本不整体崩():
    脏 = _snap({"active_days": float("inf")},
             posture={"L1": "脏", "L2": 1.0, "L3": 0.0, "L4": 0.0})
    好 = [_snap({"active_days": i + 1}, posture={"L1": 0.4, "L2": 0.2, "L3": 0.3, "L4": 0.1},
              generated_at=f"2026-06-{i + 1:02d}T00:00:00+00:00") for i in range(3)]
    result = calibrate([脏, *好])
    assert result["sample_count"] == 4                       # 快照本身仍算数
    assert result["distributions"]["active_days"]["n"] == 3  # 脏值那条观测被剔除
    text = format_report(result)                             # 渲染不得抛
    assert "active_days" in text
    assert not any(math.isinf(v) or math.isnan(v)
                   for d in result["distributions"].values()
                   for v in d.values() if isinstance(v, float))


def test_extract_series_派生深度信号取子代理与plan较大者():
    series = extract_series([_snap({"subagent_sessions": 2, "plan_mode_sessions": 7})])
    assert series["depth_signal"] == [7.0]


def test_extract_series_派生高阶编排按分项各自过门计数():
    # 默认门：并行 ≥2、后台 ≥2、自建 ≥1 —— 这里只有后台与自建过门 → 2 项
    series = extract_series([_snap({"max_parallel_agents": 1, "background_sessions": 3,
                                    "custom_skill_count": 1})])
    assert series["advanced_orchestration"] == [2.0]


def test_extract_series_派生序列分项不全时整条跳过():
    # 分项缺一就无法回算，补 0 会编出假的「没有高阶编排」
    series = extract_series([_snap({"max_parallel_agents": 3, "background_sessions": 3}),
                             _snap({"subagent_sessions": 1})])
    assert series["advanced_orchestration"] == []
    assert series["depth_signal"] == []


# ---------- 阈值清单 ----------

def test_threshold_specs_覆盖两组阈值且字段齐全():
    specs = threshold_specs(DEFAULT_STAGE_THRESHOLDS, DEFAULT_POSTURE_BANDS)
    names = {s["name"] for s in specs}
    # 成熟度闸门与姿态健康带的每个字段都要在清单里，不许漏项
    assert names >= set(vars(DEFAULT_STAGE_THRESHOLDS))
    assert names >= set(vars(DEFAULT_POSTURE_BANDS))
    for s in specs:
        assert s["group"] and s["name"] and s["value"] is not None
        assert "metric" in s


def test_threshold_specs_原不可测阈值随快照扩键变为可测():
    specs = {s["name"]: s for s in threshold_specs(DEFAULT_STAGE_THRESHOLDS,
                                                   DEFAULT_POSTURE_BANDS)}
    # 这批阈值曾因快照没记对应指标而结构性不可测；_CORE_KEYS 补齐纯标量后必须转为可测，
    # 否则「不可测」标注就成了过期的假话
    assert specs["s4_parallel_min"]["metric"] == "max_parallel_agents"
    assert specs["s4_background_min"]["metric"] == "background_sessions"
    assert specs["s4_custom_min"]["metric"] == "custom_skill_count"
    assert specs["min_decision_points"]["metric"] == "decision_point_count"
    assert specs["min_handsoff_plan_sessions"]["metric"] == "plan_mode_sessions"


def test_threshold_specs_合成信号走派生序列():
    specs = {s["name"]: s for s in threshold_specs(DEFAULT_STAGE_THRESHOLDS,
                                                   DEFAULT_POSTURE_BANDS)}
    # 高阶编排/深度信号是分项合成的，分项齐了就能精确回算，不再是「不可测」或下界近似
    assert specs["s4_advanced"]["metric"] == "advanced_orchestration"
    assert specs["s3_depth_signal"]["metric"] == "depth_signal"


def test_threshold_specs_雷达打满线也进清单():
    specs = {s["name"]: s for s in threshold_specs(DEFAULT_STAGE_THRESHOLDS,
                                                   DEFAULT_POSTURE_BANDS)}
    assert specs["RADAR_BREADTH_FULL"]["metric"] == "tool_breadth"
    assert specs["RADAR_DEPTH_FULL_TURNS"]["metric"] == "turn_p90"


# ---------- 阈值方向（下限门 / 上限门 / 刻度）----------

def test_threshold_specs_每条阈值都标方向():
    for s in threshold_specs(DEFAULT_STAGE_THRESHOLDS, DEFAULT_POSTURE_BANDS):
        assert s["direction"] in (DIR_FLOOR, DIR_CEILING, DIR_SCALE, DIR_TEXT), s["name"]


def test_threshold_specs_成熟度闸门全是下限门():
    specs = {s["name"]: s for s in threshold_specs(DEFAULT_STAGE_THRESHOLDS,
                                                   DEFAULT_POSTURE_BANDS)}
    for name in vars(DEFAULT_STAGE_THRESHOLDS):
        assert specs[name]["direction"] == DIR_FLOOR, name


def test_threshold_specs_姿态带里只有l4上限是上限门():
    specs = {s["name"]: s for s in threshold_specs(DEFAULT_STAGE_THRESHOLDS,
                                                   DEFAULT_POSTURE_BANDS)}
    # diagnose_posture 里只有 `l4 > l4_healthy_ceiling` 是「超过即判超」，其余都是「达到才算」
    assert specs["l4_healthy_ceiling"]["direction"] == DIR_CEILING
    for name in ("min_decision_points", "guide_floor", "min_handsoff_plan_sessions"):
        assert specs[name]["direction"] == DIR_FLOOR, name


# ---------- 空转旋钮：只改文案、不改判定的阈值必须被单独标出 ----------

def test_stage_的两条健康下沿对判定完全无影响():
    """真相源侧的事实钉子：`l3_healthy_floor` / `l4_healthy_floor` 不改变 state。

    `diagnose_posture` 过了 ceiling 与 guide_floor 之后，两条分支都 `return 健康`，
    这两个阈值只决定 reason 措辞。calibrate 因此把它们标成 DIR_TEXT。
    哪天有人让它们真的分档，这条测试会红——那时必须回 calibrate 把方向改回 DIR_FLOOR，
    否则表里会挂着一条「不起作用」的假说明。两侧靠这条测试锁在一起。
    """
    from ai_coding_insights.stage import PostureBands, diagnose_posture

    极高 = PostureBands(l3_healthy_floor=0.99, l4_healthy_floor=0.99)
    极低 = PostureBands(l3_healthy_floor=0.0, l4_healthy_floor=0.0)
    样本 = [({"L1": 0.40, "L2": 0.35, "L3": 0.05, "L4": 0.20}, 100),
          ({"L1": 0.10, "L2": 0.10, "L3": 0.70, "L4": 0.10}, 60),
          ({"L1": 0.30, "L2": 0.20, "L3": 0.50, "L4": 0.00}, 30)]
    for pd, dp in 样本:
        默认 = diagnose_posture(pd, dp)["state"]
        assert diagnose_posture(pd, dp, bands=极高)["state"] == 默认, pd
        assert diagnose_posture(pd, dp, bands=极低)["state"] == 默认, pd


def test_threshold_specs_空转旋钮标为文案参数而非下限门():
    """把「不起作用的旋钮」混在真门里，比方向写反更难被发现：照着调了，输出一个字不变。"""
    specs = {s["name"]: s for s in threshold_specs(DEFAULT_STAGE_THRESHOLDS,
                                                   DEFAULT_POSTURE_BANDS)}
    for name in ("l3_healthy_floor", "l4_healthy_floor"):
        assert specs[name]["direction"] == DIR_TEXT, name
        assert specs[name]["note"], f"{name} 必须写明它只影响文案"
        assert "文案" in specs[name]["note"] or "措辞" in specs[name]["note"]


def test_read_percentile_文案参数不给过门读法():
    for p in (0.0, 0.5, 1.0):
        txt = read_percentile(DIR_TEXT, p)
        assert txt
        assert "过门" not in txt and "形同虚设" not in txt and "超过上限" not in txt
        assert "不改变" in txt or "不影响" in txt


def test_format_report_文案参数不与真门共用一套读法():
    snaps = [_snap(posture={"L1": 0.4, "L2": 0.2, "L3": 0.3, "L4": 0.1},
                   generated_at=f"2026-06-{i + 1:02d}T00:00:00+00:00")
             for i in range(MIN_RELIABLE_SAMPLES)]
    text = format_report(calibrate(snaps))
    row = _thr_row(text, "l3_healthy_floor")
    assert "下限门" not in row and "过门" not in row
    assert "文案参数" in row
    # 图例也要单列一条，否则读者仍会套「过门/未过门」那套图例去读它
    图例 = "\n".join(_section(text, "== 当前阈值定位 ==").splitlines()[0:6])
    assert "文案参数" in 图例


def test_threshold_specs_雷达打满线是刻度不是门():
    specs = {s["name"]: s for s in threshold_specs(DEFAULT_STAGE_THRESHOLDS,
                                                   DEFAULT_POSTURE_BANDS)}
    assert specs["RADAR_BREADTH_FULL"]["direction"] == DIR_SCALE
    assert specs["RADAR_DEPTH_FULL_TURNS"]["direction"] == DIR_SCALE


def test_read_percentile_下限门零分位读作人人过门():
    txt = read_percentile(DIR_FLOOR, 0.0)
    assert "过门" in txt or "形同虚设" in txt


def test_read_percentile_上限门零分位读作次次超限():
    # 关键反向用例：上限门的 0% 分位＝所有观测都在阈值之上＝每次都被判超，
    # 绝不能沿用下限门的「人人过门/形同虚设」读法（方向正好相反）
    txt = read_percentile(DIR_CEILING, 0.0)
    assert "超" in txt
    assert "形同虚设" not in txt and "人人过门" not in txt


def test_read_percentile_上限门满分位读作从未触及():
    txt = read_percentile(DIR_CEILING, 1.0)
    assert "从未" in txt or "限内" in txt


def test_read_percentile_刻度线两端读作截顶与压扁():
    assert "截顶" in read_percentile(DIR_SCALE, 0.0)
    assert "压扁" in read_percentile(DIR_SCALE, 1.0)


def test_read_percentile_无分位返回空串():
    assert read_percentile(DIR_FLOOR, None) == ""


# ---------- 汇总 ----------

def _many(n, start_days=1):
    return [_snap({"active_days": start_days + i, "tool_breadth": 3 + i,
                   "human_input_count": 50 * (i + 1)},
                  generated_at=f"2026-06-{i + 1:02d}T00:00:00+00:00")
            for i in range(n)]


def test_calibrate_给出分布与阈值分位定位():
    result = calibrate(_many(MIN_RELIABLE_SAMPLES))
    assert result["sample_count"] == MIN_RELIABLE_SAMPLES
    dist = result["distributions"]["active_days"]
    assert dist["n"] == MIN_RELIABLE_SAMPLES
    assert dist["min"] == 1 and dist["max"] == MIN_RELIABLE_SAMPLES
    by_name = {t["name"]: t for t in result["thresholds"]}
    s2 = by_name["s2_active_days"]
    assert s2["metric"] == "active_days"
    assert 0.0 <= s2["percentile"] <= 1.0
    assert s2["n"] == MIN_RELIABLE_SAMPLES


def test_calibrate_样本充足时不挂警告():
    result = calibrate(_many(MIN_RELIABLE_SAMPLES))
    assert result["reliable"] is True
    assert result["caveat"] is None


def test_calibrate_样本不足必须出声():
    result = calibrate(_many(3))
    assert result["reliable"] is False
    assert "3" in result["caveat"] and "不可靠" in result["caveat"]
    by_name = {t["name"]: t for t in result["thresholds"]}
    # 逐条阈值也要挂警告，防止只在页头说一句、下面数字照样显得很确定
    assert by_name["s2_active_days"]["caveat"]


def test_calibrate_零快照不崩且明说无样本():
    result = calibrate([])
    assert result["sample_count"] == 0
    assert result["reliable"] is False
    assert result["caveat"]
    by_name = {t["name"]: t for t in result["thresholds"]}
    assert by_name["s2_active_days"]["percentile"] is None


def test_calibrate_不可测阈值不给分位数():
    # measurable=False 是「快照结构上就没有对应观测」的通路，当前无阈值走它，
    # 但通路本身必须保持：不可测就不给数字，绝不用别的指标顶包
    result = {"thresholds": [{"group": "G", "name": "x", "value": 1, "metric": None,
                              "note": "快照未记录 x", "direction": DIR_FLOOR,
                              "measurable": False, "n": 0, "percentile": None,
                              "caveat": None}]}
    text = format_report(result)
    assert "不可测" in text and "快照未记录 x" in text


def test_calibrate_可测但该键无样本时标不可测原因():
    # 有快照但全都没记 git_landed_count → 分位必须为 None 而不是编一个
    by_name = {t["name"]: t for t in calibrate(_many(6))["thresholds"]}
    spec = by_name["s4_git_landed"]
    assert spec["metric"] == "git_landed_count"
    assert spec["percentile"] is None
    assert spec["n"] == 0


def test_calibrate_记录快照日期跨度():
    result = calibrate(_many(3))
    assert result["span"] == {"first": "2026-06-01", "last": "2026-06-03"}
    assert calibrate([])["span"] == {"first": None, "last": None}


def test_calibrate_统计跨口径被剔除的样本数():
    snaps = _many(2) + [_snap({"landed_ratio": 0.9}, rubric=1)]
    result = calibrate(snaps)
    assert result["stale_rubric_count"] == 1


def test_calibrate_可整体替换阈值组():
    from ai_coding_insights.stage import StageThresholds
    custom = StageThresholds(s2_active_days=999)
    by_name = {t["name"]: t
               for t in calibrate(_many(6), thresholds=custom)["thresholds"]}
    assert by_name["s2_active_days"]["value"] == 999
    assert by_name["s2_active_days"]["percentile"] == 1.0   # 高于所有观测


# ---------- 文本渲染 ----------

def test_format_report_含样本警告与不可测标注():
    text = format_report(calibrate(_many(3)))
    assert "不可靠" in text
    assert "不可测" in text
    assert "active_days" in text


def test_format_report_逐条阈值行都带不可靠标记():
    text = format_report(calibrate(_many(3)))
    rows = [ln for ln in text.splitlines() if "分位（n=" in ln]
    assert rows and all("⚠ 不可靠" in ln for ln in rows)


def test_format_report_样本充足时不挂不可靠():
    text = format_report(calibrate(_many(MIN_RELIABLE_SAMPLES)))
    assert "⚠ 不可靠" not in text


def test_format_report_零样本也能渲染():
    text = format_report(calibrate([]))
    assert "0" in text and "样本" in text


def _section(text, start, end=None):
    body = text.split(start)[1]
    return body.split(end)[0] if end else body


def _dist_row(text, key):
    section = _section(text, "== 指标分布 ==", "== 当前阈值定位 ==")
    for ln in section.splitlines():
        if ln.split() and ln.split()[0] == key:
            return ln
    raise AssertionError(f"分布表里没有 {key} 这一行")


def _thr_row(text, name):
    for ln in _section(text, "== 当前阈值定位 ==").splitlines():
        if ln.strip().startswith(name + " "):
            return ln
    raise AssertionError(f"阈值表里没有 {name} 这一行")


def test_format_report_分布表单序列样本不足时逐行出声():
    # 总样本达标（页头说「可作为依据」）但某指标只有 1 个观测：分布表那一行必须自己出声，
    # 否则整屏一个 ⚠ 都没有，看的人会拿 1 个观测当分布用
    snaps = _many(MIN_RELIABLE_SAMPLES + 5)
    snaps[0]["metrics"]["landed_ratio"] = 0.93
    result = calibrate(snaps)
    text = format_report(result)
    assert result["caveat"] is None                      # 页头确实报「达标」
    assert result["distributions"]["landed_ratio"]["n"] == 1
    row = _dist_row(text, "landed_ratio")
    assert "⚠" in row and "1" in row


def test_format_report_分布表零样本行标无样本():
    text = format_report(calibrate(_many(MIN_RELIABLE_SAMPLES)))
    assert "⚠ 无样本" in _dist_row(text, "landed_ratio")


def test_format_report_分布表样本足的行不挂警告():
    text = format_report(calibrate(_many(MIN_RELIABLE_SAMPLES)))
    assert "⚠" not in _dist_row(text, "active_days")


def test_format_report_图例分方向给读法():
    text = format_report(calibrate(_many(3)))
    legend = _section(text, "== 当前阈值定位 ==").splitlines()[0:5]
    legend_txt = "\n".join(legend)
    assert "下限门" in legend_txt and "上限门" in legend_txt and "刻度" in legend_txt


def test_format_report_上限门零分位不读成人人过门():
    # 复现实测坑：L4 稳定 30% > 上限 20% → 分位 0%，若沿用下限门读法会被读成
    # 「门形同虚设」进而调低上限，而真相是每个窗口都被判「偏对抗」，方向正好反了
    snaps = [_snap(posture={"L1": 0.4, "L2": 0.0, "L3": 0.3, "L4": 0.3},
                   generated_at=f"2026-06-{i + 1:02d}T00:00:00+00:00")
             for i in range(MIN_RELIABLE_SAMPLES)]
    text = format_report(calibrate(snaps))
    row = _thr_row(text, "l4_healthy_ceiling")
    assert "第 0% 分位" in row
    assert "上限门" in row and "超" in row
    assert "形同虚设" not in row and "人人过门" not in row


def test_format_report_下限门读法保留():
    snaps = _many(MIN_RELIABLE_SAMPLES)
    row = _thr_row(format_report(calibrate(snaps)), "s2_active_days")
    assert "下限门" in row


def test_format_report_不泄露路径与项目名():
    # 输出只允许出现指标名与数字：不含路径分隔的目录、不含 home 前缀
    text = format_report(calibrate(_many(6)))
    assert "/Users" not in text and "~/" not in text
    assert ".json" not in text


# ---------- CLI ----------

def _write_snaps(dir, n):
    for i in range(n):
        save_snapshot({"active_days": i + 1, "tool_breadth": 3 + i,
                       "human_input_count": 50 * (i + 1)},
                      {"L1": 0.4, "L2": 0.2, "L3": 0.3, "L4": 0.1},
                      {"landed": 1, "total": 2},
                      f"2026-06-{i + 1:02d}T00:00:00+00:00", {"days": 30}, dir=dir)


def test_load_all_按日期升序且跳过杂散与损坏(tmp_path):
    # calibrate 的取数入口（snapshot.load_all）：与 load_latest 同一套文件名/容错规则
    from ai_coding_insights.snapshot import load_all
    _write_snaps(tmp_path, 3)
    (tmp_path / "2026-06-09.json").write_text("{坏", encoding="utf-8")
    (tmp_path / "notes.json").write_text("{}", encoding="utf-8")
    loaded = load_all(tmp_path)
    assert [s["generated_at"][:10] for s in loaded] == ["2026-06-01", "2026-06-02",
                                                        "2026-06-03"]
    assert load_all(tmp_path / "不存在") == []


def test_cli_calibrate_默认输出人类可读文本(tmp_path, capsys):
    _write_snaps(tmp_path, 3)
    assert main(["calibrate", "--snapshot-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "active_days" in out
    assert "不可靠" in out          # 3 个快照必须出声
    assert not out.lstrip().startswith("{")


def test_cli_calibrate_json输出可解析(tmp_path, capsys):
    _write_snaps(tmp_path, 3)
    assert main(["calibrate", "--snapshot-dir", str(tmp_path), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["sample_count"] == 3
    assert data["reliable"] is False
    assert data["caveat"]
    assert any(t["name"] == "s2_active_days" for t in data["thresholds"])


def test_cli_calibrate_空目录不崩并明说无样本(tmp_path, capsys):
    assert main(["calibrate", "--snapshot-dir", str(tmp_path / "不存在")]) == 0
    out = capsys.readouterr().out
    assert "样本" in out


def test_cli_calibrate_跳过损坏与杂散文件(tmp_path, capsys):
    _write_snaps(tmp_path, 2)
    (tmp_path / "2026-06-09.json").write_text("{坏掉的 json", encoding="utf-8")
    (tmp_path / "notes.json").write_text('{"metrics": {"active_days": 999}}',
                                         encoding="utf-8")
    assert main(["calibrate", "--snapshot-dir", str(tmp_path), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["sample_count"] == 2                      # 坏文件与非日期名都不算样本
    assert data["distributions"]["active_days"]["max"] == 2
