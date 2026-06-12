"""verify-obs 覆盖校验：obs 覆盖会话 == batch 会话（编排闸二）。

纯函数 check_obs_coverage 做决策（缺口按 batch 文件归类、orphan 检出残留旧 obs），
CLI verify-obs 只做读文件与汇报。对应 2026-06-10 活体运行暴露的两个失败模式：
extractor 漏抽低信息会话、/tmp 残留上一轮 obs 被专家静默误读。
"""

import json
from pathlib import Path

from ai_coding_insights.obs_check import (check_obs_coverage, check_posture_counts,
                                          sum_posture_counts)
from ai_coding_insights.cli import main


# ---------- 纯函数 ----------

def test_full_coverage_ok():
    res = check_obs_coverage({"b1": ["s1", "s2"], "b2": ["s3"]}, {"s1", "s2", "s3"})
    assert res["status"] == "ok"
    assert res["missing"] == []
    assert res["orphans"] == []
    assert res["batch_session_count"] == 3
    assert res["obs_session_count"] == 3


def test_missing_grouped_by_batch_file():
    res = check_obs_coverage({"b1": ["s1", "s2"], "b2": ["s3", "s4"]}, {"s1", "s3"})
    assert res["status"] == "mismatch"
    # 缺口按 batch 文件归类，便于只补派受影响的批
    assert res["missing"] == [
        {"file": "b1", "session_ids": ["s2"]},
        {"file": "b2", "session_ids": ["s4"]},
    ]
    assert res["orphans"] == []


def test_orphan_obs_ids_flagged_as_stale():
    # obs 里出现不属于任何 batch 的会话 → 残留旧 obs 的信号，必须 mismatch
    res = check_obs_coverage({"b1": ["s1"]}, {"s1", "old-x"})
    assert res["status"] == "mismatch"
    assert res["missing"] == []
    assert res["orphans"] == ["old-x"]


def test_empty_batches_and_obs_ok():
    res = check_obs_coverage({}, set())
    assert res["status"] == "ok"
    assert res["batch_session_count"] == 0


def test_posture_counts_valid_passes():
    obs = [{"session_id": "s1", "posture_counts": {"L1": 2, "L2": 0, "L3": 1, "L4": 0}}]
    assert check_posture_counts({"s1": 3}, obs) == []


def test_posture_counts_missing_key_flagged():
    obs = [{"session_id": "s1", "notable_turns": []}]   # 缺 posture_counts
    problems = check_posture_counts({"s1": 0}, obs)
    assert problems == [{"session_id": "s1", "reason": "缺 posture_counts"}]


def test_posture_counts_sum_mismatch_flagged():
    obs = [{"session_id": "s1", "posture_counts": {"L1": 1, "L2": 0, "L3": 0, "L4": 0}}]
    problems = check_posture_counts({"s1": 3}, obs)
    assert len(problems) == 1 and "总和 1" in problems[0]["reason"]


def test_posture_counts_bad_values_flagged():
    obs = [{"session_id": "s1", "posture_counts": {"L1": -1, "L2": True, "L3": 0.5, "L4": 0}}]
    problems = check_posture_counts({"s1": 0}, obs)
    assert len(problems) == 1 and "非负整数" in problems[0]["reason"]


def test_posture_counts_orphan_session_skipped():
    # 不属于任何 batch 的 obs 会话由覆盖校验报 orphan，这里不重复报
    obs = [{"session_id": "ghost", "posture_counts": {"L1": 1, "L2": 0, "L3": 0, "L4": 0}}]
    assert check_posture_counts({}, obs) == []


def test_sum_posture_counts_aggregates():
    obs = [{"session_id": "s1", "posture_counts": {"L1": 2, "L2": 1, "L3": 3, "L4": 0}},
           {"session_id": "s2", "posture_counts": {"L1": 1, "L2": 0, "L3": 0, "L4": 2}}]
    assert sum_posture_counts(obs) == {"L1": 3, "L2": 1, "L3": 3, "L4": 2}


def test_sum_posture_counts_defensive():
    obs = [{"session_id": "s1"},                                   # 缺键
           {"session_id": "s2", "posture_counts": {"L1": -5, "L2": True, "L4": 1}}]
    assert sum_posture_counts(obs) == {"L1": 0, "L2": 0, "L3": 0, "L4": 1}
    assert sum_posture_counts([]) == {"L1": 0, "L2": 0, "L3": 0, "L4": 0}
    assert sum_posture_counts(None) == {"L1": 0, "L2": 0, "L3": 0, "L4": 0}


