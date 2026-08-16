"""Claude Code 来源适配（`~/.claude/projects/<项目槽>/<会话>.jsonl`）。

只是把既有的 `jsonl_parser.parse_session` 与「文件头找最早时间戳」包成
`sources.Source` 要的两个函数。解析逻辑本身一行没动——CC 是参照来源，
它的口径就是全项目指标的定义基准。

深度固定为 `*/*.jsonl`（top-level only）：CC 把每个项目放在 projects 下一层的
槽目录里，再深就是别的东西了，递归会把无关 jsonl 卷进来。
"""
from pathlib import Path

from .jsonl_parser import parse_session
from .models import ParsedSession
from .sources import CLAUDE_CODE, file_scanner, head_earliest_ts
from .timeutil import parse_timestamp

_PATTERN = "*/*.jsonl"


def parse(path) -> ParsedSession:
    """解析单个 CC transcript，并打上来源标记。"""
    session = parse_session(path)
    session.source = CLAUDE_CODE
    return session


iter_sessions = file_scanner(_PATTERN, parse)

earliest_ts = head_earliest_ts(_PATTERN, lambda rec: parse_timestamp(rec.get("timestamp")))


def session_files(root) -> list[Path]:
    """该来源下的会话文件列表（init 向导按 cwd 归组时用，不做全量解析）。"""
    return sorted(Path(root).glob(_PATTERN))
