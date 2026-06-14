"""能力盲区检测（无 IO 纯函数）。

对照「高杠杆能力全集」与窗口内实际用过的工具（aggregate.tool_session_counts
的 key 集合）+ 定制化信号 + 并行度指标，列出还没用过/未激活的能力 + 行为级
使用场景一句话。场景文案禁业务词。
"""

# (label, 检测谓词(tools 集合, customization_signals, metrics), 场景一句话)
# 谓词统一三参签名，多数项只用 tools；自建 Skill/CLAUDE.md/Hook 看 cs；并行 SubAgent 看 metrics。
_CAPABILITIES = [
    ("SubAgent 委派", lambda ts, cs, m: "Agent" in ts,
     "多个独立查询/子任务时并行派子代理，隔离主上下文、成倍提速"),
    # 「用过 Agent」≠「真并行激活」：用过 Agent 但单轮峰值<2（顺序逐个派）只在本项报盲区，
    # 与「SubAgent 委派」拆两档，避免水平维度判「真并行零」而能力盲区却说「全覆盖」。
    ("并行 SubAgent", lambda ts, cs, m: ("Agent" not in ts)
     or bool(m and (m.get("max_parallel_agents") or 0) >= 2),
     "同一轮里并发派出多个子代理（真并行），而非顺序逐个——把可独立推进的子任务一次铺开提速"),
    ("Workflow 编排", lambda ts, cs, m: "Workflow" in ts,
     "大规模迁移/审计/多视角评审时，用确定性脚本编排几十个子代理"),
    ("MCP 外部工具", lambda ts, cs, m: any(t.startswith("mcp__") for t in ts),
     "接入文档查询/网页阅读等外部数据源，减少凭记忆作答"),
    ("Skill 调用", lambda ts, cs, m: "Skill" in ts,
     "用斜杠命令沉淀重复流程（提交、评审、调试），一次调用替代长提示"),
    ("计划模式", lambda ts, cs, m: "EnterPlanMode" in ts or "ExitPlanMode" in ts,
     "动手前让 AI 先出实施计划再批准执行，减少做一半推翻重来"),
    ("任务清单", lambda ts, cs, m: "TaskCreate" in ts or "TodoWrite" in ts,
     "多步任务让 AI 维护进度清单，长会话不丢步骤"),
    ("Web 取证", lambda ts, cs, m: "WebSearch" in ts or "WebFetch" in ts,
     "涉及库版本/外部事实时让 AI 实时检索，而非依赖训练记忆"),
    ("自建 Skill", lambda ts, cs, m: bool(cs and cs.get("has_custom_skills")),
     "把重复性工作封装成自己的可复用 skill，从消费者进阶为流程产品化者"),
    ("CLAUDE.md 定制", lambda ts, cs, m: bool(cs and cs.get("claude_md_sessions", 0) > 0),
     "通过 CLAUDE.md 持久化项目约定与个人偏好，减少每次重复交代"),
    ("Hook 自动化", lambda ts, cs, m: bool(cs and cs.get("has_hooks")),
     "用 hooks 在会话生命周期（Start/End）自动触发质检、提交或格式化"),
]


def unused_capabilities(tool_session_counts: dict,
                         customization_signals: dict | None = None,
                         metrics: dict | None = None) -> list[dict]:
    """返回 [{"label", "scene"}...]，全部用过则空列表。

    customization_signals（自建 Skill/CLAUDE.md/Hook）与 metrics（并行 SubAgent 需
    max_parallel_agents）缺省时，对应能力不进入盲区——无判定依据不报假阳性。
    「并行 SubAgent」与「SubAgent 委派」拆两档：用过 Agent 但单轮峰值<2（顺序派、从未真
    并行）只在前者报，没用过 Agent 时由「SubAgent 委派」覆盖、前者不冗余报。
    """
    tools = set(tool_session_counts or {})
    # 需额外数据才能判定的能力：无对应数据时跳过，不报假阳性
    _NEEDS_CS = {"自建 Skill", "CLAUDE.md 定制", "Hook 自动化"}
    _NEEDS_METRICS = {"并行 SubAgent"}
    result = []
    for label, used, scene in _CAPABILITIES:
        if label in _NEEDS_CS and customization_signals is None:
            continue
        if label in _NEEDS_METRICS and metrics is None:
            continue
        if not used(tools, customization_signals, metrics):
            result.append({"label": label, "scene": scene})
    return result
