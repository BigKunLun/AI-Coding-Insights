"""测试 customization.py 纯函数。"""
import json
import tempfile
from pathlib import Path
from ai_coding_insights.customization import (
    scan_custom_skills, detect_hook_config, compute_customization_signals
)


def test_scan_empty_dir(tmp_path):
    assert scan_custom_skills(str(tmp_path)) == []


def test_scan_nonexistent_dir(tmp_path):
    assert scan_custom_skills(str(tmp_path / "nonexistent")) == []


def test_scan_with_md_files(tmp_path):
    (tmp_path / "my-skill.md").write_text("# My Skill")
    (tmp_path / "deploy.md").write_text("# Deploy")
    names = scan_custom_skills(str(tmp_path))
    assert sorted(names) == sorted(["my-skill", "deploy"])


def test_scan_with_dirs(tmp_path):
    (tmp_path / "superpowers").mkdir()
    (tmp_path / "my-tools").mkdir()
    (tmp_path / "readme.md").write_text("# readme")  # .md 文件也计入
    names = scan_custom_skills(str(tmp_path))
    assert sorted(names) == sorted(["my-tools", "readme", "superpowers"])


def test_detect_hook_config_present(tmp_path):
    cfg = {"hooks": {"SessionEnd": ["echo done"], "SessionStart": ["echo start"]}}
    p = tmp_path / "settings.json"
    p.write_text(json.dumps(cfg))
    result = detect_hook_config(str(p))
    assert result["has_hooks"] is True
    assert sorted(result["hook_events"]) == sorted(["SessionEnd", "SessionStart"])


def test_detect_hook_config_absent(tmp_path):
    cfg = {"theme": "dark"}
    p = tmp_path / "settings.json"
    p.write_text(json.dumps(cfg))
    result = detect_hook_config(str(p))
    assert result["has_hooks"] is False
    assert result["hook_events"] == []


def test_detect_hook_config_no_file(tmp_path):
    result = detect_hook_config(str(tmp_path / "nonexistent.json"))
    assert result["has_hooks"] is False
    assert result["hook_events"] == []


def test_detect_hook_config_invalid_json(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text("not json")
    result = detect_hook_config(str(p))
    assert result["has_hooks"] is False


def test_compute_customization_signals_all_present():
    result = compute_customization_signals(
        custom_skill_names=["deploy", "lint"],
        claude_md_sessions=3,
        hook_config={"has_hooks": True, "hook_events": ["SessionEnd"]},
    )
    assert result["has_custom_skills"] is True
    assert result["custom_skill_count"] == 2
    assert result["claude_md_sessions"] == 3
    assert result["has_hooks"] is True
    assert result["hook_events"] == ["SessionEnd"]


def test_compute_customization_signals_all_absent():
    result = compute_customization_signals([], 0, None)
    assert result["has_custom_skills"] is False
    assert result["custom_skill_count"] == 0
    assert result["claude_md_sessions"] == 0
    assert result["has_hooks"] is False
