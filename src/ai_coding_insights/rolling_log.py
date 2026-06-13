"""滚动日志：后台 auto-scan 的诊断辅助。

后台 hook 的 stderr 没有去处，静默失败会彻底不可见（曾因此漏掉一次 auto-scan
永久失效）。滚动日志把每次运行的关键节点与真实异常落盘，超出上限保留尾部，
且记日志本身的任何 IO 失败一律静默——诊断辅助绝不能反过来打断后台主流程。
"""
from pathlib import Path


def append_rolling_log(log_path: Path, line: str, max_lines: int = 500) -> None:
    """向 *log_path* 追加一行 *line*，总行数超过 *max_lines* 时丢弃最旧的、保留尾部。

    任何 OSError（父目录不存在、不可写等）静默吞掉，不抛、不影响调用方。
    """
    try:
        line = line.replace("\n", " ")   # 单条压成一行，max_lines 才数得准逻辑条数（非物理行）
        prev = (log_path.read_text(encoding="utf-8").splitlines()
                if log_path.is_file() else [])
        prev.append(line)
        log_path.write_text("\n".join(prev[-max_lines:]) + "\n", encoding="utf-8")
    except Exception:
        # 诊断辅助绝不能反过来打断后台主流程。除 OSError 外，read_text 遇非法 UTF-8 会抛
        # UnicodeDecodeError(属 ValueError 非 OSError)，而本函数恰在 auto-scan 自身的 except
        # 块内被调用——任何漏接异常都会逃逸成 SessionEnd hook 的 traceback。一律静默吞掉。
        pass
