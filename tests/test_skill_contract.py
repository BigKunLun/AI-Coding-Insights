"""跨层文件契约的可执行闸门：把 CLAUDE.md 里「改任何接缝必须两侧同步」的人工纪律变成测试。

背景：规则层（Python）与 LLM 层（SKILL.md）靠文件契约衔接。契约失配的危害不是报错，
而是**不报错**——SKILL.md 自己就写着「专家读到错配的 obs 不报错，只会安静产出错误结论」。
同理，改个 `--obs-glob` 参数名或 profile 字段名，全量测试照样绿，SKILL.md 静默失配，
用户拿到一份**看起来正常的错报告**。本文件用纯文本/内省比对把这几组接缝钉死：

1. CLI 参数双向差集（文档提到的必须存在；CLI 有的必须被文档提及或显式豁免）
2. SKILL.md 内嵌的 profile.json 示例必须过 profile_schema.validate_profile
3. reset 删除白名单必须覆盖全部 3 处真相源（snapshots / reports / run）
4. `.auto-scan.lock` 锁协议在 auto-scan 与 reset 三处的格式常量一致
5. render-profile 的 stdout 行前缀与姿态/档位值域（SKILL 第 5 步逐字照搬）
6. 中间 JSON 的**字段名**（CLAUDE.md 列的 5 份全覆盖）：
   - `_aggregate.json`（含 friction_stats 等嵌套子键）、manifest（stdout 清单）、
     `_window.json`、`profile.json`（走 schema）；`friction_stats` 最危险，它在规则层除
     signals/models 外没有任何消费者，是纯 LLM 契约，改名后全量测试照样绿。
   - `batch-NN.json`（6b）：**安静失败**的一侧。`text`/`anchors` 改名后 extractor
     读不到字段，behavior/posture 全靠猜，报告照样出得来，verify-obs 也拦不住。
   - `obs-*.json`（6c）：响亮失败（verify-obs 拦成「整批 posture_counts 缺失」），
     但代价是整轮重派，仍值得在提交时就红。

全部离线纯文本，不跑 LLM、不碰真实 ~/.ai-coding-insights。

**定位一律用内容锚点，不用序号/位置**：守卫因无关排版而红，最后一定被人放宽或关掉。
"""
import inspect
import json
import re
from pathlib import Path

import pytest

from ai_coding_insights import cli, parse_health
from ai_coding_insights.cli import _RESET_PRODUCTS, build_parser
from ai_coding_insights.profile_schema import validate_profile
from ai_coding_insights.snapshot import DEFAULT_SNAPSHOT_DIR

仓库根 = Path(__file__).resolve().parent.parent
SKILL_路径 = 仓库根 / "skills" / "ai-coding-insights" / "SKILL.md"
CLAUDE_路径 = 仓库根 / "CLAUDE.md"
HOOK_路径 = 仓库根 / "hooks" / "auto-scan-hook.sh"

# 所有会写出 CLI 调用行的文档 / 脚本：任何一处提到的子命令与参数都必须真实存在。
文档来源 = (
    SKILL_路径,
    CLAUDE_路径,
    仓库根 / "README.md",
    仓库根 / "commands" / "reset.md",
    HOOK_路径,
)

调用标记 = "python -m ai_coding_insights"


# ---------------------------------------------------------------- 纯函数解析层

def 清洗令牌(tok: str) -> str:
    """剥掉 markdown 反引号、引号与中文标点等噪声，只留标识符主体。"""
    return re.sub(r"^[^A-Za-z0-9_-]+|[^A-Za-z0-9_*.-]+$", "", tok)


def 抽取调用(文本: str) -> dict[str, set[str]]:
    """从任意文档/脚本文本里抽出 `python -m ai_coding_insights <子命令> --flag…` 的调用面。

    纯函数：入参是文本、出参是「子命令 → 用到的长参数集合」。
    - 先把 shell 的反斜杠续行拼成一行（hook 脚本是多行写法）。
    - 只取调用标记**之后**的令牌——标记之前的 `--project` 属于 uv，不是本 CLI 的参数。
    - 首个令牌不以 `-` 开头即子命令；以 `-` 开头是历史的「无子命令」写法，按 scan 归入。
    """
    文本 = re.sub(r"\\\s*\n\s*", " ", 文本)
    结果: dict[str, set[str]] = {}
    for 行 in 文本.splitlines():
        位置 = 行.find(调用标记)
        if 位置 < 0:
            continue
        令牌 = 行[位置 + len(调用标记):].split()
        if not 令牌:
            continue
        if 令牌[0].startswith("-"):
            子命令, 其余 = "scan", 令牌
        else:
            子命令, 其余 = 清洗令牌(令牌[0]), 令牌[1:]
        if not 子命令:
            continue
        参数 = {清洗令牌(t.split("=")[0]) for t in 其余 if t.startswith("--")}
        结果.setdefault(子命令, set()).update(p for p in 参数 if p.startswith("--"))
    return 结果


def 抽取注册面() -> dict[str, set[str]]:
    """内省 argparse 实际注册的子命令与长参数（排除 argparse 自带的 --help）。"""
    _, sub = build_parser()
    return {
        名字: {
            选项
            for 动作 in p._actions
            for 选项 in 动作.option_strings
            if 选项.startswith("--") and 选项 != "--help"
        }
        for 名字, p in sub.choices.items()
    }


def 抽取CLAUDE子命令清单(文本: str) -> tuple[int, list[str]]:  # noqa: N802
    """从 CLAUDE.md 抽出「规则层共 N 个子命令」的 N 与其后的 `- \\`名字\\` ——` 条目名。"""
    m = re.search(r"规则层共\s*(\d+)\s*个子命令", 文本)
    声明数 = int(m.group(1)) if m else -1
    名字 = re.findall(r"^-\s+`([a-z][a-z-]*)`\s*——", 文本, re.MULTILINE)
    return 声明数, 名字


def 抽取shell变量(文本: str, 名字: str) -> str | None:  # noqa: N802
    """从 shell 脚本里读一个形如 `NAME="value"` 的赋值。"""
    m = re.search(rf'^{名字}="([^"]+)"', 文本, re.MULTILINE)
    return m.group(1) if m else None


