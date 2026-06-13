"""滚动日志（auto-scan 后台诊断辅助）：追加 + 尾部封顶 + 失败静默。"""
from ai_coding_insights.rolling_log import append_rolling_log


def test_appends_lines_in_order(tmp_path):
    log = tmp_path / "auto-scan.log"
    append_rolling_log(log, "2026-06-13T00:00:00Z start")
    append_rolling_log(log, "2026-06-13T00:00:01Z ok sessions=3")
    assert log.read_text(encoding="utf-8").splitlines() == [
        "2026-06-13T00:00:00Z start",
        "2026-06-13T00:00:01Z ok sessions=3",
    ]


def test_caps_to_max_lines_keeping_tail(tmp_path):
    log = tmp_path / "auto-scan.log"
    for i in range(10):
        append_rolling_log(log, f"line {i}", max_lines=3)
    # 只保留最近 3 行（滚动），旧行被丢弃
    assert log.read_text(encoding="utf-8").splitlines() == ["line 7", "line 8", "line 9"]


def test_io_error_is_silent(tmp_path):
    # 父路径是一个文件 → 写入必然失败；但记日志失败绝不能抛（否则反而打断后台主流程）
    parent_is_file = tmp_path / "f"
    parent_is_file.write_text("x", encoding="utf-8")
    bad = parent_is_file / "sub" / "auto.log"
    append_rolling_log(bad, "whatever")   # 不得抛异常
