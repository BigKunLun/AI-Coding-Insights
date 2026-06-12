import subprocess
from .models import ParsedSession, OutcomeStats


def verify_sha_in_history(cwd: str, sha: str) -> bool:
    """SHA 是否仍是当前 HEAD 的祖先（真落地且未被 reset 出历史）。fail-safe False。"""
    if not cwd or not sha:
        return False
    try:
        r = subprocess.run(["git", "-C", cwd, "merge-base", "--is-ancestor", sha, "HEAD"],
                           capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def compute_outcome(session: ParsedSession, _verify=verify_sha_in_history) -> OutcomeStats:
    shas = [c.sha for c in session.commits]
    landed = sum(1 for s in shas if _verify(session.cwd, s))
    return OutcomeStats(session_id=session.session_id, cwd=session.cwd,
                        commit_count=len(shas), landed_count=landed,
                        edit_count=session.edit_count)