def 抽取参数取值(文本: str, 参数: str) -> str | None:
    """取文档里某个长参数后面紧跟的取值令牌（去引号）。"""
    m = re.search(rf'{re.escape(参数)}\s+("[^"]+"|\'[^\']+\'|\S+)', 文本)
    return m.group(1).strip("\"'") if m else None


def 归一化家目录(路径: str) -> str:
    """把 `${HOME}` / `$HOME` / `~` 统一成实际家目录，便于跨 md 与 sh 比对。"""
    return re.sub(r"^(\$\{HOME\}|\$HOME|~)", str(Path.home()), 路径.strip("\"'"))


def 抽取profile示例(skill文本: str) -> dict:  # noqa: N802
    """按**内容锚点**从 SKILL.md 全部 json 代码块里挑出 profile.json 示例块。

    刻意不按序号取块。原实现断言「全文恰好 1 个 ```json 块」并取 `块[0]`，等于把
    「文档里能不能再出现第二个 json 块」也一起锁死了：给第 2 步 obs 结构块补个语言标注
    （纯排版、不动任何契约）就会让守卫红，且 `块[0]` 会换成 obs 块导致拿错块比对。
    这里改成认「顶层含 breadth/depth/outcome」的那一块，解析不了的块直接跳过。
    """
    候选 = []
    for 块 in re.findall(r"```json\s*\n(.*?)\n```", skill文本, re.DOTALL):
        try:
            obj = json.loads(块)
        except json.JSONDecodeError:
            continue   # 带 <n> 之类占位符的示意块不是 JSON，本就不该参与比对
        if isinstance(obj, dict) and {"breadth", "depth", "outcome"} <= set(obj):
            候选.append(obj)
    assert len(候选) == 1, (
        f"SKILL.md 里应恰好有 1 个 profile.json 示例块（顶层含 breadth/depth/outcome），"
        f"实际 {len(候选)} 个"
    )
    return 候选[0]


def 抽取drift提醒段(skill文本: str) -> str:
    """按内容锚点截取第 5 步 `drift_flags` 提醒块：从提到它的那条一级列表项起，
    到下一条一级列表项止。

    原实现用 `split("drift_flags")[-1]` 取「最后一次出现之后的全文」，是位置依赖：
    文档别处（哪怕正文）再提一次 drift_flags，提醒段就被截空、整片 kind 误报漏述。
    """
    m = re.search(r"^- [^\n]*drift_flags[^\n]*\n(?:(?!^- ).*\n)*", skill文本, re.MULTILINE)
    assert m, "SKILL.md 第 5 步找不到 drift_flags 提醒块（一级列表项）"
    return m.group(0)


# ------------------------------------------------- 显式豁免白名单（不是默认放过）

# CLI 注册了但任何文档调用行都没出现的**子命令**：必须在此显式写明理由。
未文档化子命令豁免 = {
    "calibrate": "手动调试命令：读本机快照做阈值分位定位，不进 SKILL.md 编排、不产 HTML",
}

# 编排链路上的子命令——它们的参数面直接决定报告正确性，逐个参数要么在 SKILL.md 出现，
# 要么在此显式豁免。init/auto-scan/reset/calibrate 不由 SKILL.md 编排，整体不在此列。
编排子命令 = ("scan", "verify-obs", "render-profile")

未进SKILL的参数豁免 = {  # noqa: N816
    "scan": {
        "--projects-dir": "默认即 ~/.claude/projects，编排不覆盖",
        "--config": "默认按 plugin-root 解析，编排不覆盖",
        "--days": "窗口由规则层 decide_window 决定，编排不得手填",
        "--json": "调试用输出形态，与 --emit-batches 互斥",
        "--profile-input": "调试用输出形态，与 --emit-batches 互斥",
        "--since": "调试用窗口覆写",
        "--out": "仅默认渲染形态用得到，编排走 --emit-batches",
        "--snapshot-dir": "默认即 DEFAULT_SNAPSHOT_DIR，编排不覆盖",
    },
    "verify-obs": {},
    "render-profile": {
        "--out": "刻意不传：报告名由规则层按日期生成（SKILL.md 第 4 步明文要求）",
        "--projects-dir": "默认即 ~/.claude/projects，编排不覆盖",
        "--days": "窗口由 --window 文件透传，编排不得手填",
        "--config": "默认按 plugin-root 解析，编排不覆盖",
        "--snapshot-dir": "默认即 DEFAULT_SNAPSHOT_DIR，编排不覆盖",
        "--no-snapshot": "调试用：跳过写快照，正式编排必须写",
    },
}


@pytest.fixture(scope="module")
def skill文本():
    return SKILL_路径.read_text(encoding="utf-8")


# ------------------------------------------------------- 1. CLI 参数双向差集

def test_文档提到的子命令与参数在_CLI_里都真实存在():
    """方向一（文档 → CLI）：文档写了但 CLI 没注册 = 契约断裂，必须红。"""
    注册 = 抽取注册面()
    失配 = []
    for 源 in 文档来源:
        for 子命令, 参数集 in 抽取调用(源.read_text(encoding="utf-8")).items():
            if 子命令 not in 注册:
                失配.append(f"{源.name}: 子命令 `{子命令}` 未在 cli.py 注册")
                continue
            for p in sorted(参数集 - 注册[子命令]):
                失配.append(f"{源.name}: `{子命令} {p}` 未在 cli.py 注册")
    assert not 失配, "文档写的调用面在 CLI 不存在（改了 CLI 忘了改文档）：\n" + "\n".join(失配)


def test_文档至少覆盖了每个子命令的一次真实调用():
    """方向二（CLI → 文档）：注册了却无人文档化的子命令必须显式豁免，不默认放过。"""
    注册 = 抽取注册面()
    被文档化 = set()
    for 源 in 文档来源:
        被文档化 |= set(抽取调用(源.read_text(encoding="utf-8")))
    缺文档 = set(注册) - 被文档化 - set(未文档化子命令豁免)
    assert not 缺文档, f"这些子命令没有任何文档调用行，也没写进豁免白名单：{sorted(缺文档)}"
    # 豁免不许腐烂：写在白名单里却其实已经被文档化了，说明白名单该清理。
    僵尸豁免 = set(未文档化子命令豁免) & 被文档化
    assert not 僵尸豁免, f"这些豁免已失效（文档里其实已有调用行），请从白名单删除：{sorted(僵尸豁免)}"


