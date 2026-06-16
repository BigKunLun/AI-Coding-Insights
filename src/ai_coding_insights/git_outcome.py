"""git 主锚成果采集：窗口内本人提交 × 会话编辑文件重叠归属。

口径（文件重叠，时间无关，只出整数计数）：
- 提交来源 = `git log`（HEAD 祖先——与 outcome.verify_sha_in_history 的 landed
  语义一致），author 限本机 `git config user.email`，`--no-merges`；取不到 email、
  非 git 仓库、子进程异常一律 fail-safe 零采集（宁漏勿误）。
- 归属 = 提交改动文件集与会话编辑集（ParsedSession.edited_paths）求交，有交集即落地；
  total = 窗口内本人提交总数（落地率分母，同口径）。与时间窗无关。
- 隐私（承重）：文件名只在本机内用于求交，**绝不进入任何返回结构**——本模块返回与
  emit 的只有整数 landed/total 计数；只读 `--name-only`（文件名），永不读 diff 内容。
"""
import os
import subprocess
from datetime import datetime

from .models import RepoOutcome

_GIT_TIMEOUT = 10   # log 扫历史比单 sha 验证慢，给比 outcome.py 略宽的超时


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


def normalize_to_repo_rel(abs_paths: set, root: str) -> set:
    """会话编辑绝对路径 → 仓库相对路径（仅保留 root 下的）。路径只在本机内用。"""
    rel = set()
    base = root.rstrip(os.sep) + os.sep
    for p in abs_paths:
        if isinstance(p, str) and p.startswith(base):
            rel.add(os.path.relpath(p, root))
    return rel


def attribute_by_overlap(commit_file_sets: list, edited_rel: set) -> RepoOutcome:
    """纯函数：提交改动文件集与会话编辑集求交。total=提交数；landed=有交集的提交数。"""
    total = len(commit_file_sets)
    if not edited_rel:
        return RepoOutcome(landed_count=0, total_count=total)
    landed = sum(1 for fs in commit_file_sets if fs & edited_rel)
    return RepoOutcome(landed_count=landed, total_count=total)


def window_commit_file_sets(cwd: str, since: datetime) -> list:
    """窗口内本人提交的改动文件集列表（HEAD 祖先，--no-merges）。
    只读 --name-only，不读 diff。fail-safe []（宁漏勿误）。"""
    email = (_run_git(cwd, "config", "user.email") or "").strip()
    if not email:
        return []
    out = _run_git(cwd, "log", f"--since={since.isoformat()}",
                   f"--author={email}", "--no-merges", "--name-only",
                   "--pretty=format:%x01")
    if out is None:
        return []
    sets, cur = [], None
    for line in out.splitlines():
        if line.startswith("\x01"):
            if cur is not None:
                sets.append(cur)
            cur = set()
        elif cur is not None and line.strip():
            cur.add(line.strip())
    if cur is not None:
        sets.append(cur)
    return sets


def repo_outcome(root: str, edited_abs: set, since: datetime) -> RepoOutcome:
    return attribute_by_overlap(window_commit_file_sets(root, since),
                                normalize_to_repo_rel(edited_abs, root))
