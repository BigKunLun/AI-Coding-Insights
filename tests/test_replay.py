from datetime import date

from ai_coding_insights.calibrate import replay_windows


def test_replay_不重叠切片按窗口长度等分():
    ws = replay_windows(date(2026, 6, 1), date(2026, 7, 30), window_days=30, step_days=30)
    assert len(ws) == 2
    # 升序返回，且每片恰好 window_days 天（右开区间）
    for since, until in ws:
        assert (until - since).days == 30
    assert ws[0][0] < ws[1][0]
    # 最后一片必须贴到数据末尾（含 last_day 当天），否则最近的数据被丢掉
    assert ws[-1][1] == date(2026, 7, 31)


def test_replay_滑动步长小于窗口时切片重叠且更密():
    ws = replay_windows(date(2026, 6, 1), date(2026, 7, 30), window_days=30, step_days=7)
    assert len(ws) > 2
    assert all((u - s).days == 30 for s, u in ws)
    # 相邻片起点相差正好一个步长
    assert (ws[1][0] - ws[0][0]).days == 7


def test_replay_数据不足一个窗口时返回空():
    assert replay_windows(date(2026, 7, 1), date(2026, 7, 10), window_days=30) == []


def test_replay_窗口或步长非正时返回空():
    a, b = date(2026, 6, 1), date(2026, 7, 30)
    assert replay_windows(a, b, window_days=0) == []
    assert replay_windows(a, b, window_days=30, step_days=0) == []


def test_replay_起止倒置返回空():
    assert replay_windows(date(2026, 7, 30), date(2026, 6, 1), window_days=30) == []


def test_replay_默认步长等于窗口不重叠():
    ws = replay_windows(date(2026, 5, 1), date(2026, 7, 30), window_days=30)
    starts = [s for s, _ in ws]
    assert all((starts[i + 1] - starts[i]).days == 30 for i in range(len(starts) - 1))


from ai_coding_insights.calibrate import (REPLAY_UNMEASURED, calibrate,
                                          replay_snapshot, window_indices)


def test_归片_会话按last_day落入对应窗口():
    ws = [(date(2026, 6, 1), date(2026, 7, 1)), (date(2026, 7, 1), date(2026, 8, 1))]
    days = [date(2026, 6, 15), date(2026, 7, 20), date(2026, 5, 1)]
    assert window_indices(days, ws) == [[0], [1]]   # 5月那条在任何窗口外


def test_归片_右开区间边界归下一窗不重复计():
    ws = [(date(2026, 6, 1), date(2026, 7, 1)), (date(2026, 7, 1), date(2026, 8, 1))]
    assert window_indices([date(2026, 7, 1)], ws) == [[], [0]]


def test_归片_重叠窗口下同一会话进多个窗口():
    ws = [(date(2026, 6, 1), date(2026, 7, 1)), (date(2026, 6, 8), date(2026, 7, 8))]
    assert window_indices([date(2026, 6, 20)], ws) == [[0], [0]]


def test_归片_时间戳不可解析的会话不进任何窗口():
    ws = [(date(2026, 6, 1), date(2026, 7, 1))]
    assert window_indices([None], ws) == [[]]


def test_伪快照_未测量的键整键不放而非填零():
    snap = replay_snapshot(date(2026, 7, 1),
                           {"active_days": 12, "git_landed_count": 0,
                            "landed_ratio": 0.0, "git_commit_total": 0})
    assert snap["metrics"]["active_days"] == 12
    for k in REPLAY_UNMEASURED:
        assert k not in snap["metrics"], k


def test_伪快照_自建技能数整键不放而非填零():
    """自建技能数来自**文件系统当下状态**，不是任何历史切片的观测。

    回归：`_replay_snapshots` 调 aggregate_metrics 时没传 custom_skill_count，
    默认值 0 一路进伪快照，calibrate 于是把 s4_custom_min 定位成「100% 分位 /
    无人过门」，诱导人把门调低——而真实 scan 口径下这台机器是 32。
    「未测量 ≠ 0」是本项目的承重约束，回放通道不能例外。
    """
    snap = replay_snapshot(date(2026, 7, 1), {"active_days": 12, "custom_skill_count": 0})
    assert "custom_skill_count" not in snap["metrics"]
    assert "custom_skill_count" in REPLAY_UNMEASURED


def test_回放快照里高阶编排整条跳过而非按缺分项算低():
    """advanced_orchestration 的三个分项缺一，整条派生观测就该跳过。

    拿 2/3 个分项凑出来的合成值必然偏低，比没有观测更坏——它看起来像真数据。
    """
    snaps = [replay_snapshot(date(2026, 7, i + 1),
                             {"active_days": 12, "max_parallel_agents": 3,
                              "background_sessions": 4, "custom_skill_count": 0})
             for i in range(3)]
    rows = {r["name"]: r for r in calibrate(snaps)["thresholds"]}
    assert rows["s4_custom_min"]["n"] == 0
    assert rows["s4_advanced"]["n"] == 0