def test_编排子命令的每个参数要么进_SKILL_要么显式豁免(skill文本):
    """方向二（细粒度）：编排链路上新增参数，必须要么写进 SKILL.md，要么写明豁免理由。"""
    注册 = 抽取注册面()
    skill调用 = 抽取调用(skill文本)
    漏网 = []
    for 子命令 in 编排子命令:
        豁免 = 未进SKILL的参数豁免.get(子命令, {})
        for p in sorted(注册[子命令] - skill调用.get(子命令, set()) - set(豁免)):
            漏网.append(f"{子命令} {p}")
    assert not 漏网, (
        "这些参数既没出现在 SKILL.md，也没在 未进SKILL的参数豁免 里写明理由：\n"
        + "\n".join(漏网)
    )


def test_参数豁免白名单本身不腐烂(skill文本):
    """豁免条目必须真的用得上：参数已被 SKILL.md 用了却还挂着豁免 = 白名单该清。"""
    注册 = 抽取注册面()
    skill调用 = 抽取调用(skill文本)
    僵尸 = []
    for 子命令, 豁免 in 未进SKILL的参数豁免.items():
        for p, 理由 in 豁免.items():
            assert 理由.strip(), f"{子命令} {p} 的豁免理由为空"
            if p not in 注册[子命令]:
                僵尸.append(f"{子命令} {p}（CLI 已不再注册该参数）")
            elif p in skill调用.get(子命令, set()):
                僵尸.append(f"{子命令} {p}（SKILL.md 里其实已经在用）")
    assert not 僵尸, "豁免白名单已腐烂，请清理：\n" + "\n".join(僵尸)


def test_CLAUDE_子命令清单与实际注册面一致():  # noqa: N802
    """CLAUDE.md 的「规则层共 N 个子命令」+ 逐条说明，必须与 argparse 注册面一一对应。"""
    声明数, 清单 = 抽取CLAUDE子命令清单(CLAUDE_路径.read_text(encoding="utf-8"))
    注册 = set(抽取注册面())
    assert set(清单) == 注册, (
        f"CLAUDE.md 子命令清单与实际注册不一致：文档多 {sorted(set(清单) - 注册)}，"
        f"文档缺 {sorted(注册 - set(清单))}"
    )
    assert 声明数 == len(注册), f"CLAUDE.md 写「共 {声明数} 个子命令」，实际注册 {len(注册)} 个"


# ------------------------------------------- 2. SKILL.md 内嵌 profile.json 示例

def test_SKILL_里的_profile_示例能过_schema_校验(skill文本):  # noqa: N802
    """schema 一改而 SKILL.md 示例没跟着改 → 立刻红。

    第 4 步内嵌的 profile.json 结构示例是 LLM 层照抄的模板；它若不合 schema，
    专家照着写出来的画像会在渲染时才失败（甚至静默少字段）。
    """
    示例 = 抽取profile示例(skill文本)
    assert validate_profile(示例) == [], validate_profile(示例)


def test_SKILL_示例覆盖了渲染必需的顶层键(skill文本):  # noqa: N802
    """示例不能只是「能过校验」——渲染要用到的键都得示范到，否则 LLM 照抄会漏。"""
    示例 = 抽取profile示例(skill文本)
    assert set(示例) == {"breadth", "depth", "outcome", "frictions", "evidence", "highlights"}
    assert set(示例["outcome"]) >= {"landed", "total"}, "outcome 必须示范 landed/total（落地率口径）"


def test_drift_flags_的每种_kind_在_SKILL_里都有对应文案(skill文本):
    """`_aggregate.json` 的 `parse_health.drift_flags` 是中间 JSON 契约的一部分。

    规则层新增一种 kind 而 SKILL.md 第 5 步提醒没跟着分述，用户会拿到方向错误的
    可信度提醒（例如把「虚高」说成「漏数」）——照样不报错，照样是错报告。
    """
    源码 = inspect.getsource(parse_health)
    kinds = set(re.findall(r'(?:\bkind\s*=\s*|"kind":\s*)"(\w+)"', 源码))
    assert kinds, "parse_health 里抽不到任何 drift kind 字面量"
    提醒段 = 抽取drift提醒段(skill文本)
    漏述 = sorted(k for k in kinds if f'kind="{k}"' not in 提醒段)
    assert not 漏述, f"SKILL.md 的 drift_flags 提醒未按 kind 分述：{漏述}"


def test_drift_flags_的每种_kind_在_HTML_渲染层也分文案():  # noqa: N802
    """同一条契约的另一侧：HTML 才是用户真正看的产物，也必须按 kind 分述。

    钉的是「每个 kind 都命中显式分支、没有一个掉进兜底」，而不是「文案两两不同」——
    后者挡不住上一轮踩过的那条路径：`_drift_flag_text` 末尾有一条**无方向**的兜底
    （`_DRIFT_FALLBACK`，为缺 kind 的老 `_aggregate.json` 保留），任何未分支的新 kind
    都落到它，文案自然与已有几条都不同，「两两不同」恒成立、一次只加一个 kind 永远不红。
    于是「加 kind → SKILL 侧守卫红 → 补 SKILL.md → 全绿放行」就成了引人入坑的路径，
    渲染层静默输出一句不含方向、不含硬指标提醒的兜底话。

    只认哨兵常量、不认具体措辞：改文案不会误红，漏分支任意个数都会红。
    """
    from ai_coding_insights.report import (_DRIFT_FALLBACK, _drift_flag_text,
                                           _render_health_section)

    源码 = inspect.getsource(parse_health)
    kinds = sorted(set(re.findall(r'(?:\bkind\s*=\s*|"kind":\s*)"(\w+)"', 源码)))
    assert kinds, "parse_health 里抽不到任何 drift kind 字面量"
    骨架 = {"cc_version_span": {"min": "1.0.0", "max": "1.0.9", "distinct": 2},
            "unknown_record_types": []}
    文案 = {}
    for k in kinds:
        flag = {"signal": "edit", "kind": k, "older_rate": 0.9, "newer_rate": 0.1,
                "older_median": 3, "newer_median": 9, "median_ratio": 3.0}
        文案[k] = _render_health_section(dict(骨架, drift_flags=[flag]), 1)
    掉兜底 = sorted(k for k in kinds if _DRIFT_FALLBACK in 文案[k])
    assert not 掉兜底, (
        f"这些 kind 在 HTML 渲染层没有显式分支、落到了无方向的兜底文案"
        f"（只同步了 SKILL.md、漏同步 report.py）：{掉兜底}"
    )
    assert len(set(文案.values())) == len(kinds), (
        f"这些 kind 在 HTML 渲染层共用同一套文案（漏同步渲染层）：{文案}"
    )
    # 兜底本身必须还在（缺 kind 的老 _aggregate.json 走它），且确实是「无方向」那一句
    assert _DRIFT_FALLBACK in _drift_flag_text({"signal": "edit", "older_rate": 0.3,
                                                "newer_rate": 0.0})


