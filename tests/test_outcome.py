import subprocess
from ai_coding_insights.models import ParsedSession, CommitRef
from ai_coding_insights.outcome import verify_sha_in_history, compute_outcome

def _real_repo(tmp_path):
    d = tmp_path/"repo"; d.mkdir()
    run = lambda *a: subprocess.run(["git","-C",str(d),*a], check=True, capture_output=True)
    run("init","-q"); run("config","user.email","t@t"); run("config","user.name","t")
    (d/"f.txt").write_text("v1")
    run("add","."); run("commit","-q","-m","c1")
    sha = subprocess.run(["git","-C",str(d),"rev-parse","HEAD"],capture_output=True,text=True).stdout.strip()
    return str(d), sha

def test_verify_real_sha_in_history(tmp_path):
    cwd, sha = _real_repo(tmp_path)
    assert verify_sha_in_history(cwd, sha) is True
    assert verify_sha_in_history(cwd, "deadbeef") is False     # 不存在
    assert verify_sha_in_history("/no/such/dir", sha) is False  # fail-safe

def test_compute_outcome(tmp_path):
    cwd, sha = _real_repo(tmp_path)
    s = ParsedSession("f","sess",cwd,None,[],[],[],None,None,
                      commits=[CommitRef(sha,"committed"), CommitRef("deadbeef","committed")],
                      edit_count=7)
    o = compute_outcome(s)
    assert o.commit_count == 2 and o.landed_count == 1 and o.edit_count == 7
    assert o.landed_ratio == 0.5
