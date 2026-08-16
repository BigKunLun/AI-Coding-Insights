#!/usr/bin/env python3
"""生成 demo 报告所需的**全套假数据**（零真实数据）。

用途：给 README / 推广页放一份在线可点的样例报告。数据全部虚构，不含任何真实
项目名、路径、业务词与真人指标；渲染走的是仓库里真实的 `render-profile`，
所以样例报告的版式、口径、caveat 与真跑一次完全一致。

两套场景，各自展示一件事：
- `claude-code`：能力全集来源，展示完整四维画像 + 同比箭头（seed 了一份上次快照）。
- `codex`：能力子集来源，展示「未测量 ≠ 0」的渲染与「读数前提 · 来源口径」caveat 卡片。
  `unmeasured` 字段名不手写，从 `sources.unmeasured_fields(get_source("codex").capabilities)`
  取真值——手抄一份就等于给这条承重约束造了第二个真相源。

用法（仓库根）：
    uv run python docs/demo/生成demo数据.py
随后按 docs/demo/README.md 里的两条 `render-profile` 命令渲染 HTML。
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

这里 = Path(__file__).resolve().parent
sys.path.insert(0, str(这里.parents[1] / "src"))

from ai_coding_insights.sources import get_source, unmeasured_fields   # noqa: E402

数据目录 = 这里 / "data"

# demo 的「今天」固定成一个日期常量：报告里会出现窗口区间，每天重跑一次就变一次
# diff 会让样例报告的 git 历史噪声化。
今天 = date(2026, 8, 10)


# ---------------------------------------------------------------- 公共零件

def 日历(起始: date, 天数: int, 种子: list[int]) -> list[dict]:
    """每日活跃柱。种子是会话数序列（虚构），其余按固定倍率派生，保证可复现。"""
    out = []
    for i in range(天数):
        c = 种子[i % len(种子)]
        d = 起始 + timedelta(days=i)
        out.append({
            "date": d.isoformat(),
            "session_count": c,
            "human_input_count": c * 9 + (i % 5),
            "commit_count": max(0, c - 1),
            "landed_count": max(0, c - 2),
            "edit_count": c * 7,
            "token_total": c * 41000,
        })
    return out


def 写(路径: Path, obj) -> Path:
    路径.parent.mkdir(parents=True, exist_ok=True)
    路径.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return 路径


# ---------------------------------------------------------------- 场景一：Claude Code

CC_窗口 = {
    "status": "incremental",
    "lookback_days": 31,
    "since_date": (今天 - timedelta(days=31)).isoformat(),
    "until_date": 今天.isoformat(),
    "last_check_date": (今天 - timedelta(days=31)).isoformat(),
    "days_since_last": 31,
    "message": None,
    "data_start": (今天 - timedelta(days=31)).isoformat(),
    "truncated": False,
    "mode": "all",
    "source": "claude-code",
}

CC_工具 = {
    "Read": 66, "Edit": 61, "Bash": 58, "Grep": 47, "Glob": 39, "Write": 33,
    "TodoWrite": 30, "Agent": 21, "MultiEdit": 18, "Skill": 15,
    "BashOutput": 12, "mcp__docsearch__lookup": 12, "NotebookEdit": 6,
    "EnterPlanMode": 9, "ExitPlanMode": 9, "Workflow": 3,
}

CC_指标 = {
    "session_count": 68,
    "human_input_count": 640,
    "active_days": 16,
    "avg_turns": 9.4,
    "tool_breadth": len(CC_工具),
    "tool_session_counts": CC_工具,
    "subagent_sessions": 21,
    "workflow_sessions": 3,
    "mcp_sessions": 12,
    "model_counts": {"model-a-large": 52, "model-a-fast": 31, "model-a-mini": 9},
    "commit_count": 46,
    "landed_count": 38,
    "edit_count": 512,
    "duration_median_min": 24.5,
    "anchor_counts": {"override": 74, "error": 118, "code": 260, "link": 41},
    "token_usage": {
        "model-a-large": {"input": 1_180_000, "output": 214_000,
                          "cache_read": 8_640_000, "cache_creation": 930_000},
        "model-a-fast": {"input": 410_000, "output": 86_000,
                         "cache_read": 2_100_000, "cache_creation": 240_000},
        "model-a-mini": {"input": 96_000, "output": 21_000,
                         "cache_read": 380_000, "cache_creation": 44_000},
    },
    "token_total": 14_341_000,
    "trend": {
        "first_half": {"sessions": 31, "commits": 19, "landed_ratio": 0.58,
                       "override": 41, "error": 71, "short_ratio": 0.24},
        "second_half": {"sessions": 37, "commits": 27, "landed_ratio": 0.70,
                        "override": 33, "error": 47, "short_ratio": 0.16},
    },
    "short_turn_count": 96,
    "option_pick_count": 26,
    "decision_point_count": 666,
    "git_landed_count": 41,
    "git_commit_total": 63,
    "landed_ratio": 41 / 63,
    "dropped_count": 8,
    "friction_stats": {
        "error_top_sessions": [{"rank": 1, "error_turns": 14}, {"rank": 2, "error_turns": 11}],
        "override_top_sessions": [{"rank": 1, "override_turns": 9}],
        "error_session_share": 0.44,
        "override_session_share": 0.29,
    },
    "plan_mode_sessions": 9,
    "plan_mode_count": 14,
    "concurrent_days": 6,
    "claude_md_sessions": 7,
    "max_concurrent_sessions": 3,
    "skill_counts": {"skill-甲": 9, "skill-乙": 5, "skill-丙": 3},
    "skill_total_counts": {"skill-甲": 22, "skill-乙": 8, "skill-丙": 4},
    "mcp_server_counts": {"docsearch": 12},
    "daily": 日历(今天 - timedelta(days=30), 31,
                 [0, 3, 5, 0, 2, 7, 4, 0, 6, 9, 3, 0, 0, 5, 8, 2]),
    "custom_skill_count": 4,
    "duration_p90_min": 71.0,
    "turn_p90": 26,
    "thinking_block_count": 318,
    "thinking_sessions": 40,
    "background_task_count": 11,
    "background_sessions": 6,
    "max_parallel_agents": 4,
    "parallel_agent_turns": 9,
    "source": "claude-code",
    "unmeasured": [],
    # 能力集也要写：缺这个键时渲染层只能按来源名回填，虽有兜底但 demo 该走正路
    "capabilities": sorted(get_source("claude-code").capabilities),
    "customization_signals": {
        "has_custom_skills": True,
        "custom_skill_count": 4,
        "claude_md_sessions": 7,
        "has_hooks": False,
        "hook_events": [],
        "hooks_measured": True,
    },
    "parse_health": {
        "cc_version_span": {"min": "9.9.101", "max": "9.9.140", "distinct": 7},
        "unknown_record_types": ["demo-record-type"],
        "drift_flags": [
            {"signal": "mcp", "kind": "drop", "older_rate": 0.41, "newer_rate": 0.0},
        ],
    },
}

# 上一次快照（虚构）：让 demo 展示同比箭头而不是「首次基线」。
CC_上次快照 = {
    "generated_at": (今天 - timedelta(days=31)).isoformat() + "T09:12:00+00:00",
    "window": {"lookback_days": 30},
    "metrics": {
        "landed_ratio": 0.52, "commit_count": 39, "landed_count": 30,
        "git_landed_count": 31, "git_commit_total": 60, "dropped_count": 9,
        "edit_count": 447, "session_count": 61, "human_input_count": 583,
        "tool_breadth": 14, "active_days": 15, "token_total": 12_050_000,
        "subagent_sessions": 14, "workflow_sessions": 1, "mcp_sessions": 17,
        "duration_median_min": 22.0, "decision_point_count": 604,
        "plan_mode_sessions": 5, "turn_p90": 23, "custom_skill_count": 2,
        "background_sessions": 3, "max_parallel_agents": 3,
    },
    "posture_distribution": {"L1": 0.27, "L2": 0.38, "L3": 0.26, "L4": 0.09},
    "posture_rubric": 3,
    "outcome": {"landed": 31, "total": 40},
}

# extractor 的逐 turn 语义分档计数（阶段一产物）。四档比例决定姿态健康带落点。
CC_观测 = {"sessions": [
    {"session_id": "demo-cc-0001", "posture_counts": {"L1": 62, "L2": 104, "L3": 91, "L4": 33}},
    {"session_id": "demo-cc-0002", "posture_counts": {"L1": 58, "L2": 106, "L3": 89, "L4": 37}},
]}

CC_画像 = {
    "breadth": {
        "headline": "工具面铺到 16 种，子代理与计划模式已成常规动作",
        "points": [
            "21 场会话派过子代理，其中 9 轮是单轮并发多个（真并行，不是顺序逐个派）",
            "计划模式 9 场：动手前先出方案再放行，集中在改动面较大的那几次",
            "后台委托 11 次，长跑任务不再堵住主线程",
        ],
        "metrics": [
            {"label": "工具广度", "value": "16 种"},
            {"label": "真并行峰值", "value": "4 个/轮"},
            {"label": "自建扩展", "value": "4 个"},
        ],
        "tools": ["Agent", "Skill", "EnterPlanMode", "Workflow"],
    },
    "depth": {
        "headline": "P90 轮次 26，长会话里以「贴报错 + 追加约束」推进，不是一锤子买卖",
        "points": [
            "报错锚点 118 次、纠偏锚点 74 次，纠错基本发生在同一场会话内闭环",
            "窗口后半段每会话报错密度从 2.3 降到 1.3，同一类问题重复出现的次数明显减少",
            "极短输入占比 15%：跟随式「继续 / 好的」不多，多数输入带具体约束",
        ],
        "metrics": [
            {"label": "P90 轮次", "value": "26"},
            {"label": "时长 P90", "value": "71 min"},
            {"label": "深度推理块", "value": "318"},
        ],
    },
    "outcome": {
        "headline": "落地率 65%：会话里改过的文件，多数最终进了提交",
        "points": [
            "窗口内本人提交 63 次，其中 41 次的改动文件与会话编辑集有交集",
            "会话内观测到的提交 46 次，有 8 次事后不在 HEAD 历史里（被回退或重做）",
        ],
        "metrics": [
            {"label": "落地提交", "value": "41"},
            {"label": "提交总数", "value": "63"},
        ],
        "landed": 41,
        "total": 49,
    },
    "frictions": [
        {
            "observation": "有 3 场会话在同一处报错上反复 5 轮以上，每轮只贴新报错、"
                           "没有补充已经排除掉的可能性，AI 重复给出已被否掉的方案。",
            "suggestion": "第 3 轮还没过就换姿势：一次性把「已试过什么、结果如何、"
                          "现在的假设是什么」摊开写成 5 行，再让 AI 只在剩余假设里选。",
            "pointers": ["/home/demo/.aci-demo/sessions/demo-cc-0001.jsonl#turn-7c2d41",
                         "/home/demo/.aci-demo/sessions/demo-cc-0002.jsonl#turn-90ab13"],
        },
        {
            "observation": "子代理多数是顺序逐个派（真并行轮次 9 / 子代理会话 21），"
                           "几处彼此独立的排查被排成了串行。",
            "suggestion": "确认互不依赖时，在同一条消息里一次派完；"
                          "把「先查 A 再查 B」改成「同时查 A 和 B，回来我合并」。",
            "pointers": ["/home/demo/.aci-demo/sessions/demo-cc-0002.jsonl#turn-4f81c0"],
        },
        {
            "observation": "会话结束后仍有 8 次提交被回退，集中在没有先跑测试就提交的那几场。",
            "suggestion": "把「跑一遍测试再提交」写进项目约定文件，"
                          "让它成为默认动作而不是每次口头交代。",
            "pointers": [],
        },
    ],
    "evidence": [
        {"pointer": "/home/demo/.aci-demo/sessions/demo-cc-0001.jsonl#turn-11d9e2",
         "behavior": "主动给出接口边界与错误分支的期望行为，再让 AI 补实现"},
        {"pointer": "/home/demo/.aci-demo/sessions/demo-cc-0001.jsonl#turn-55c7aa",
         "behavior": "对 AI 给的方案指出具体技术缺陷并要求换实现路径"},
        {"pointer": "/home/demo/.aci-demo/sessions/demo-cc-0002.jsonl#turn-4f81c0",
         "behavior": "同一轮里并发派出 4 个子代理分头排查，回来自行合并结论"},
    ],
    "highlights": [
        {"pointer": "/home/demo/.aci-demo/sessions/demo-cc-0002.jsonl#turn-33be07",
         "behavior": "先写失败用例复现，再让 AI 在用例约束下改实现——"
                     "整场会话由测试而不是描述来定义「做到位」"},
    ],
}


# ---------------------------------------------------------------- 场景二：Codex CLI

CODEX_未测量 = list(unmeasured_fields(get_source("codex").capabilities))

CODEX_窗口 = {
    "status": "first",
    "lookback_days": 30,
    "since_date": (今天 - timedelta(days=30)).isoformat(),
    "until_date": 今天.isoformat(),
    "last_check_date": None,
    "days_since_last": None,
    "message": None,
    "data_start": (今天 - timedelta(days=24)).isoformat(),
    "truncated": False,
    "mode": "all",
    "source": "codex",
}

CODEX_工具 = {
    "shell": 44, "apply_patch": 38, "read_file": 41, "exec_command": 22,
    "list_dir": 19, "grep": 17, "update_plan": 12, "web_search": 6, "view_image": 3,
}

CODEX_指标 = {
    "session_count": 47,
    "human_input_count": 470,
    "active_days": 11,
    "avg_turns": 10.0,
    "tool_breadth": len(CODEX_工具),
    "tool_session_counts": CODEX_工具,
    # 以下整族为「该来源测不到」，值恒 0/空；渲染层据 unmeasured 打「未测量」而非 0
    "subagent_sessions": 0,
    "workflow_sessions": 0,
    "mcp_sessions": 0,
    "model_counts": {"model-b-high": 39, "model-b-mini": 12},
    "commit_count": 0,
    "landed_count": 0,
    "edit_count": 388,
    "duration_median_min": 19.0,
    "anchor_counts": {"override": 38, "error": 96, "code": 173, "link": 22},
    "token_usage": {
        "model-b-high": {"input": 860_000, "output": 171_000,
                         "cache_read": 4_300_000, "cache_creation": 0},
        "model-b-mini": {"input": 190_000, "output": 38_000,
                         "cache_read": 640_000, "cache_creation": 0},
    },
    "token_total": 6_199_000,
    "trend": {
        "first_half": {"sessions": 22, "commits": 0, "landed_ratio": None,
                       "override": 22, "error": 58, "short_ratio": 0.31},
        "second_half": {"sessions": 25, "commits": 0, "landed_ratio": None,
                        "override": 16, "error": 38, "short_ratio": 0.27},
    },
    "short_turn_count": 136,
    "option_pick_count": 0,
    "decision_point_count": 470,
    "git_landed_count": 19,
    "git_commit_total": 34,
    "landed_ratio": 19 / 34,
    "dropped_count": 0,
    "friction_stats": {
        "error_top_sessions": [{"rank": 1, "error_turns": 12}],
        "override_top_sessions": [{"rank": 1, "override_turns": 6}],
        "error_session_share": 0.51,
        "override_session_share": 0.23,
    },
    "plan_mode_sessions": 0,
    "plan_mode_count": 0,
    "concurrent_days": 2,
    "claude_md_sessions": 3,
    "max_concurrent_sessions": 2,
    "skill_counts": {},
    "skill_total_counts": {},
    "mcp_server_counts": {},
    "daily": 日历(今天 - timedelta(days=23), 24, [0, 2, 4, 3, 0, 5, 1, 0]),
    "custom_skill_count": 0,
    "duration_p90_min": 52.0,
    "turn_p90": 18,
    "thinking_block_count": 204,
    "thinking_sessions": 29,
    "background_task_count": 0,
    "background_sessions": 0,
    "max_parallel_agents": 0,
    "parallel_agent_turns": 0,
    "source": "codex",
    "unmeasured": CODEX_未测量,
    # 少了这个键，能力盲区会退到「全集」判——给 Codex 用户报一串它压根没有的
    # 能力（「你没用过 Workflow」）。渲染层已按来源名兜底，这里仍照真实链路写全。
    "capabilities": sorted(get_source("codex").capabilities),
    "customization_signals": {
        "has_custom_skills": False,
        "custom_skill_count": 0,
        "claude_md_sessions": 3,
        "has_hooks": None,
        "hook_events": [],
        "hooks_measured": False,
    },
    "parse_health": {
        "cc_version_span": {"min": "0.60.1", "max": "0.62.4", "distinct": 4},
        "unknown_record_types": [],
        "drift_flags": [],
    },
}

CODEX_观测 = {"sessions": [
    {"session_id": "demo-cx-0001", "posture_counts": {"L1": 108, "L2": 131, "L3": 34, "L4": 9}},
    {"session_id": "demo-cx-0002", "posture_counts": {"L1": 102, "L2": 129, "L3": 36, "L4": 11}},
]}

CODEX_画像 = {
    "breadth": {
        "headline": "工具面 9 种，集中在读改跑三件事上，编排类动作基本没出现",
        "points": [
            "改动一律走结构化补丁（38 场），不是整文件覆写",
            "外部检索只用了 6 场，多数事实判断仍靠模型记忆",
        ],
        "metrics": [
            {"label": "工具广度", "value": "9 种"},
            {"label": "编辑数", "value": "388"},
        ],
    },
    "depth": {
        "headline": "P90 轮次 18，报错闭环较快，但极短输入占比偏高",
        "points": [
            "极短输入 136 条（占 29%）：大量「继续 / 可以 / 好的」式放行",
            "报错锚点 96 次，其中过半集中在 5 场会话里",
        ],
        "metrics": [
            {"label": "P90 轮次", "value": "18"},
            {"label": "时长 P90", "value": "52 min"},
        ],
    },
    "outcome": {
        "headline": "落地率 56%：git 主锚口径，独立于会话记录可复算",
        "points": [
            "窗口内本人提交 34 次，19 次的改动文件与会话编辑集有交集",
            "会话内提交回执这一项本来源取不到，落地口径只靠 git 主锚",
        ],
        "metrics": [
            {"label": "落地提交", "value": "19"},
            {"label": "提交总数", "value": "34"},
        ],
        "landed": 19,
        "total": 34,
    },
    "frictions": [
        {
            "observation": "近三成输入是纯放行（「继续」「可以」），"
                           "方案定型阶段没有补充过约束。",
            "suggestion": "放行前加一句「按什么标准算做完」——"
                          "哪怕只写一条验收条件，也把选择题变成带约束的委托。",
            "pointers": ["/home/demo/.aci-demo/sessions/demo-cx-0001.jsonl#turn-2a77f5"],
        },
        {
            "observation": "涉及外部库行为的判断多数没有检索，直接采信了模型给的用法。",
            "suggestion": "凡是「某个库怎么用」的问题，先让它检索再回答；"
                          "把这条写进项目约定文件，省得每次口头交代。",
            "pointers": [],
        },
    ],
    "evidence": [
        {"pointer": "/home/demo/.aci-demo/sessions/demo-cx-0001.jsonl#turn-2a77f5",
         "behavior": "对 AI 的实现方案只回「可以」，未补充约束即放行"},
        {"pointer": "/home/demo/.aci-demo/sessions/demo-cx-0002.jsonl#turn-6b0e28",
         "behavior": "贴出完整报错并指出自己已经排除的两种可能，再让 AI 继续"},
    ],
    "highlights": [
        {"pointer": "/home/demo/.aci-demo/sessions/demo-cx-0002.jsonl#turn-6b0e28",
         "behavior": "把「已排除的可能性」显式写出来，避免 AI 重复给已被否掉的方案"},
    ],
}


# ---------------------------------------------------------------- 落盘

def main() -> int:
    数据目录.mkdir(parents=True, exist_ok=True)
    # 配置是 toml 纯文本，不走 写()（那是 JSON 落盘）
    (数据目录 / "config.toml").write_text(
        '# demo 渲染用的最小配置：只为让 render-profile 不去读本机个人配置。\n'
        'mode = "all"\n'
        'business_terms = []\n', encoding="utf-8")

    产物 = [
        写(数据目录 / "claude-code" / "_window.json", CC_窗口),
        写(数据目录 / "claude-code" / "_aggregate.json", CC_指标),
        写(数据目录 / "claude-code" / "obs-01.json", CC_观测),
        写(数据目录 / "claude-code" / "profile.json", CC_画像),
        写(数据目录 / "claude-code" / "snapshots"
          / f"{CC_上次快照['generated_at'][:10]}.json", CC_上次快照),
        写(数据目录 / "codex" / "_window.json", CODEX_窗口),
        写(数据目录 / "codex" / "_aggregate.json", CODEX_指标),
        写(数据目录 / "codex" / "obs-01.json", CODEX_观测),
        写(数据目录 / "codex" / "profile.json", CODEX_画像),
    ]
    for p in 产物:
        print(p)
    print(f"codex 未测量字段（取自 sources.unmeasured_fields）：{len(CODEX_未测量)} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