# ------------------------------------------------- 3. reset 删除白名单覆盖性

def test_reset_白名单覆盖快照目录真相源():
    """真相源之一：snapshot.py 的 DEFAULT_SNAPSHOT_DIR。

    注意「`DEFAULT_SNAPSHOT_DIR.name in _RESET_PRODUCTS`」单看是恒真的（白名单本就是
    从它取值），所以这里钉两件真会失效的事：
    1. 白名单必须**引用**该常量而非硬编码 "snapshots"——一旦有人改成字面量，
       snapshot.py 改目录名时 reset 就会静默漏删；
    2. 快照目录必须落在 reset 的 --state-dir 之下，否则白名单里的名字压根指不到它。
    """
    白名单源码 = re.search(r"^_RESET_PRODUCTS\s*=\s*\(.*?\)$",
                       inspect.getsource(cli), re.MULTILINE | re.DOTALL)
    assert 白名单源码, "cli.py 里读不到 _RESET_PRODUCTS 赋值"
    assert "DEFAULT_SNAPSHOT_DIR.name" in 白名单源码.group(0), (
        "_RESET_PRODUCTS 必须引用 DEFAULT_SNAPSHOT_DIR.name 随动，不得硬编码快照目录名"
    )
    assert DEFAULT_SNAPSHOT_DIR.name in _RESET_PRODUCTS
    assert DEFAULT_SNAPSHOT_DIR.parent == _reset默认状态目录(), (
        "快照目录不在 reset 的 --state-dir 之下，reset 会漏删"
    )


def test_reset_白名单覆盖_hook_的报告目录真相源():
    """真相源之二：hooks/auto-scan-hook.sh 的 REPORT_DIR（无 Python 常量可引，只能读脚本）。"""
    报告目录 = 抽取shell变量(HOOK_路径.read_text(encoding="utf-8"), "REPORT_DIR")
    assert 报告目录, "hooks/auto-scan-hook.sh 里读不到 REPORT_DIR 赋值"
    路径 = Path(归一化家目录(报告目录))
    assert 路径.name in _RESET_PRODUCTS, f"REPORT_DIR={报告目录} 的落点不在 reset 白名单"
    assert 路径.parent == _reset默认状态目录(), "报告目录不在 reset 的 --state-dir 之下，reset 会漏删"


def test_reset_白名单覆盖_SKILL_的_emit_batches_落点(skill文本):
    """真相源之三：SKILL.md 里 `--emit-batches` 的落点目录（中间 JSON 全落这里）。"""
    落点 = 抽取参数取值(skill文本, "--emit-batches")
    assert 落点, "SKILL.md 里读不到 --emit-batches 的取值"
    路径 = Path(归一化家目录(落点))
    assert 路径.name in _RESET_PRODUCTS, f"--emit-batches 落点 {落点} 不在 reset 白名单"
    assert 路径.parent == _reset默认状态目录(), "batches 落点不在 reset 的 --state-dir 之下，reset 会漏删"


def test_reset_白名单覆盖滚动日志且不多不少():
    """auto-scan 的滚动日志名以 _cmd_auto_scan 源码为准；白名单不得有多余项。"""
    源码 = inspect.getsource(cli._cmd_auto_scan)
    日志名 = re.search(r'state_dir\s*/\s*"([^"]*\.log)"', 源码)
    assert 日志名, "_cmd_auto_scan 里读不到滚动日志文件名"
    assert 日志名.group(1) in _RESET_PRODUCTS
    # 白名单恰好 = 3 处真相源 + 滚动日志，多一项就说明有人顺手塞了没登记的东西
    assert len(_RESET_PRODUCTS) == 4 and len(set(_RESET_PRODUCTS)) == 4


def _reset默认状态目录() -> Path:
    """reset 的 --state-dir 默认值——三处落点都必须在它之下，否则 reset 静默漏删。"""
    _, sub = build_parser()
    for 动作 in sub.choices["reset"]._actions:
        if "--state-dir" in 动作.option_strings:
            return Path(动作.default).expanduser()
    raise AssertionError("reset 未注册 --state-dir")


# ------------------------------------------- 4. .auto-scan.lock 锁协议三处一致

锁文件名 = ".auto-scan.lock"
锁日期格式 = "%Y-%m-%d"


def test_锁协议在_auto_scan_与_reset_两侧的文件名一致():
    """写锁（auto-scan）/ 读锁（auto-scan）/ 置锁（reset）必须指同一个文件。

    只搜函数源码里有没有这串字面量是不够的——两个函数的 docstring 里都提到了
    `.auto-scan.lock`，改坏代码也搜得到。所以这里只认「`state_dir / "<锁名>"`」
    这种真正构造路径的写法。
    """
    锁名 = {}
    for 名字, 函数 in (("auto-scan", cli._cmd_auto_scan), ("reset", cli._cmd_reset)):
        源码 = inspect.getsource(函数)
        锁名[名字] = set(re.findall(r'state_dir\s*/\s*"(\.[^"]*lock)"', 源码))
    assert 锁名["auto-scan"] == 锁名["reset"] == {锁文件名}, (
        f"锁文件名两侧不一致或不是约定值：{锁名}"
    )
    # 锁刻意不在删除集：删锁会解除抑制，正是「reset 后重跑仍 too_soon」的根因。
    assert 锁文件名 not in _RESET_PRODUCTS


