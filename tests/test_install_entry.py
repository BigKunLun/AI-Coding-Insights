"""命令前缀（entry）的推断与透传。

**这一层守的是什么**：`install` 把 playbook 落到用户机器上，playbook 正文里每条规则层
命令都以 `<ACI>` 开头。安装器把 `<ACI>` 替换成什么，就决定了用户跑评估时那条命令能不能
解析出来。原先它被写死成 `uvx ai-coding-insights`——**只有包发到 PyPI 才解析得出来**。

后果是本项目定义的最危险那一类故障的近亲：从 git 装（`uvx --from git+... install`）
装得上、`--print` 一切正常、落位文件也对，但 playbook 第一条 `uvx ai-coding-insights scan`
在 PyPI 上找不到包。用户看到的不是「安装失败」，而是「装好了，一跑就报找不到包」。

修法是让前缀**跟安装来源走**：读本进程所属发行版的 PEP 610 `direct_url.json`
（uv / pip 从 URL 或 VCS 装包时会写这个文件），把「我是从哪来的」翻译成「该怎么再调起我」。
`--entry` 可显式覆盖，推断不出就退回 PyPI 口径。

配套的承重细节：**allowed-tools 必须跟着前缀一起改**。前缀换成 `uv run …` 而白名单
还写着 `Bash(uvx *)`，表现是每条命令都弹权限确认、编排卡在半路——同样不报错。
"""
import json

import pytest

from ai_coding_insights import sources
from ai_coding_insights.installers import (
    ADAPTERS,
    PLUGIN_ADAPTER,
    _ACI_ENTRY,
    bash_glob_for,
    detect_entry,
    entry_from_direct_url,
    plan_install,
    render_playbook,
    with_entry,
)

SAMPLE = "---\nname: x\n---\n\n跑一下：`<ACI> scan --source <SOURCE> <PLUGIN_ROOT_OPT>`\n"

_GIT_URL = "https://github.com/BigKunLun/AI-Coding-Insights"


def _du(**kw) -> str:
    return json.dumps(kw)


# ------------------------------------------------- entry_from_direct_url（纯函数）

def test_git安装推断出带from的uvx前缀():
    """从 git 装 → 再调起时也必须带 `--from git+…`，否则解析回 PyPI。"""
    raw = _du(url=_GIT_URL, vcs_info={"vcs": "git", "commit_id": "092cc48"})
    assert entry_from_direct_url(raw) == f"uvx --from git+{_GIT_URL} ai-coding-insights"


def test_git安装保留用户显式指定的修订():
    """用户装的是 `@v1.0`，落位的 playbook 就该继续钉在 `@v1.0`。

    只在 `requested_revision` 存在时钉——它记的是**用户当初怎么写的**。不拿
    `commit_id` 顶替：那会把用户明明想跟默认分支的意图，偷偷冻结在安装当天那个提交上。
    """
    raw = _du(url=_GIT_URL,
              vcs_info={"vcs": "git", "commit_id": "092cc48", "requested_revision": "v1.0"})
    assert entry_from_direct_url(raw) == f"uvx --from git+{_GIT_URL}@v1.0 ai-coding-insights"


def test_未指定修订时不钉commit():
    raw = _du(url=_GIT_URL, vcs_info={"vcs": "git", "commit_id": "092cc48"})
    assert "092cc48" not in entry_from_direct_url(raw)


def test_本地目录安装推断出uv_run_project():
    """从仓库目录装（含 editable）→ 前缀指回那个目录，不经任何索引。

    这就是 BOSS 问的「直接通过项目走」：clone 下来 `uv run … install`，
    落位的 playbook 从此指着这份 clone。
    """
    raw = _du(url="file:///Users/x/AI-Coding-Insights", dir_info={"editable": True})
    assert entry_from_direct_url(raw) == (
        "uv run --project /Users/x/AI-Coding-Insights python -m ai_coding_insights")


def test_本地目录非editable同样成立():
    raw = _du(url="file:///opt/aci", dir_info={})
    assert entry_from_direct_url(raw) == "uv run --project /opt/aci python -m ai_coding_insights"


def test_含空格的路径被引号包住():
    """不引起来，shell 会把 `--project` 的取值切断，`uv run` 转头去跑当前目录的项目。"""
    raw = _du(url="file:///Users/x/My%20Repos/aci", dir_info={})
    entry = entry_from_direct_url(raw)
    assert "'/Users/x/My Repos/aci'" in entry


