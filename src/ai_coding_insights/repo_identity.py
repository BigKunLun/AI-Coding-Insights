import re
import subprocess
from urllib.parse import urlparse

from .models import RemoteRule, RemoteIdentity

_SCP_RE = re.compile(r"^(?:[^@]+@)?([^:/]+):(.+)$")  # git@host:org/repo.git


def parse_remote_url(url: str) -> RemoteIdentity | None:
    url = (url or "").strip()
    if not url:
        return None
    if "://" in url:
        p = urlparse(url)
        host = p.hostname or ""
        seg = p.path.strip("/").split("/")
    else:
        m = _SCP_RE.match(url)
        if not m:
            return None
        host = m.group(1)
        seg = m.group(2).strip("/").split("/")
    if not host:
        return None
    host = host.lower()  # 规范化 host：消除 SSH/HTTPS 两分支大小写不一致（org 的大小写归一收在 matches 比较时做，identity 保留原样供诊断）
    org = seg[0] if seg and seg[0] else None
    return RemoteIdentity(host=host, org=org)


def matches(identity: RemoteIdentity, rules: list[RemoteRule]) -> bool:
    # org 大小写不敏感：GitHub/GitLab 的 owner/org 名平台层即不区分大小写，规则与远程
    # 存储大小写不一致不应导致公司会话被静默漏纳（host 已在 parse 时小写归一）。
    def _org_eq(a: str | None, b: str | None) -> bool:
        return (a or "").lower() == (b or "").lower()
    return any(
        identity.host == r.host and (r.org is None or _org_eq(identity.org, r.org))
        for r in rules
    )


def _git_remote_urls(cwd: str) -> list[str]:
    # 只读本地 .git/config，绝不联网（不用 fetch/ls-remote）；任何子进程异常都 fail-safe 返回 []
    try:
        out = subprocess.run(["git", "-C", cwd, "remote", "-v"],
                             capture_output=True, text=True, timeout=5)
    except (subprocess.SubprocessError, OSError):
        return []
    if out.returncode != 0:
        return []
    urls = [parts[1] for line in out.stdout.splitlines()
            if (parts := line.split()) and len(parts) >= 2]
    return list(dict.fromkeys(urls))


def remote_identities(cwd: str) -> list[RemoteIdentity]:
    """该目录全部 remote 的解析结果（不可解析的 URL 跳过；非 git 目录返回 []）。"""
    out = []
    for url in _git_remote_urls(cwd):
        ident = parse_remote_url(url)
        if ident:
            out.append(ident)
    return out


def classify(cwd: str, rules: list[RemoteRule]) -> bool:
    # 看全部 remote（迁移期可能 origin/旧仓双远程），任一命中即纳入
    return any(matches(i, rules) for i in remote_identities(cwd))
