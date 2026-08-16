"""playbook 正文的定位与读取（单一真相源的两段式解析）。

playbook = 编排文档（LLM 层的 SKILL.md）。它**只有一份真相源**：仓库里的
`skills/ai-coding-insights/SKILL.md`。各 harness 的差异不在这份文档里分叉，
而是由安装器在落位时替换占位符（见 `installers.py`）——一份正文、N 个落点。

为什么要两段式解析：
- **开发/插件场景**：仓库树就在手边，直接读 `skills/ai-coding-insights/SKILL.md`。
- **uvx / pip 场景**：用户机器上根本没有这个仓库，只有装好的 wheel。故 pyproject
  用 `force-include` 把同一份文件搬进包数据 `ai_coding_insights/playbook/PLAYBOOK.md`，
  这里退回去读它。

两处内容必须逐字相同（同一个文件搬两次），`tests/test_skill_contract.py` 有守卫。
"""
from pathlib import Path

# 仓库内真相源（相对仓库根）。改这个路径要同步 pyproject 的 force-include
# 与 tests/test_skill_contract.py 的 PLAYBOOK_路径 —— 三处一动全动。
# 注意它**不是** `skills/ai-coding-insights/SKILL.md`：那份是本模板经 CC 插件适配器
# 渲染出的**生成物**（供 marketplace 分发），由 scripts/render-plugin-skill.py 产出。
REPO_PLAYBOOK = Path("playbook") / "PLAYBOOK.md"
# wheel 内包数据落点（相对包目录）
PACKAGED_PLAYBOOK = Path("playbook") / "PLAYBOOK.md"


class PlaybookNotFound(FileNotFoundError):
    """两个位置都找不到 playbook。必须报错中止，不能拿空串继续装——
    装出一份空 playbook 的后果是用户触发后什么也不会发生，还以为装好了。"""


def repo_root() -> Path:
    """从本模块位置反推仓库根（`src/ai_coding_insights/playbook.py` → 上三层）。

    只在源码树里成立；装成 wheel 后这个路径指向 site-packages，`find_playbook`
    会因文件不存在而自动落到包数据分支，不需要额外判断「我是不是在仓库里」。
    """
    return Path(__file__).resolve().parent.parent.parent


def find_playbook(explicit: str | None = None) -> Path:
    """定位 playbook 文件：显式路径 > 仓库树 > 包数据。找不到抛 `PlaybookNotFound`。"""
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_file():
            raise PlaybookNotFound(f"--playbook 指定的文件不存在：{p}")
        return p
    candidates = [repo_root() / REPO_PLAYBOOK,
                  Path(__file__).resolve().parent / PACKAGED_PLAYBOOK]
    for c in candidates:
        if c.is_file():
            return c
    raise PlaybookNotFound(
        "找不到 playbook 正文，试过：" + "、".join(str(c) for c in candidates))


def load_playbook(explicit: str | None = None) -> str:
    return find_playbook(explicit).read_text(encoding="utf-8")
