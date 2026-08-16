"""Customization 定制化信号检测（纯函数 + 受控 IO）。

检测用户是否做了工具定制（自建 skill、项目约定文件、hooks），这些是
"从默认用户到 power user"的关键信号。

## 来源感知（多 harness 地基）

三家 harness 的定制化落点各不相同，**同一个函数按 `source` 分派**，而不是让调用方
各自拼路径（拼错就静默扫空目录 → 定制化信号全 0 → 报告说人家「没做定制」）：

| 信号        | claude-code                  | codex                     | opencode |
|-------------|------------------------------|---------------------------|----------|
| 自建技能    | `~/.claude/skills/` 下的子目录与 .md | `~/.codex/skills/*/SKILL.md` | 未探测   |
| 项目约定文件| 各 cwd 下 `CLAUDE.md`        | 各 cwd 下 `AGENTS.md`（另有全局 `~/.codex/AGENTS.md`，非会话级故不计入） | 未探测   |
| 生命周期 hook| `~/.claude/settings.json` 的 `hooks` | **无此机制** → 未探测      | 未探测   |

两条铁律：

1. **「无此机制」返回 None，不返回 False**。Codex 压根没有 hook 机制，返回
   `{"has_hooks": False}` 等于对用户说「你没配 hook」——那是错误结论，正是本项目
   定义的最危险故障（不报错、只安静产出错报告）。故 `detect_hook_config` 对
   codex/opencode 返回 `None`，`compute_customization_signals` 把它翻译成
   `has_hooks: None` + `hooks_measured: False`，由渲染层打「未测量」。

2. **不传来源时行为与改造前逐字一致**（默认 claude-code，第一位参数仍是路径），
   既有调用方与既有测试无需改动。
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from .sources import CLAUDE_CODE, CODEX, OPENCODE, SOURCE_NAMES, UnknownSourceError

# 自建技能目录（相对 home 的路径段；None = 该来源尚未探测出落点，不是「没有」）。
# 用 `Path.home()/…` 而非字符串 expanduser：与 sources.py 的 default_root_fn 同风格，
# 也让测试能像别处一样 monkeypatch Path.home 隔离掉真实本机目录。
_SKILL_ROOTS: dict[str, tuple[str, ...] | None] = {
    CLAUDE_CODE: (".claude", "skills"),
    CODEX: (".codex", "skills"),
    # 实测本机 v1.18.18：~/.config/opencode/skills/<name>/SKILL.md，25 个，
    # 与 CC / Codex 同构（服务端 bundle 里另有 "Global skills" 与项目级 ".opencode/skills"）。
    OPENCODE: (".config", "opencode", "skills"),
}

# 项目级约定文件名（放在会话 cwd 下）
_PROJECT_MD: dict[str, str | None] = {
    CLAUDE_CODE: "CLAUDE.md",
    CODEX: "AGENTS.md",
    # 实测：opencode 服务端 bundle 里 25 处引用 AGENTS.md，与 Codex 同名约定
    OPENCODE: "AGENTS.md",
}

# 生命周期 hook 的配置文件；None = 该 harness 无此机制或落点未探测
_HOOK_CONFIGS: dict[str, tuple[str, ...] | None] = {
    CLAUDE_CODE: (".claude", "settings.json"),
    CODEX: None,        # Codex 无生命周期 hook（config.toml 的 notify 语义不同）
    OPENCODE: None,     # TODO(opencode): 待实测
}

_NO_HOOKS = {"has_hooks": False, "hook_events": []}


def _check_source(source: str) -> str:
    """校验来源名。拼错要立刻炸，不静默回退——静默回退会让 Codex 用户拿到一份
    照 CC 落点扫出来的定制化信号（多半全 0），也就是一个看起来正常的错报告。"""
    if source not in SOURCE_NAMES:
        raise UnknownSourceError(
            f"未知会话来源 {source!r}，可选：{', '.join(SOURCE_NAMES)}")
    return source


def scan_custom_skills(skill_dir: str | None = None,
                       source: str = CLAUDE_CODE) -> list[str]:
    """扫描技能目录，返回自建 skill 名列表（不含扩展名/路径）。

    - claude-code（默认）：`~/.claude/skills/` 下的一级子目录与 .md 文件，**行为一字未改**。
    - codex：`~/.codex/skills/*/SKILL.md` —— 只认真正带 SKILL.md 的目录
      （Codex 的技能目录里还会有 references/ 等附属物，按目录名裸数会虚高）。
    - opencode：未探测，返回 []。

    `skill_dir` 显式路径优先（测试与调试用），此时按 claude-code 的扫描规则走。
    仅返回名字（不含路径），避免业务目录名泄露。
    """
    # 兼容位置传参写成来源名的调用（`scan_custom_skills("codex")`）
    if skill_dir in SOURCE_NAMES:
        skill_dir, source = None, skill_dir
    _check_source(source)
    explicit = skill_dir is not None
    if not explicit:
        parts = _SKILL_ROOTS.get(source)
        if parts is None:
            return []       # 未探测：空列表，不代表用户没建技能
        skill_dir = str(Path.home().joinpath(*parts))
    p = Path(skill_dir)
    if not p.is_dir():
        return []
    names: list[str] = []
    if source == CODEX and not explicit:
        for entry in sorted(p.iterdir()):
            if entry.is_dir() and (entry / "SKILL.md").is_file():
                names.append(entry.name)
        return names
    for entry in sorted(p.iterdir()):
        if entry.is_dir():
            # skill 目录（如 superpowers/）
            names.append(entry.name)
        elif entry.suffix == ".md":
            # 单文件 skill
            names.append(entry.stem)
    return names


def count_project_md_sessions(sessions, since_dt, source: str = CLAUDE_CODE) -> int:
    """窗口内被改动过的项目约定文件数（按会话 cwd 去重）。

    判据是文件 mtime 落在窗口内——「这个窗口里他维护过项目约定」比「文件存在」更能
    区分真在用和当初建了就不管。cwd 去重是因为同一项目多个会话只该算一次。
    opencode 未探测约定文件名，恒返回 0（配 `unmeasured` 语义，不是「没写过」）。

    `sessions` 是 ParsedSession 列表，`since_dt` 是 aware datetime。
    """
    _check_source(source)
    name = _PROJECT_MD.get(source)
    if not name:
        return 0
    count = 0
    seen_cwd = set()
    for s in sessions:
        cwd = getattr(s, "cwd", "")
        if not cwd or cwd in seen_cwd:
            continue
        seen_cwd.add(cwd)
        f = Path(cwd) / name
        if not f.is_file():
            continue
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            continue
        if mtime >= since_dt:
            count += 1
    return count


def detect_hook_config(config_path: str | None = None,
                       source: str = CLAUDE_CODE) -> dict | None:
    """解析 settings.json，返回 hook 配置信息；**该 harness 无此机制时返回 None**。

    返回 {"has_hooks": bool, "hook_events": [str]}；hook_events 列出配置了 hook 的
    事件名（如 SessionStart, SessionEnd）。文件不存在 / 解析失败 / 无 hooks 均返回
    空信号，不抛异常。

    codex / opencode 返回 `None` = 「未探测」。**别退化成 False**：False 会被报告的
    能力盲区读成「你没用过 Hook 自动化」，那是对 Codex 用户的错误指控。
    """
    if config_path in SOURCE_NAMES:
        config_path, source = None, config_path
    _check_source(source)
    if config_path is None:
        parts = _HOOK_CONFIGS.get(source)
        if parts is None:
            return None
        config_path = str(Path.home().joinpath(*parts))
    p = Path(config_path)
    if not p.is_file():
        return dict(_NO_HOOKS)
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return dict(_NO_HOOKS)
    if not isinstance(cfg, dict):
        return dict(_NO_HOOKS)
    hooks = cfg.get("hooks")
    if not isinstance(hooks, dict):
        return dict(_NO_HOOKS)
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
       "claude_md_sessions": int, "has_hooks": bool | None,
       "hook_events": [str], "hooks_measured": bool}

    `hook_config=None` 有两种来源，语义不同但这里一律按「未探测」处理：
    该 harness 无 hook 机制（`detect_hook_config` 返回 None），或调用方没传。
    此时 `has_hooks` 是 **None 而不是 False**，并配 `hooks_measured: False`
    供渲染层打「未测量」——写 False 等于断言「你没配 hook」。
    """
    measured = isinstance(hook_config, dict)
    return {
        "has_custom_skills": len(custom_skill_names) > 0,
        "custom_skill_count": len(custom_skill_names),
        "claude_md_sessions": claude_md_sessions,
        "has_hooks": hook_config.get("has_hooks", False) if measured else None,
        "hook_events": hook_config.get("hook_events", []) if measured else [],
        "hooks_measured": measured,
    }
