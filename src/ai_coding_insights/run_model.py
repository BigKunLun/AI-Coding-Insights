"""运行模型的确定性识别（决策为纯函数，文件 IO 收在薄壳里）。

「本报告是什么模型跑出来的」不能让 LLM 自报——模型 ID 自报实测会编造。
可信来源：Claude Code 在 Bash 环境注入 CLAUDE_CODE_SESSION_ID，对应
~/.claude/projects/<项目槽>/<session-id>.jsonl，其中每条 assistant 行的
message.model 字段由 CC 写入，LLM 全程无法插手。任一环节拿不到就返回
None（宁缺勿假），报告端整段省略。
"""
import re
from pathlib import Path

_ASSISTANT_LINE = re.compile(r'"type"\s*:\s*"assistant"')
_MODEL_FIELD = re.compile(r'"model"\s*:\s*"([^"]+)"')

# transcript 可能数十 MB；model 字段每条 assistant 行都有，读尾部即足够。
_TAIL_BYTES = 256 * 1024


def extract_run_model(lines) -> str | None:
    """取最后一条 assistant 行的 model 字段值。

    只认 assistant 行——子 agent 派发参数等其他行也带 model 值，不可信；
    跳过 <synthetic>（CC 对失败 turn 写入的占位，不是真实模型）。
    """
    model = None
    for line in lines:
        if not _ASSISTANT_LINE.search(line):
            continue
        m = _MODEL_FIELD.search(line)
        if m and m.group(1) != "<synthetic>":
            model = m.group(1)
    return model


def detect_run_model(session_id, projects_dir: Path) -> str | None:
    """按 session id 在 projects_dir 下定位会话 transcript 并提取模型名。"""
    if not session_id:
        return None
    try:
        for p in Path(projects_dir).glob(f"*/{session_id}.jsonl"):
            with p.open("rb") as f:
                f.seek(max(0, p.stat().st_size - _TAIL_BYTES))
                tail = f.read().decode("utf-8", errors="replace")
            return extract_run_model(tail.splitlines())
    except OSError:
        pass
    return None
