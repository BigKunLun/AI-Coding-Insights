"""git 主锚成果采集：窗口内本人提交 × 会话时间窗细口径归属。

口径（2026-06-12 拍板，细口径）：
- 提交来源 = `git log`（HEAD 祖先——与 outcome.verify_sha_in_history 的 landed
  语义一致），author 限本机 `git config user.email`；取不到 email、非 git 仓库、
  子进程异常一律 fail-safe 零采集（宁漏勿误）。
- 归属 = 提交 author 时间落入该仓库任一纳入会话的 [first_ts-宽限, last_ts+宽限]；
  窗外提交单独计数（outside），仅参考，不进任何比率。
- 隐私：只取 --pretty=%aI（时间戳），提交信息/文件名永不读取。
- transcript 的 gitOperation 口径（outcome.py）保留为增强信号：它能看到「提交过
  但已被丢弃」的失败成本，git log 只含存活提交、天然测不到丢弃。
"""
import subprocess
from datetime import datetime, timedelta

from .models import RepoOutcome

_GRACE = timedelta(minutes=30)
_GIT_TIMEOUT = 10   # log 扫历史比单 sha 验证慢，给比 outcome.py 略宽的超时


def attribute_commits(commit_times: list, spans: list,
                      grace: timedelta = _GRACE) -> RepoOutcome:
    """纯函数：提交时间归属到会话时间窗。spans 元素为 (first, last) aware datetime。"""
    landed = sum(1 for t in commit_times
                 if any(a - grace <= t <= b + grace for a, b in spans))
    return RepoOutcome(landed_count=landed,
                       outside_count=len(commit_times) - landed)


def _run_git(cwd: str, *args) -> str | None:
    """成功返回 stdout，任何失败（非 git 目录/超时/异常）返回 None。"""
    try:
        r = subprocess.run(["git", "-C", cwd, *args],
                           capture_output=True, text=True, timeout=_GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def repo_root(cwd: str) -> str | None:
    """仓库顶层目录；同仓多 cwd（子目录会话）据此归并，防同一提交双计。"""
    out = _run_git(cwd, "rev-parse", "--show-toplevel")
    root = (out or "").strip()
    return root or None


def window_commit_times(cwd: str, since: datetime) -> list:
    """窗口内本人提交的 author 时间（HEAD 祖先）。fail-safe []（宁漏勿误）。"""
    email = (_run_git(cwd, "config", "user.email") or "").strip()
    if not email:
        return []
    out = _run_git(cwd, "log", f"--since={since.isoformat()}",
                   f"--author={email}", "--pretty=%aI")
    if not out:
        return []
    times = []
    for line in out.splitlines():
        try:
            times.append(datetime.fromisoformat(line.strip()))
        except ValueError:
            continue
    return times


def repo_outcome(cwd: str, spans: list, since: datetime) -> RepoOutcome:
    return attribute_commits(window_commit_times(cwd, since), spans)