def test_伪快照_不带姿态分布():
    snap = replay_snapshot(date(2026, 7, 1), {"active_days": 3})
    assert "posture_distribution" not in snap


def test_伪快照_白名单外的键不进快照():
    snap = replay_snapshot(date(2026, 7, 1),
                           {"active_days": 3, "project_breakdown": {"/私有/路径": 1}})
    assert "project_breakdown" not in snap["metrics"]


def test_伪快照_日期取窗口末日且不含路径():
    snap = replay_snapshot(date(2026, 7, 31), {"active_days": 3})
    assert snap["generated_at"].startswith("2026-07-30")


def test_回放快照喂进calibrate后git阈值如实报无样本():
    snaps = [replay_snapshot(date(2026, 7, 1), {"active_days": 12}) for _ in range(3)]
    rows = {r["name"]: r for r in calibrate(snaps)["thresholds"]}
    assert rows["s4_git_landed"]["n"] == 0
    assert rows["s4_git_landed"]["percentile"] is None
    assert rows["s2_active_days"]["n"] == 3


from ai_coding_insights.calibrate import _fmt, describe, format_report


def test_回放口径声明必须列全未测量项():
    """抬头那句「回放里未测量的是哪些」漏一项，用户就会把幽灵 0 当真值读。

    列表与 REPLAY_UNMEASURED 同步是纪律，这条断言把它变成闸门。
    """
    snaps = [replay_snapshot(date(2026, 7, 1), {"active_days": 12})]
    result = calibrate(snaps)
    result["replay"] = {"window_days": 30, "step_days": 30, "overlapping": False}
    text = format_report(result)
    head = text.split("== 指标分布 ==")[0]
    assert "未测量" in head
    for word in ("git 落地", "姿态四档", "自建技能数"):
        assert word in head, word


def test_大数用紧凑单位不撑破列宽():
    """token_total 这类 10 位数会把等宽表的列挤没（实测：1201456221 占满 10 列宽）。"""
    assert _fmt(1_201_456_221) == "1.2G"
    assert _fmt(12_345_678) == "12.3M"
    assert len(_fmt(999_999_999_999)) <= 8


def test_小数与整数显示不受影响():
    assert _fmt(49) == "49"
    assert _fmt(0) == "0"
    assert _fmt(17.754) == "17.754"
    assert _fmt(0.93) == "0.93"
    assert _fmt(999_999) == "999999"      # 未达 1e6 仍走原样整数
    assert _fmt(None) == "—"


def test_分布表列不粘连():
    """回归：大数导致相邻列之间没有空格，肉眼无法分辨字段边界。"""
    result = {"sample_count": 1, "span": {}, "distributions":
              {"token_total": describe([1_201_456_221])}, "thresholds": []}
    row = [ln for ln in format_report(result).splitlines() if "token_total" in ln][0]
    assert "  " in row.split("token_total")[1].strip() or row.count(" ") > 10
    assert "1201456221" not in row


from ai_coding_insights.calibrate import MIN_PERCENTILE_SAMPLES, replay_windows as _rw


def _one_sample_report():
    snaps = [replay_snapshot(date(2026, 7, 1), {"active_days": 23})]
    return format_report(calibrate(snaps))


def test_样本极少时不给绝对化的定性读法():
    """n=1 只能算出 0%/50%/100% 三个分位，「几乎所有观测都…」是 1 个观测撑不起的断言。

    行尾虽有「⚠ 不可靠」，但定性结论已经先入为主——这个项目宁可说测不准，
    不可用技术上正确的措辞造出确定感。
    """
    row = [ln for ln in _one_sample_report().splitlines()
           if "s2_active_days" in ln][0]
    assert "形同虚设" not in row and "几乎" not in row
    assert "样本" in row


def test_样本充足时定性读法照常给():
    snaps = [replay_snapshot(date(2026, 7, i + 1), {"active_days": 23})
             for i in range(MIN_PERCENTILE_SAMPLES + 2)]
    row = [ln for ln in format_report(calibrate(snaps)).splitlines()
           if "s2_active_days" in ln][0]
    assert "过门" in row or "形同虚设" in row


def test_样本极少时仍给出分位数字():
    """压制的是定性断言，不是数字本身——位置信息仍然有参考价值。"""
    row = [ln for ln in _one_sample_report().splitlines()
           if "s2_active_days" in ln][0]
    assert "分位" in row and "n=1" in row
