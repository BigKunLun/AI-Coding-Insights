from dataclasses import dataclass
from pathlib import Path

from .jsonl_parser import session_cwd
from .models import RemoteIdentity
from .repo_identity import remote_identities

# 公共代码托管域按 (host, org) 细分（同域既有团队又有私人）；
# 自建域整域归一组——自建 git 服务器通常整体属同一归属体，host 级规则即可覆盖。
PUBLIC_HOSTS = frozenset({"github.com", "gitlab.com", "bitbucket.org",
                          "gitee.com", "codeberg.org"})


@dataclass(frozen=True)
class SourceGroup:
    host: str | None   # None = 无 git remote（仅展示，不可选）
    org: str | None    # 仅公共托管域非 None
    project_count: int
    session_count: int

    @property
    def label(self) -> str:
        if self.host is None:
            return "（无 git remote）"
        return f"{self.host}/{self.org}" if self.org else self.host


def _group_key(ident: RemoteIdentity) -> tuple[str, str | None]:
    if ident.host in PUBLIC_HOSTS:
        return (ident.host, ident.org)
    return (ident.host, None)


def aggregate_sources(cwd_identities: dict[str, list[RemoteIdentity]],
                      cwd_session_counts: dict[str, int]) -> list[SourceGroup]:
    """按 (host, org) 聚合本机会话来源。一个 cwd 多个 remote 时计入每个对应组；
    无 remote 的 cwd 归 (None, None) 组。排序：会话数降序，无 remote 组恒末位。"""
    projects: dict[tuple, set[str]] = {}
    sessions: dict[tuple, int] = {}
    for cwd, idents in cwd_identities.items():
        keys = {_group_key(i) for i in idents} or {(None, None)}
        for k in keys:
            projects.setdefault(k, set()).add(cwd)
            sessions[k] = sessions.get(k, 0) + cwd_session_counts.get(cwd, 0)
    groups = [SourceGroup(host=k[0], org=k[1],
                          project_count=len(projects[k]), session_count=sessions[k])
              for k in projects]
    return sorted(groups, key=lambda g: (g.host is None, -g.session_count, g.label))


def _unselectable_reason(g: SourceGroup) -> str | None:
    if g.host is None:
        return "include 模式下无 remote 的项目天然不纳入"
    if g.host in PUBLIC_HOSTS and g.org is None:
        # host-only 规则在 matches() 里 org 为通配；对公共托管域意味着整域
        # （含私人项目）全部卷入——过度纳入比漏更糟，机制上禁止
        return "公共托管域缺 org，整域纳入会卷入私人项目"
    return None


def render_menu(groups: list[SourceGroup]) -> str:
    # 隐私约束：只展示 host/org 与计数，绝不展示项目名 / 路径 / 会话内容
    lines = []
    for i, g in enumerate(groups, start=1):
        reason = _unselectable_reason(g)
        note = f"（不可选：{reason}）" if reason else ""
        lines.append(f"  [{i}] {g.label}    {g.project_count} 项目 / {g.session_count} 会话{note}")
    return "\n".join(lines)


def parse_selection(text: str, groups: list[SourceGroup]) -> list[SourceGroup]:
    """空输入 = 个人形态（mode="all"）。序号越界 / 非数字 / 选中不可选组均报 ValueError。"""
    text = text.strip()
    if not text:
        return []
    picked: list[SourceGroup] = []
    for tok in text.split(","):
        tok = tok.strip()
        idx = int(tok) if tok.isdigit() else 0
        if not (1 <= idx <= len(groups)):
            raise ValueError(f"无效序号 {tok!r}（可选 1–{len(groups)}）")
        g = groups[idx - 1]
        reason = _unselectable_reason(g)
        if reason:
            raise ValueError(f"「{g.label}」不可选：{reason}")
        if g not in picked:
            picked.append(g)
    return picked


def _toml_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_config_toml(selected: list[SourceGroup]) -> str:
    if not selected:
        return 'mode = "all"\n'
    lines = ['mode = "include"']
    for g in selected:
        lines.append("")
        lines.append("[[include_remotes]]")
        lines.append(f"host = {_toml_str(g.host)}")
        if g.org:
            lines.append(f"org = {_toml_str(g.org)}")
    return "\n".join(lines) + "\n"


def collect_sources(projects_dir) -> tuple[dict[str, list[RemoteIdentity]], dict[str, int]]:
    """IO 壳：遍历全部会话（不限窗口——init 看历史全量），按 cwd 计会话数并取 remote。
    只需 cwd，用 session_cwd 头部短读，不全文件解析（全量历史可能数千个大文件）。"""
    counts: dict[str, int] = {}
    idents: dict[str, list[RemoteIdentity]] = {}
    for path in sorted(Path(projects_dir).glob("*/*.jsonl")):
        cwd = session_cwd(path)
        if not cwd:
            continue
        counts[cwd] = counts.get(cwd, 0) + 1
        if cwd not in idents:
            idents[cwd] = remote_identities(cwd)
    return idents, counts