def test_锁内容格式在两侧都是_UTC_日期且完全一致():
    """格式一变（比如改成 %Y%m%d 或带时分秒），reset 置的锁 auto-scan 就认不出来。"""
    格式集 = {}
    for 名字, 函数 in (("auto-scan", cli._cmd_auto_scan), ("reset", cli._cmd_reset)):
        源码 = inspect.getsource(函数)
        格式集[名字] = set(re.findall(r'strftime\("([^"]+)"\)', 源码))
        assert "timezone.utc" in 源码, f"{名字} 的锁日期不是 UTC 口径"
    assert 格式集["auto-scan"] == 格式集["reset"] == {锁日期格式}, (
        f"锁日期格式两侧不一致或不是约定值：{格式集}"
    )


def test_auto_scan_读锁比对的是当日格式化结果():
    """读锁侧必须拿 strftime 出来的同一个变量做等值比对，而不是自己另解析一套。"""
    源码 = inspect.getsource(cli._cmd_auto_scan)
    变量 = re.search(r"(\w+)\s*=\s*datetime\.now\(timezone\.utc\)\.strftime", 源码)
    assert 变量, "_cmd_auto_scan 里找不到锁日期变量"
    assert re.search(rf"==\s*{变量.group(1)}\b", 源码), (
        f"读锁未与写锁变量 {变量.group(1)} 做等值比对，两侧可能各算各的"
    )


def test_reset_写出的锁内容能被约定格式解析(tmp_path):
    """端到端补一刀：reset 真写出来的内容必须正好是 UTC `%Y-%m-%d`。"""
    from datetime import datetime, timezone

    assert cli.main(["reset", "--state-dir", str(tmp_path)]) == 0
    内容 = (tmp_path / 锁文件名).read_text(encoding="utf-8").strip()
    assert datetime.strptime(内容, 锁日期格式).date() == datetime.now(timezone.utc).date()


# ----------------------------- 5. render-profile stdout 四行（SKILL.md 逐字照搬）

def test_SKILL_逐字照搬的_stdout_行前缀在_cli_里真实打印(skill文本):  # noqa: N802
    """SKILL.md 第 5 步要求「逐字照搬」这几行，行前缀一改，编排端就取不到值。"""
    源码 = inspect.getsource(cli._cmd_render_profile)
    for 前缀 in ("姿势分布: ", "姿态健康态: ", "成熟度档位: "):
        assert f'print("{前缀}"' in 源码, f"cli 不再打印「{前缀}」这一行，SKILL.md 第 5 步会取空"
        assert 前缀.rstrip() in skill文本, f"SKILL.md 第 5 步没提到「{前缀.rstrip()}」"


def test_SKILL_列的姿态值域与档位名与_stage_真相源一致(skill文本):  # noqa: N802
    """值域是 stage.py 定的；SKILL.md 把它抄了一份给编排端做「不要当褒奖」的判读。

    规则层新增/改名一档而 SKILL.md 没跟着改，编排端会拿到一个它没被告知的值——
    照样不报错，只会安静地把它解释错。
    """
    from ai_coding_insights import stage

    姿态值域 = set(re.findall(r'"state":\s*"([^"]+)"', inspect.getsource(stage.diagnose_posture)))
    档位值域 = set(re.findall(r'\(\d,\s*"([^"]+)",\s*\[', inspect.getsource(stage._stages)))
    assert 姿态值域 and 档位值域, "stage.py 里抽不到值域字面量"
    assert not (漏 := sorted(v for v in 姿态值域 if v not in skill文本)), \
        f"SKILL.md 第 5 步的姿态健康态值域漏了：{漏}"
    assert not (漏 := sorted(v for v in 档位值域 if v not in skill文本)), \
        f"SKILL.md 第 5 步的成熟度档位值域漏了：{漏}"


# ------------------------- 6. 中间 JSON 字段名（_aggregate.json / manifest / window）

