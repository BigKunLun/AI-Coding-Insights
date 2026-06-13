"""Customization 定制化信号检测（纯函数 + 受控 IO）。

检测用户是否做了工具定制（自建 skill、CLAUDE.md、hooks），这些是
"从默认用户到 power user"的关键信号。
"""
import json
from pathlib import Path


def scan_custom_skills(skill_dir: str | None = None) -> list[str]:
    """扫描技能目录，返回自建 skill 文件名列表（不含扩展名）。

    默认扫描 ~/.claude/skills/ 下的一级子目录和 .md 文件；
    仅返回文件名（不含路径），避免业务目录名泄露。
    """
    if skill_dir is None:
        skill_dir = str(Path.home() / ".claude" / "skills")
    p = Path(skill_dir)
    if not p.is_dir():
        return []
    names: list[str] = []
    for entry in sorted(p.iterdir()):
        if entry.is_dir():
            # skill 目录（如 superpowers/）
            names.append(entry.name)
        elif entry.suffix == ".md":
            # 单文件 skill
            names.append(entry.stem)
    return names


def detect_hook_config(config_path: str | None = None) -> dict:
    """解析 settings.json，返回 hook 配置信息。

    返回 {"has_hooks": bool, "hook_events": [str]}；
    hook_events 列出配置了 hook 的事件名（如 SessionStart, SessionEnd 等）。
    文件不存在 / 解析失败 / 无 hooks 均返回空信号，不抛异常。
    """
    if config_path is None:
        config_path = str(Path.home() / ".claude" / "settings.json")
    p = Path(config_path)
    if not p.is_file():
        return {"has_hooks": False, "hook_events": []}
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"has_hooks": False, "hook_events": []}
    if not isinstance(cfg, dict):
        return {"has_hooks": False, "hook_events": []}
    hooks = cfg.get("hooks")
    if not isinstance(hooks, dict):
        return {"has_hooks": False, "hook_events": []}
    # 仅统计真正挂了 hook 的事件：声明但为空的事件段（如 "SessionStart": []，CC 容许遗留）
    # 不应算作启用 hook 自动化，否则会误把该用户从「未用 hook」盲区里剔除、虚报为已用。
    events = sorted(k for k, v in hooks.items() if v)
    return {"has_hooks": len(events) > 0, "hook_events": events}


def compute_customization_signals(custom_skill_names: list[str],
                                   claude_md_sessions: int = 0,
                                   hook_config: dict | None = None) -> dict:
    """聚合定制化信号为标准化 dict。

    返回：
      {"has_custom_skills": bool, "custom_skill_count": int,
       "claude_md_sessions": int, "has_hooks": bool,
       "hook_events": [str]}
    """
    if hook_config is None:
        hook_config = {"has_hooks": False, "hook_events": []}
    return {
        "has_custom_skills": len(custom_skill_names) > 0,
        "custom_skill_count": len(custom_skill_names),
        "claude_md_sessions": claude_md_sessions,
        "has_hooks": hook_config.get("has_hooks", False),
        "hook_events": hook_config.get("hook_events", []),
    }