@pytest.mark.parametrize("raw", [
    None,
    "",
    "not json at all",
    "[]",                                            # 顶层不是对象
    _du(vcs_info={"vcs": "git"}),                    # 缺 url
    _du(url=_GIT_URL),                               # 三种 info 一个都没有
    _du(url="https://x/a.whl", archive_info={}),     # 直接装 wheel：没有可复现的再调起写法
    _du(url="hg+https://x/y", vcs_info={"vcs": "hg"}),   # 非 git VCS
])
def test_推断不出就返回None(raw):
    """推断不出**必须**返回 None 让调用方退回 PyPI 口径，不许瞎猜出一条跑不通的命令。"""
    assert entry_from_direct_url(raw) is None


def test_真实的uvx_git元数据能被解析():
    """回归锚：这串是 2026-08-16 从 `uvx --from git+… ` 环境里原样读出来的。

    字段名或嵌套结构若被上游改掉，这条会先炸——而不是等到用户装完跑不通。
    """
    raw = ('{"url":"https://github.com/BigKunLun/AI-Coding-Insights",'
           '"vcs_info":{"vcs":"git","commit_id":"092cc487b50d50321e23d46ce3f4907b08754ca6"}}')
    assert entry_from_direct_url(raw) == f"uvx --from git+{_GIT_URL} ai-coding-insights"


def test_真实的editable元数据能被解析():
    raw = ('{"url":"file:///Users/shijianing/CodingTime/AI-Coding-Insights",'
           '"dir_info":{"editable":true}}')
    assert entry_from_direct_url(raw) == (
        "uv run --project /Users/shijianing/CodingTime/AI-Coding-Insights "
        "python -m ai_coding_insights")


# ------------------------------------------------------------- detect_entry（薄 IO）

def test_detect_entry推断不出时退回PyPI口径(monkeypatch):
    monkeypatch.setattr("ai_coding_insights.installers.read_direct_url", lambda: None)
    assert detect_entry() == _ACI_ENTRY


def test_detect_entry跟着安装来源走(monkeypatch):
    raw = _du(url=_GIT_URL, vcs_info={"vcs": "git", "commit_id": "abc"})
    monkeypatch.setattr("ai_coding_insights.installers.read_direct_url", lambda: raw)
    assert detect_entry() == f"uvx --from git+{_GIT_URL} ai-coding-insights"


def test_detect_entry在本机真跑不抛异常():
    """无论本机是怎么装的，这个函数都必须给出一条字符串，绝不因元数据缺失而中断安装。"""
    entry = detect_entry()
    assert isinstance(entry, str) and entry.strip()


# ------------------------------------------------------------ bash_glob_for（纯函数）

@pytest.mark.parametrize("entry,glob", [
    ("uvx ai-coding-insights", "Bash(uvx *)"),
    (f"uvx --from git+{_GIT_URL} ai-coding-insights", "Bash(uvx *)"),
    ("uv run --project /x python -m ai_coding_insights", "Bash(uv run *)"),
    ("uv run --project ${CLAUDE_PLUGIN_ROOT} python -m ai_coding_insights", "Bash(uv run *)"),
    ("ai-coding-insights", "Bash(ai-coding-insights *)"),
])
def test_白名单条目由前缀推出(entry, glob):
    assert bash_glob_for(entry) == glob


def test_uv与uv_run不可混淆():
    """`uv` 单独一个词（不是 `uv run`）不该被当成 `uv run` 放行。"""
    assert bash_glob_for("uv tool run aci") == "Bash(uv *)"


@pytest.mark.parametrize("adapter", list(ADAPTERS.values()) + [PLUGIN_ADAPTER])
def test_出厂适配器的白名单与其前缀自洽(adapter):
    """**这条是本文件的核心不变量**：allowed-tools 与 command_prefix 失配 = 全程弹权限确认。

    出厂三家 + 插件形态都要自洽；日后谁改了一边忘了另一边，这条先炸。
    """
    allowed = adapter.frontmatter.get("allowed-tools")
    if not allowed:
        pytest.skip(f"{adapter.label} 的 frontmatter 规范里没有 allowed-tools")
    assert bash_glob_for(adapter.command_prefix) in allowed


# --------------------------------------------------------------- with_entry（纯函数）

def test_with_entry换掉前缀():
    a = with_entry(ADAPTERS[sources.CLAUDE_CODE], "uv run --project /x python -m ai_coding_insights")
    assert a.command_prefix == "uv run --project /x python -m ai_coding_insights"


