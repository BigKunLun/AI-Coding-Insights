"""测试 customization.py 纯函数。"""
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ai_coding_insights.customization import (
    compute_customization_signals, count_project_md_sessions,
    detect_hook_config, scan_custom_skills,
)
from ai_coding_insights.sources import (
    CLAUDE_CODE, CODEX, OPENCODE, UnknownSourceError,
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


def test_detect_hook_config_empty_event_not_counted(tmp_path):
    # 声明但为空的 hook 段（CC 容许的遗留）不应被算作启用 hook 自动化
    cfg = {"hooks": {"SessionStart": [],
                     "SessionEnd": [{"hooks": [{"type": "command", "command": "x"}]}]}}
    p = tmp_path / "settings.json"
    p.write_text(json.dumps(cfg))
    result = detect_hook_config(str(p))
    assert result["hook_events"] == ["SessionEnd"]   # 空的 SessionStart 不计
    assert result["has_hooks"] is True


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
    # hook_config=None 是「未探测」（该 harness 无 hook 机制 / 调用方没传），
    # 不是「探测到没配」。原先这里断言 False，会让报告的能力盲区对 Codex 用户
    # 断言「你没用过 Hook 自动化」——那是错误结论，故语义改成 None + hooks_measured。
    assert result["has_hooks"] is None
    assert result["hooks_measured"] is False

# ---------------------------------------------------------------- 来源感知分派

class _S:
    """最小 ParsedSession 替身：count_project_md_sessions 只读 cwd。"""
    def __init__(self, cwd):
        self.cwd = cwd


def _mk_skill_dir(root: str, name: str, with_skill_md: bool = True) -> Path:
    d = Path(root) / name
    d.mkdir(parents=True)
    (d / "references").mkdir()          # Codex 技能目录常见附属物
    if with_skill_md:
        (d / "SKILL.md").write_text("# 占位技能", encoding="utf-8")
    return d


def test_scan_default_source_is_claude_code(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".claude" / "skills" / "deploy").mkdir(parents=True)
    (tmp_path / ".claude" / "skills" / "solo.md").write_text("x", encoding="utf-8")
    assert scan_custom_skills() == ["deploy", "solo"]


def test_scan_codex_requires_skill_md(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = tmp_path / ".codex" / "skills"
    _mk_skill_dir(str(root), "hyperframes")
    _mk_skill_dir(str(root), "figma")
    _mk_skill_dir(str(root), "just-a-folder", with_skill_md=False)
    assert scan_custom_skills(source=CODEX) == ["figma", "hyperframes"]


def test_scan_codex_missing_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert scan_custom_skills(source=CODEX) == []


def test_scan_opencode_技能落点(tmp_path, monkeypatch):  # noqa: N802
    """opencode 全局技能落在 ~/.config/opencode/skills/<name>/SKILL.md（本机实测 25 个）。

    此前这里断言「未探测→恒空」，那是无证据时的保守占位；实测到落点后必须改判，
    否则 CAP_CUSTOM_SKILL 已声明而计数恒 0，报告会说人家没沉淀过技能。
    """
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert scan_custom_skills(source=OPENCODE) == []      # 目录不存在 → 空
    (tmp_path / ".config" / "opencode" / "skills" / "demo").mkdir(parents=True)
    (tmp_path / ".config" / "opencode" / "skills" / "demo" / "SKILL.md").write_text(
        "x", encoding="utf-8")
    assert len(scan_custom_skills(source=OPENCODE)) == 1


def test_scan_explicit_dir_still_wins(tmp_path):
    # 既有调用（位置传目录）行为一字不改
    (tmp_path / "a").mkdir()
    assert scan_custom_skills(str(tmp_path)) == ["a"]


def test_scan_positional_source_name_also_works(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert scan_custom_skills(CODEX) == []


def test_scan_unknown_source_raises():
    # 拼错来源要立刻炸，不静默回退到 CC（回退＝拿别人的落点算这家的定制化信号）
    with pytest.raises(UnknownSourceError):
        scan_custom_skills(source="cluade-code")


def test_hooks_claude_code_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    d = tmp_path / ".claude"
    d.mkdir()
    (d / "settings.json").write_text(
        json.dumps({"hooks": {"SessionEnd": ["x"]}}), encoding="utf-8")
    assert detect_hook_config() == {"has_hooks": True, "hook_events": ["SessionEnd"]}


def test_hooks_codex_returns_none_not_false():
    # Codex 无 hook 机制：None = 未探测。返回 False 等于断言「你没配 hook」
    assert detect_hook_config(source=CODEX) is None
    assert detect_hook_config(source=OPENCODE) is None


def test_hooks_unmeasured_flows_into_signals():
    sig = compute_customization_signals(["a"], 2, detect_hook_config(source=CODEX))
    assert sig["has_hooks"] is None
    assert sig["hooks_measured"] is False
    assert sig["custom_skill_count"] == 1


def test_hooks_measured_flag_true_for_cc(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
    sig = compute_customization_signals([], 0, detect_hook_config(str(p)))
    assert sig["has_hooks"] is False        # 探测到了，确实没配
    assert sig["hooks_measured"] is True


def _touch(path: Path, when: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# 占位约定", encoding="utf-8")
    import os
    os.utime(path, (when.timestamp(), when.timestamp()))


def test_project_md_claude_code(tmp_path):
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=30)
    _touch(tmp_path / "p1" / "CLAUDE.md", now - timedelta(days=1))
    _touch(tmp_path / "p2" / "CLAUDE.md", now - timedelta(days=90))   # 窗口外
    sessions = [_S(str(tmp_path / "p1")), _S(str(tmp_path / "p1")),   # 同 cwd 只算一次
                _S(str(tmp_path / "p2")), _S(str(tmp_path / "p3"))]   # p3 无文件
    assert count_project_md_sessions(sessions, since) == 1


def test_project_md_codex_reads_agents_md(tmp_path):
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=30)
    _touch(tmp_path / "p1" / "AGENTS.md", now - timedelta(days=1))
    _touch(tmp_path / "p1" / "CLAUDE.md", now - timedelta(days=1))
    sessions = [_S(str(tmp_path / "p1"))]
    assert count_project_md_sessions(sessions, since, source=CODEX) == 1
    # 反向：只有 CLAUDE.md 的项目在 Codex 口径下不算
    _touch(tmp_path / "p2" / "CLAUDE.md", now - timedelta(days=1))
    assert count_project_md_sessions([_S(str(tmp_path / "p2"))], since, source=CODEX) == 0


def test_project_md_opencode_用_AGENTS_md(tmp_path):  # noqa: N802
    """opencode 的项目约定文件与 Codex 同名（AGENTS.md）。

    改判依据：本机 opencode v1.18.18 的服务端 bundle 里 25 处引用 AGENTS.md。
    此前这里断言「未探测→恒 0」，那是拿不到证据时的保守占位；有了实测证据就该改判——
    继续返回 0 会在 CAP_PROJECT_MD 已声明的前提下把 0 当真值渲染，说人家没写项目约定。
    """
    now = datetime.now(timezone.utc)
    _touch(tmp_path / "p1" / "AGENTS.md", now)
    assert count_project_md_sessions([_S(str(tmp_path / "p1"))],
                                     now - timedelta(days=30), source=OPENCODE) == 1
    # 反向：CLAUDE.md 在 opencode 口径下不算
    _touch(tmp_path / "p2" / "CLAUDE.md", now)
    assert count_project_md_sessions([_S(str(tmp_path / "p2"))],
                                     now - timedelta(days=30), source=OPENCODE) == 0


def test_project_md_empty_and_bad_cwd(tmp_path):
    since = datetime.now(timezone.utc) - timedelta(days=30)
    assert count_project_md_sessions([], since) == 0
    assert count_project_md_sessions([_S(""), _S("/nope/does/not/exist")], since) == 0


def test_project_md_unknown_source_raises():
    with pytest.raises(UnknownSourceError):
        count_project_md_sessions([], datetime.now(timezone.utc), source="codexx")
