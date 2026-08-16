#!/usr/bin/env python3
"""从 `sources.py` 生成 README 的「三家 harness 能力矩阵」Markdown 表。

**不许手抄**：矩阵的真相源是 `sources._SOURCES` 的能力集声明与 `CAPABILITY_METRICS`
（能力键 → 缺了它就「未测量」的 aggregate 字段）。手抄一份到 README 里，等于给
「未测量 ≠ 0」这条承重约束造第二个真相源——代码放宽了能力、README 还写着未测量，
读者据此下的结论就是错的。

用法（仓库根）：
    uv run python docs/demo/生成能力矩阵.py          # 打印 Markdown 表到 stdout
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ai_coding_insights import sources          # noqa: E402
from ai_coding_insights.view_model import metric_label   # noqa: E402

# 能力键的中文说明。只做「键 → 人话」的展示映射，测得到/测不到一律由代码算。
# 有键漏登记会在下面 assert 里红，不会静默少一行。
CAP_CN = {
    sources.CAP_TOOL_CALLS: "工具调用（工具广度 / 各工具覆盖）",
    sources.CAP_SUBAGENT: "子代理派发（含真并行峰值）",
    sources.CAP_WORKFLOW: "Workflow 确定性编排",
    sources.CAP_MCP: "MCP 外部工具",
    sources.CAP_SKILL: "Skill / 斜杠命令调用",
    sources.CAP_PLAN_MODE: "计划模式（先出方案再放行）",
    sources.CAP_THINKING: "深度推理块",
    sources.CAP_BACKGROUND: "后台委托",
    sources.CAP_OPTION_PICK: "结构化选项应答",
    sources.CAP_TOKEN_USAGE: "Token 计量",
    sources.CAP_EDITED_PATHS: "会话编辑文件集（git 落地锚的交集来源）",
    sources.CAP_GIT_OP: "会话内 git 提交回执",
    sources.CAP_EDIT_COUNT: "编辑动作计数",
    sources.CAP_CLI_VERSION: "记录带 CLI 版本号（版本漂移雷达）",
    sources.CAP_CUSTOM_SKILL: "用户自建扩展（文件系统扫描）",
    sources.CAP_PROJECT_MD: "项目约定文件（CLAUDE.md 一类）",
    sources.CAP_HOOKS: "生命周期 hook（会话结束自动快照的前提）",
    sources.CAP_TODO: "任务清单工具（多步任务维护进度）",
    sources.CAP_WEB: "联网检索 / 抓取",
}


def rows():
    """[(能力中文名, 受影响的报告指标, {来源名: 测得到?})]，顺序稳定。"""
    missing = sorted(sources.ALL_CAPABILITIES - set(CAP_CN))
    assert not missing, f"能力键没登记中文名，请补 CAP_CN：{missing}"
    out = []
    for cap in sorted(sources.ALL_CAPABILITIES, key=lambda c: (
            # 先排「会落到未测量字段」的能力，再排只影响别处降级的两项
            0 if c in sources.CAPABILITY_METRICS else 1, CAP_CN[c])):
        fields = sources.CAPABILITY_METRICS.get(cap, ())
        影响 = "、".join(metric_label(f) for f in fields) or "（不进未测量字段集，各自在别处降级）"
        支持 = {n: sources.get_source(n).supports(cap) for n in sources.SOURCE_NAMES}
        out.append((CAP_CN[cap], 影响, 支持))
    return out


def markdown() -> str:
    labels = [sources.get_source(n).label for n in sources.SOURCE_NAMES]
    lines = [
        "| 能力 / 概念 | 缺失时被标「未测量」的报告指标 | " + " | ".join(labels) + " |",
        "|---|---|" + "|".join([":---:"] * len(labels)) + "|",
    ]
    for 名, 影响, 支持 in rows():
        单元 = ["✅ 测得到" if 支持[n] else "⚪️ 未测量" for n in sources.SOURCE_NAMES]
        lines.append(f"| {名} | {影响} | " + " | ".join(单元) + " |")
    return "\n".join(lines)


if __name__ == "__main__":
    print(markdown())