def test_with_entry同步白名单():
    a = with_entry(ADAPTERS[sources.CLAUDE_CODE], "uv run --project /x python -m ai_coding_insights")
    allowed = a.frontmatter["allowed-tools"]
    assert "Bash(uv run *)" in allowed
    assert "Bash(uvx *)" not in allowed, "旧条目必须换掉而不是并存——并存等于白名单越放越宽"
    # 与本工具无关的条目原样保留
    assert "Bash(date *)" in allowed and "Agent" in allowed


def test_with_entry不动没有白名单的家():
    """Codex / opencode 的 frontmatter 规范里没有 allowed-tools，不许凭空加一个。"""
    for name in (sources.CODEX, sources.OPENCODE):
        a = with_entry(ADAPTERS[name], "uv run --project /x python -m ai_coding_insights")
        assert "allowed-tools" not in a.frontmatter


def test_with_entry不改坏共享的ADAPTERS():
    """`ADAPTERS` 是模块级共享 dict，`with_entry` 必须返回新对象、不得原地改。"""
    before = dict(ADAPTERS[sources.CLAUDE_CODE].frontmatter)
    a = with_entry(ADAPTERS[sources.CLAUDE_CODE], "uv run --project /x python -m ai_coding_insights")
    assert ADAPTERS[sources.CLAUDE_CODE].frontmatter == before
    assert a.frontmatter is not ADAPTERS[sources.CLAUDE_CODE].frontmatter


def test_with_entry给空值就原样返回():
    base = ADAPTERS[sources.CLAUDE_CODE]
    assert with_entry(base, None) is base
    assert with_entry(base, "") is base
    assert with_entry(base, "   ") is base


def test_with_entry其余字段全部照搬():
    base = ADAPTERS[sources.CLAUDE_CODE]
    a = with_entry(base, "uvx --from git+X ai-coding-insights")
    for f in ("source", "label", "target", "has_subagent", "doc_url", "plugin_root_opt"):
        assert getattr(a, f) == getattr(base, f)


# ------------------------------------------------------------------- 渲染端到端

def test_渲染出的每条命令都用新前缀():
    entry = f"uvx --from git+{_GIT_URL} ai-coding-insights"
    text = render_playbook(SAMPLE, with_entry(ADAPTERS[sources.CLAUDE_CODE], entry))
    assert entry in text
    assert "<ACI>" not in text


def test_真实playbook里不残留裸PyPI调用():
    """**这条直接钉死本次修的 bug**：从 git 装出来的 playbook 里，
    不许再出现任何一条会去 PyPI 解析的 `uvx ai-coding-insights <子命令>`。
    """
    from ai_coding_insights.playbook import load_playbook
    entry = f"uvx --from git+{_GIT_URL} ai-coding-insights"
    text = render_playbook(load_playbook(), with_entry(ADAPTERS[sources.CLAUDE_CODE], entry))
    for sub in ("scan", "verify-obs", "render-profile"):
        assert f"uvx ai-coding-insights {sub}" not in text, f"{sub} 仍走裸 PyPI 前缀"
    assert f"{entry} scan" in text


def test_plan_install透出entry(tmp_path):
    """`--print` 是用户写盘前唯一的核对窗口，前缀必须看得见。"""
    entry = "uv run --project /x python -m ai_coding_insights"
    plan = plan_install(SAMPLE, with_entry(ADAPTERS[sources.CLAUDE_CODE], entry))
    assert plan["entry"] == entry


# --------------------------------------------------------------------- CLI 接线

def test_cli_entry参数覆盖推断(capsys, monkeypatch):
    from ai_coding_insights.cli import main
    monkeypatch.setenv("CLAUDECODE", "1")
    entry = "uv run --project /tmp/aci python -m ai_coding_insights"
    assert main(["install", "--print", "--entry", entry]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["entry"] == entry
    assert "Bash(uv run *)" in plan["frontmatter"]["allowed-tools"]


def test_cli不给entry时走推断(capsys, monkeypatch):
    from ai_coding_insights.cli import main
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setattr("ai_coding_insights.installers.read_direct_url",
                        lambda: _du(url=_GIT_URL, vcs_info={"vcs": "git", "commit_id": "abc"}))
    assert main(["install", "--print"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["entry"] == f"uvx --from git+{_GIT_URL} ai-coding-insights"


def test_cli推断失败时退回PyPI(capsys, monkeypatch):
    from ai_coding_insights.cli import main
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setattr("ai_coding_insights.installers.read_direct_url", lambda: None)
    assert main(["install", "--print"]) == 0
    assert json.loads(capsys.readouterr().out)["entry"] == _ACI_ENTRY
