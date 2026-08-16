#!/usr/bin/env python3
"""从 playbook 模板重新生成 CC 插件形态的 SKILL.md。

`skills/ai-coding-insights/SKILL.md` 是**生成物**，不要手改——改了会在下次生成时被
覆盖，而且在此之前模板与生成物会不一致（契约测试会红，但只有跑测试才发现）。
要改内容请改 `playbook/PLAYBOOK.md`，然后跑：

    uv run python scripts/render-plugin-skill.py

为什么需要这一步：marketplace 装的是整个仓库，插件自带的 skill 必须是**已渲染**的
（`<ACI>` 之类占位符对 LLM 毫无意义）；而 `uvx … install` 那条路是在用户机器上现渲染。
同一份模板、两种分发形态，故插件那份要预先烘焙进仓库。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ai_coding_insights.installers import (PLUGIN_ADAPTER, render_playbook,  # noqa: E402
                                           unsubstituted_placeholders)
from ai_coding_insights.playbook import load_playbook  # noqa: E402


def main() -> int:
    text = load_playbook()
    rendered = render_playbook(text, PLUGIN_ADAPTER)
    left = unsubstituted_placeholders(rendered)
    if left:
        # 渲染不干净就别落盘：留着占位符的 SKILL.md 会让插件用户的编排在第 1 步就停住
        print(f"渲染后仍有未替换的安装期占位符：{left}", file=sys.stderr)
        return 1
    target = PLUGIN_ADAPTER.target()
    target.parent.mkdir(parents=True, exist_ok=True)
    old = target.read_text(encoding="utf-8") if target.is_file() else None
    if old == rendered:
        print(f"已是最新，无需改动：{target}")
        return 0
    target.write_text(rendered, encoding="utf-8")
    print(f"已生成：{target}（{len(rendered)} 字符）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