# ---------- CLI ----------

def _write_batch(d: Path, nn: str, sids: list[str], turns_by_sid: dict | None = None):
    (d / f"batch-{nn}.json").write_text(
        json.dumps([{"session_id": s, "turns": (turns_by_sid or {}).get(s, [])}
                    for s in sids], ensure_ascii=False),
        encoding="utf-8")


def _write_obs(d: Path, nn: str, sids: list[str], counts: dict | None = None):
    (d / f"aci-obs-{nn}.json").write_text(
        json.dumps({"sessions": [
            {"session_id": s, "notable_turns": [],
             "posture_counts": (counts or {}).get(
                 s, {"L1": 0, "L2": 0, "L3": 0, "L4": 0})}
            for s in sids]}, ensure_ascii=False),
        encoding="utf-8")


def test_cli_ok(tmp_path, capsys):
    batches = tmp_path / "batches"; batches.mkdir()
    _write_batch(batches, "01", ["s1", "s2"])
    _write_obs(tmp_path, "01", ["s1", "s2"])
    rc = main(["verify-obs", "--batches", str(batches),
               "--obs-glob", str(tmp_path / "aci-obs-*.json")])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "ok"


def test_cli_missing_reports_batch_file(tmp_path, capsys):
    batches = tmp_path / "batches"; batches.mkdir()
    _write_batch(batches, "01", ["s1", "s2"])
    _write_batch(batches, "02", ["s3"])
    _write_obs(tmp_path, "01", ["s1"])   # 漏 s2；02 整批没写
    rc = main(["verify-obs", "--batches", str(batches),
               "--obs-glob", str(tmp_path / "aci-obs-*.json")])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "mismatch"
    missing_files = [m["file"] for m in out["missing"]]
    assert str(batches / "batch-01.json") in missing_files
    assert str(batches / "batch-02.json") in missing_files


def test_cli_orphan_from_stale_obs(tmp_path, capsys):
    batches = tmp_path / "batches"; batches.mkdir()
    _write_batch(batches, "01", ["s1"])
    _write_obs(tmp_path, "01", ["s1"])
    _write_obs(tmp_path, "99", ["old-x"])  # 上一轮残留
    rc = main(["verify-obs", "--batches", str(batches),
               "--obs-glob", str(tmp_path / "aci-obs-*.json")])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["orphans"] == ["old-x"]


def test_cli_unreadable_obs_is_mismatch(tmp_path, capsys):
    batches = tmp_path / "batches"; batches.mkdir()
    _write_batch(batches, "01", ["s1"])
    (tmp_path / "aci-obs-01.json").write_text("{broken", encoding="utf-8")
    rc = main(["verify-obs", "--batches", str(batches),
               "--obs-glob", str(tmp_path / "aci-obs-*.json")])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "mismatch"
    assert out["unreadable"] == [str(tmp_path / "aci-obs-01.json")]


def test_cli_posture_sum_mismatch_is_mismatch(tmp_path, capsys):
    batches = tmp_path / "batches"; batches.mkdir()
    _write_batch(batches, "01", ["s1"], {"s1": [{"uuid": "u1", "text": "a"},
                                                {"uuid": "u2", "text": "b"}]})
    _write_obs(tmp_path, "01", ["s1"], {"s1": {"L1": 1, "L2": 0, "L3": 0, "L4": 0}})
    rc = main(["verify-obs", "--batches", str(batches),
               "--obs-glob", str(tmp_path / "aci-obs-*.json")])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "mismatch"
    assert out["posture_invalid"][0]["session_id"] == "s1"
    assert out["posture_invalid"][0]["file"] == str(batches / "batch-01.json")


def test_cli_missing_posture_counts_is_mismatch(tmp_path, capsys):
    batches = tmp_path / "batches"; batches.mkdir()
    _write_batch(batches, "01", ["s1"])
    (tmp_path / "aci-obs-01.json").write_text(
        json.dumps({"sessions": [{"session_id": "s1", "notable_turns": []}]}),
        encoding="utf-8")
    rc = main(["verify-obs", "--batches", str(batches),
               "--obs-glob", str(tmp_path / "aci-obs-*.json")])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert any("posture_counts" in p["reason"] for p in out["posture_invalid"])