def 抽取反引号标识符(skill文本: str) -> set[str]:  # noqa: N802
    """SKILL.md 里反引号包住、且**形状上像字段名**的标识符（可含点号路径）。

    反引号在 SKILL.md 里是多用途的：工具名（`Read`/`Write`）、文件名（`SKILL.md`、
    `batch-NN.json`）、CLI 参数（`--obs-glob`）、表达式（`edit_count÷git_landed_count`）、
    占位符（`${HOME}`）都包在里面。若把它们一律当字段名，守卫会天天误报、最后被人关掉。
    所以这里只留 snake_case 形状的令牌，把「是不是真字段」交给下面的对照集合判定。
    `aggregate.xxx` 前缀是同一个字段的另一种写法，统一剥掉。
    """
    令牌 = re.findall(r"`([^`\n]+)`", skill文本)
    return {re.sub(r"^aggregate\.", "", t) for t in 令牌
            if re.fullmatch(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*", t)}


def 反引号内提到(skill文本: str, 名字: str) -> bool:
    """SKILL.md 的某个反引号片段里是否作为独立词提到过该标识符。

    专供「清单不腐烂」这类**宽松方向**的检查：`batch_count == 0`、
    `edit_count÷git_landed_count` 这类片段整体不是标识符形状，但确实是在引用该字段。
    """
    return re.search(rf"`[^`\n]*\b{re.escape(名字)}\b[^`\n]*`", skill文本) is not None


def 抽取aggregate键集() -> set[str]:  # noqa: N802
    """`_aggregate.json` 的实际顶层键集合（= LLM 层读到的那份 dict 的键）。

    真相源是 AggregateMetrics 的 dataclass 字段 + property（`_metrics_dict` 手动补的
    landed_ratio/dropped_count），再叠上 `_emit_batches` 里对 agg 的增删。
    刻意不写死名单：signals/models 改名后这里跟着变，SKILL.md 的引用才会露馅。
    """
    import dataclasses

    from ai_coding_insights.models import AggregateMetrics

    字段 = {f.name for f in dataclasses.fields(AggregateMetrics)}
    属性 = {n for n, v in vars(AggregateMetrics).items() if isinstance(v, property)}
    源码 = inspect.getsource(cli._emit_batches)
    追加 = set(re.findall(r'agg\["(\w+)"\]\s*=', 源码))
    剥离 = set(re.findall(r'k\s*!=\s*"(\w+)"', 源码))   # project_breakdown 主动剥掉（隐私）
    assert 剥离, "_emit_batches 里读不到 agg 的剥离规则"
    return (字段 | 属性 | 追加) - 剥离


def 抽取dict字面量键(源码: str, 变量名: str) -> set[str]:
    """从 `变量名 = {` 到同缩进 `}` 之间抽 `"key":` —— 用于嵌套子键的真相源。"""
    m = re.search(rf"^(\s*){re.escape(变量名)}\s*=\s*\{{(.*?)^\1\}}", 源码,
                  re.DOTALL | re.MULTILINE)
    assert m, f"源码里读不到 `{变量名} = {{...}}` 字面量"
    return set(re.findall(r'"(\w+)":', m.group(2)))


# SKILL.md 逐字引用的 `_aggregate.json` 顶层键（人工核对过：确实指 aggregate 字段，
# 不是工具名 / obs 字段 / profile 字段）。规则层改名而 SKILL.md 没跟着改 → 这里红。
SKILL引用的aggregate键 = {  # noqa: N816
    "anchor_counts", "avg_turns", "background_sessions", "background_task_count",
    "commit_count", "dropped_count", "edit_count", "friction_stats",
    "git_commit_total", "git_landed_count", "landed_ratio", "max_parallel_agents",
    "mcp_sessions", "model_counts", "parallel_agent_turns", "parse_health",
    "session_count", "skill_total_counts", "subagent_sessions",
    "thinking_block_count", "thinking_sessions", "tool_breadth",
    "tool_session_counts", "trend", "workflow_sessions",
}

# SKILL.md 引用的嵌套子键：{父键: (源码所在模块, 字面量变量名, 被引用的子键集合)}。
# `friction_stats` 是最危险的一类——它在规则层除 signals/models 外**没有任何消费者**，
# 属于纯 LLM 契约：改名后全量测试照样绿，教练读不到该字段，安静退回泛化建议。
SKILL引用的子键 = {  # noqa: N816
    "friction_stats": ("signals", "friction_stats",
                       {"error_session_count", "error_top_counts", "override_top_counts"}),
    "anchor_counts": ("signals", "anchor_counts", {"override"}),
}


def test_SKILL_引用的_aggregate_字段在规则层都真实存在():  # noqa: N802
    """`_aggregate.json` 字段名是纯文件契约：规则层改名不会报错，只会让专家读到空值。

    典型事故：signals.py 把 `friction_stats` 改名并顺手改了 test_signals_aggregate.py 的
    断言 —— 全量测试仍绿，但 SKILL.md 教练条还写着「输入：aggregate（含 friction_stats…）」，
    教练取不到数，安静退回泛化建议。这就是「不报错的错报告」。
    """
    实际 = 抽取aggregate键集()
    缺失 = sorted(SKILL引用的aggregate键 - 实际)
    assert not 缺失, (
        f"SKILL.md 逐字引用了这些 aggregate 字段，但规则层已不再产出（改名/删字段忘了同步 "
        f"SKILL.md）：{缺失}"
    )


def test_aggregate_引用清单既不遗漏也不腐烂(skill文本):  # noqa: N802
    """清单两侧都不许烂：写进清单的必须真在 SKILL.md 出现；SKILL.md 新引的必须进清单。"""
    实际 = 抽取aggregate键集()
    令牌 = {t.split(".")[0] for t in 抽取反引号标识符(skill文本)}
    僵尸 = sorted(k for k in SKILL引用的aggregate键 if not 反引号内提到(skill文本, k))
    assert not 僵尸, f"这些键已不在 SKILL.md 里出现，请从 SKILL引用的aggregate键 删除：{僵尸}"
    漏登记 = sorted((令牌 & 实际) - SKILL引用的aggregate键)
    assert not 漏登记, (
        f"SKILL.md 新引用了这些 aggregate 字段却没进守卫清单（进 SKILL引用的aggregate键）：{漏登记}"
    )


def test_SKILL_引用的嵌套子键在规则层字面量里都真实存在(skill文本):  # noqa: N802
    """嵌套一层的子键（如 `friction_stats.error_top_counts`）同样是纯 LLM 契约。"""
    import importlib

    实际顶层 = 抽取aggregate键集()
    失配 = []
    for 父键, (模块名, 变量名, 子键集) in SKILL引用的子键.items():
        assert 父键 in 实际顶层, f"守卫清单里的父键 `{父键}` 已不是 aggregate 字段"
        模块 = importlib.import_module(f"ai_coding_insights.{模块名}")
        实际子键 = 抽取dict字面量键(inspect.getsource(模块), 变量名)
        失配 += [f"{父键}.{k}" for k in sorted(子键集 - 实际子键)]
        # 不腐烂：登记的子键必须真的在 SKILL.md 里被引用（裸写或带父键前缀均可）
        未引用 = sorted(k for k in 子键集 if not 反引号内提到(skill文本, k))
        assert not 未引用, f"这些子键已不在 SKILL.md 出现，请从 SKILL引用的子键 删除：{未引用}"
    assert not 失配, f"SKILL.md 引用的嵌套子键在规则层已不存在（改名忘了同步）：{失配}"


def test_SKILL_按_kind_分述引用的_drift_flag_字段真实产出():  # noqa: N802
    """第 5 步的 `shift` 文案逐字引用 `older_median`/`newer_median`/`median_ratio`。

    这几个键只在 parse_health 的 drift_flags 条目里产出；改名后 SKILL.md 的分述取不到值，
    用户拿到的是一句缺数的可信度提醒——照样不报错。
    """
    源码 = inspect.getsource(parse_health.compute_parse_health)
    块 = re.findall(r"drift_flags\.append\(\{(.*?)\}\)", 源码, re.DOTALL)
    assert 块, "compute_parse_health 里读不到 drift_flags.append 的 dict 字面量"
    实际 = {k for b in 块 for k in re.findall(r'"(\w+)":', b)}
    需要 = {"signal", "kind", "older_rate", "newer_rate",
            "older_median", "newer_median", "median_ratio"}
    assert not (缺 := sorted(需要 - 实际)), (
        f"SKILL.md 第 5 步 drift_flags 分述引用的字段在 parse_health 已不产出：{缺}"
    )
    # parse_health 顶层必须真有 drift_flags 这个键（SKILL 与渲染层都按它取数）
    返回块 = re.search(r"^    return \{(.*?)^    \}", 源码, re.DOTALL | re.MULTILINE)
    assert 返回块, "compute_parse_health 里读不到返回字面量"
    assert "drift_flags" in set(re.findall(r'"(\w+)":', 返回块.group(1))), (
        "compute_parse_health 不再返回 drift_flags 键，SKILL.md 第 5 步与渲染层都会取空"
    )


def 抽取manifest键集() -> set[str]:  # noqa: N802
    """`scan --emit-batches` 打到 stdout 的清单（manifest）的顶层键。

    两条分支都算：正常清单字面量 + too_soon 提前返回的那份。
    """
    源码 = inspect.getsource(cli._emit_batches)
    正常 = 抽取dict字面量键(源码, "manifest")
    m = re.search(r'print\(json\.dumps\(\{"status": "too_soon"(.*?)\}, ensure_ascii',
                  源码, re.DOTALL)
    assert m, "_emit_batches 里读不到 too_soon 分支的 stdout 字面量"
    return 正常 | {"status"} | set(re.findall(r'"(\w+)":', m.group(1)))


# SKILL.md 逐字引用的清单键（人工核对过）。
SKILL引用的manifest键 = {  # noqa: N816
    "status", "batch_count", "batches", "included_projects",
    "plugin_root", "batches_dir", "window", "aggregate", "message",
}

未进SKILL的manifest键豁免 = {  # noqa: N816
    "mode": "与 window.mode 同值，SKILL 只用 window.mode 判取数范围",
    "days_since_last": "too_soon 分支的诊断数字，SKILL 只把 message 原样转述给用户",
}


def test_SKILL_引用的清单键在_manifest_里真实存在(skill文本):  # noqa: N802
    """manifest 是 stdout JSON 契约：键改名后编排端取到 None，静默走错分支。"""
    实际 = 抽取manifest键集()
    缺失 = sorted(SKILL引用的manifest键 - 实际)
    assert not 缺失, f"SKILL.md 引用的清单键在 _emit_batches 已不产出：{缺失}"
    # 另一条独立路径：文中「清单 `x`」这种带锚点的写法自动抽取，不依赖上面的人工清单
    自动 = {t.split(".")[0] for t in re.findall(r"清单\s*`([a-z_][a-z0-9_.]*)`", skill文本)}
    assert 自动, "SKILL.md 里抽不到任何「清单 `x`」引用"
    assert not (缺 := sorted(自动 - 实际)), f"SKILL.md「清单 `x`」引用的键不存在：{缺}"
    assert not (漏 := sorted(自动 - SKILL引用的manifest键)), \
        f"这些清单键在 SKILL.md 有锚点引用却没进守卫清单：{漏}"


def test_manifest_每个键要么进_SKILL_要么显式豁免(skill文本):  # noqa: N802
    """反向：新增清单键必须要么被 SKILL.md 用上，要么写明豁免理由；豁免不许腐烂。"""
    实际 = 抽取manifest键集()
    漏网 = sorted(实际 - SKILL引用的manifest键 - set(未进SKILL的manifest键豁免))
    assert not 漏网, (
        f"这些清单键既没进 SKILL引用的manifest键，也没在豁免白名单写明理由：{漏网}"
    )
    僵尸 = sorted(k for k in SKILL引用的manifest键 if not 反引号内提到(skill文本, k))
    assert not 僵尸, f"这些键已不在 SKILL.md 出现，请从 SKILL引用的manifest键 删除：{僵尸}"
    for k, 理由 in 未进SKILL的manifest键豁免.items():
        assert 理由.strip(), f"清单键 {k} 的豁免理由为空"
        assert k in 实际, f"豁免的清单键 {k} 已不存在，请清理白名单"


# ------------------------------ 6b. batch-NN.json（规则层产、extractor 逐字读）

def 抽取斜杠列表(skill文本: str, 锚点: str) -> set[str]:  # noqa: N802
    """按内容锚点取 SKILL.md 里 `a/b/c` 这种斜杠分隔的字段名列表。

    第 2 步的 extractor prompt 是纯文本 prompt（不是 JSON 块），字段名就写成
    「每元素一会话：session_id/cwd/file_path/signals/turns」这种斜杠串——
    规则层改个键名，prompt 里这串就成了谎话，extractor 读不到字段，安静按空处理。
    """
    m = re.search(rf"{re.escape(锚点)}\s*([a-z_]+(?:/[a-z_]+)+)", skill文本)
    assert m, f"SKILL.md 里找不到锚点「{锚点}」后面的斜杠字段列表"
    return set(m.group(1).split("/"))


def 抽取元素取键(源码: str) -> set[str]:  # noqa: N802
    """从源码里抽「对循环变量 `s` 取键」的字面量：`s["k"]` / `s.get("k")`。

    前瞻挡住 `obs["sessions"]` 这种以 s 结尾的其它变量——否则会把顶层键误算成元素键。
    """
    return set(re.findall(r'(?<![A-Za-z0-9_])s(?:\[|\.get\()"(\w+)"', 源码))


def 实建一条batch会话() -> dict:  # noqa: N802
    """真跑 `build_session_input` 拿它产出的实际键集（比正则扒源码更贴近真相源）。"""
    from ai_coding_insights.models import (OutcomeStats, ParsedSession, SessionStats,
                                           UserTurn)
    from ai_coding_insights.profile_input import build_session_input

    turns = [UserTurn("u1", "把这段改成按配置注入", "t")]
    se = ParsedSession("/f.jsonl", "sess", "/r", "main", turns, ["Bash"], ["m"], None, None)
    st = SessionStats("sess", "/r", 1, 0.0, 60.0, ["Bash"], ["m"])
    return build_session_input(se, st, OutcomeStats("sess", "/r", 0, 0, 0))


def test_batch_会话字段与_SKILL_的_extractor_prompt_双向一致(skill文本):  # noqa: N802
    """`batch-NN.json` 是**安静失败**的一侧：extractor 读不到 `text`/`anchors` 也不报错，

    只会把 behavior/posture 全靠猜，报告照样出得来。verify-obs 兜不住（它只查 obs 覆盖）。
    双向差集：prompt 写了规则层没有 = 谎话；规则层有而 prompt 没写 = extractor 不知道能用。
    """
    实际 = set(实建一条batch会话())
    文档 = 抽取斜杠列表(skill文本, "每元素一会话：")
    assert 文档 == 实际, (
        f"batch 会话字段与 SKILL.md 第 2 步的 extractor prompt 不一致："
        f"prompt 多写 {sorted(文档 - 实际)}，prompt 漏写 {sorted(实际 - 文档)}"
    )


def test_batch_turn_字段与_SKILL_的_extractor_prompt_双向一致(skill文本):  # noqa: N802
    """turn 级同理：`uuid` 决定 pointer 能否成立，`anchors` 是 posture 判据的硬锚。"""
    实际 = set(实建一条batch会话()["turns"][0])
    文档 = 抽取斜杠列表(skill文本, "每条有 ")
    assert 文档 == 实际, (
        f"batch turn 字段与 SKILL.md 第 2 步的 extractor prompt 不一致："
        f"prompt 多写 {sorted(文档 - 实际)}，prompt 漏写 {sorted(实际 - 文档)}"
    )


def test_verify_obs_读_batch_用的键在_batch_里真实存在():
    """规则层自己的另一个 batch 消费者（`_cmd_verify_obs`）也得跟着改名，不能只改产出侧。"""
    实际 = set(实建一条batch会话())
    用到 = 抽取元素取键(inspect.getsource(cli._cmd_verify_obs))
    assert 用到, "_cmd_verify_obs 里读不到它对 batch 会话取的键"
    assert not (缺 := sorted(用到 - 实际)), f"verify-obs 取的 batch 键规则层已不产出：{缺}"


# ------------------------------ 6c. obs-*.json（extractor 产、规则层逐字读）

def 抽取obs骨架键(skill文本: str) -> tuple[set[str], set[str], set[str]]:  # noqa: N802
    """按内容锚点截 SKILL.md 第 2 步的 obs 结构骨架，抽三层键。

    骨架里的值是 `<n>` / `...` 之类占位符，整段不是合法 JSON，只能按行取键名：
    返回（顶层键、会话级键、posture_counts 档位键）。
    """
    m = re.search(r'\{"sessions":\[(.*?)^\]\}', skill文本, re.DOTALL | re.MULTILINE)
    assert m, "SKILL.md 第 2 步找不到 obs 结构骨架（`{\"sessions\":[` … `]}`）"
    骨架 = m.group(1)
    pc = re.search(r'"posture_counts":\s*\{([^}]*)\}', 骨架)
    assert pc, "obs 骨架里找不到 posture_counts 字面量"
    档位 = set(re.findall(r'"(\w+)":', pc.group(1)))
    nt = re.search(r'"notable_turns":\s*\[(.*?)\]', 骨架, re.DOTALL)
    turn级 = set(re.findall(r'"(\w+)":', nt.group(1))) if nt else set()
    # 会话级键 = 骨架里出现的全部键，减去 posture_counts / notable_turns 里层的键
    会话级 = set(re.findall(r'"(\w+)":', 骨架)) - 档位 - turn级
    return {"sessions"}, 会话级, 档位


def test_规则层读_obs_用的键在_SKILL_骨架里都被要求产出(skill文本):
    """obs 失配是**响亮失败**（verify-obs 拦成「整批 posture_counts 缺失」），但代价是整轮重派。

    规则层（`obs_check` + `_cmd_verify_obs`）取的每个键，SKILL.md 的 obs 骨架都得写着；
    否则 extractor 压根不知道要产出它。同侧单元测试兜不住——改名者会一并改掉它们。
    """
    from ai_coding_insights import obs_check

    顶层, 会话级, 档位 = 抽取obs骨架键(skill文本)
    取顶层 = set(re.findall(r'obs\[?"(\w+)"', inspect.getsource(cli._cmd_verify_obs)))
    assert 取顶层, "_cmd_verify_obs 里读不到它对 obs 顶层取的键"
    assert not (缺 := sorted(取顶层 - 顶层)), f"规则层读的 obs 顶层键不在 SKILL 骨架里：{缺}"

    # 会话级只看 obs_check：`_cmd_verify_obs` 同一个函数里也在读 batch 会话（`turns`），
    # 混在一起会把 batch 的键误算进 obs 契约。obs 会话语义的真相源就是 obs_check。
    取会话级 = 抽取元素取键(inspect.getsource(obs_check))
    assert 取会话级, "obs_check 里读不到它对 obs 会话取的键"
    assert not (缺 := sorted(取会话级 - 会话级)), (
        f"规则层读的 obs 会话键不在 SKILL 骨架里（改名只改了规则层）：{缺}"
    )


def test_obs_姿态四档键两侧一致(skill文本):
    """`_POSTURE_KEYS` 是 posture_counts 校验与聚合的真相源，必须与骨架逐档对上。"""
    from ai_coding_insights.obs_check import _POSTURE_KEYS

    _, _, 档位 = 抽取obs骨架键(skill文本)
    assert set(_POSTURE_KEYS) == 档位, (
        f"posture_counts 档位两侧不一致：规则层 {sorted(_POSTURE_KEYS)}，SKILL 骨架 {sorted(档位)}"
    )


def test_SKILL_引用的_window_子键在规则层真实赋值(skill文本):  # noqa: N802
    """`_window.json` 同属中间 JSON 契约：`window.mode` / `window.truncated` 决定
    SKILL 第 1 步的取数范围判读与第 5 步的截断提醒，键名一改就静默失效。"""
    源码 = inspect.getsource(cli._emit_batches)
    实际 = set(re.findall(r'window_dict\["(\w+)"\]\s*=', 源码))
    assert 实际, "_emit_batches 里读不到 window_dict 的赋值"
    引用 = {t.split(".", 1)[1] for t in 抽取反引号标识符(skill文本)
          if t.startswith("window.")}
    assert 引用, "SKILL.md 里抽不到任何 `window.x` 引用"
    assert not (缺 := sorted(引用 - 实际)), f"SKILL.md 引用的 window 子键规则层已不产出：{缺}"
