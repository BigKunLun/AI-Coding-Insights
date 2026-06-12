"""能力盲区检测（无 IO 纯函数）。

对照「高杠杆能力全集」与窗口内实际用过的工具（aggregate.tool_session_counts
的 key 集合），列出还没用过的能力 + 行为级使用场景一句话。场景文案禁业务词。
"""

# (label, 检测谓词(tools 集合), 场景一句话)
_CAPABILITIES = [
    ("SubAgent 委派", lambda ts: "Agent" in ts,
     "多个独立查询/子任务时并行派子代理，隔离主上下文、成倍提速"),
    ("Workflow 编排", lambda ts: "Workflow" in ts,
     "大规模迁移/审计/多视角评审时，用确定性脚本编排几十个子代理"),
    ("MCP 外部工具", lambda ts: any(t.startswith("mcp__") for t in ts),
     "接入文档查询/网页阅读等外部数据源，减少凭记忆作答"),
    ("Skill 调用", lambda ts: "Skill" in ts,
     "用斜杠命令沉淀重复流程（提交、评审、调试），一次调用替代长提示"),
    ("计划模式", lambda ts: "EnterPlanMode" in ts or "ExitPlanMode" in ts,
     "动手前让 AI 先出实施计划再批准执行，减少做一半推翻重来"),
    ("任务清单", lambda ts: "TaskCreate" in ts or "TodoWrite" in ts,
     "多步任务让 AI 维护进度清单，长会话不丢步骤"),
    ("Web 取证", lambda ts: "WebSearch" in ts or "WebFetch" in ts,
     "涉及库版本/外部事实时让 AI 实时检索，而非依赖训练记忆"),
    ("自建 Skill", lambda ts, cs=None: bool(cs and cs.get("has_custom_skills")),
     "把重复性工作封装成自己的可复用 skill，从消费者进阶为流程产品化者"),
    ("CLAUDE.md 定制", lambda ts, cs=None: bool(cs and cs.get("claude_md_sessions", 0) > 0),
     "通过 CLAUDE.md 持久化项目约定与个人偏好，减少每次重复交代"),
    ("Hook 自动化", lambda ts, cs=None: bool(cs and cs.get("has_hooks")),
     "用 hooks 在会话生命周期（Start/End）自动触发质检、提交或格式化"),
]


def unused_capabilities(tool_session_counts: dict,
                         customization_signals: dict | None = None) -> list[dict]:
    """返回 [{"label", "scene"}...]，全部用过则空列表。

    customization_signals 可选，来自 compute_customization_signals()；
    提供后，自建 Skill / CLAUDE.md / Hook 三项可被检测。
    未提供时这三项不进入盲区（无法判定，不报假阳性）。
    """
    tools = set(tool_session_counts or {})
    # 需要 customization_signals 才能判定的能力：无此数据时跳过，不报假阳性
    _NEEDS_CS = {"自建 Skill", "CLAUDE.md 定制", "Hook 自动化"}
    result = []
    for label, used, scene in _CAPABILITIES:
        if label in _NEEDS_CS and customization_signals is None:
            continue
        try:
            is_used = used(tools, customization_signals)
        except TypeError:
            # 旧版谓词只接受 tools 一个参数（如 SubAgent/Workflow 等）；
            # 新版谓词接受 (tools, customization_signals)。TypeError 说明
            # 是旧版签名，回退单参数调用。
            is_used = used(tools)
        if not is_used:
            result.append({"label": label, "scene": scene})
    return result
