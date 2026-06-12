from datetime import date

from ai_coding_insights.window import (
    WINDOW_CAP_DAYS,
    WINDOW_FLOOR_DAYS,
    WindowDecision,
    decide_window,
)


def test_first_run_uses_cap():
    d = decide_window(None, date(2026, 6, 10))
    assert d.status == "first"
    assert d.lookback_days == 45
    assert d.since_date == date(2026, 4, 26)
    assert d.until_date == date(2026, 6, 10)
    assert d.last_check_date is None
    assert d.days_since_last is None
    assert d.message is None


def test_too_soon_below_floor():
    d = decide_window(date(2026, 6, 5), date(2026, 6, 10))
    assert d.status == "too_soon"
    assert d.lookback_days == 0
    assert d.since_date is None
    assert d.until_date == date(2026, 6, 10)
    assert d.last_check_date == date(2026, 6, 5)
    assert d.days_since_last == 5
    assert d.message is not None
    assert "5" in d.message


def test_ok_within_window():
    d = decide_window(date(2026, 5, 1), date(2026, 6, 10))
    assert d.status == "ok"
    assert d.lookback_days == 40
    assert d.since_date == date(2026, 5, 1)
    assert d.until_date == date(2026, 6, 10)
    assert d.days_since_last == 40
    assert d.message is None


def test_ok_capped_above_cap():
    d = decide_window(date(2026, 3, 1), date(2026, 6, 10))
    assert d.status == "ok"
    assert d.lookback_days == 45
    assert d.since_date == date(2026, 4, 26)
    assert d.days_since_last == 101


def test_boundary_floor_is_ok():
    # N == 30 exactly -> ok
    d = decide_window(date(2026, 5, 11), date(2026, 6, 10))
    assert d.days_since_last == 30
    assert d.status == "ok"
    assert d.lookback_days == 30
    assert d.since_date == date(2026, 5, 11)


def test_boundary_cap_is_ok():
    # N == 45 exactly -> ok, lookback == 45
    d = decide_window(date(2026, 4, 26), date(2026, 6, 10))
    assert d.days_since_last == 45
    assert d.status == "ok"
    assert d.lookback_days == 45
    assert d.since_date == date(2026, 4, 26)


def test_constants():
    assert WINDOW_FLOOR_DAYS == 30
    assert WINDOW_CAP_DAYS == 45


def test_to_dict_first():
    d = decide_window(None, date(2026, 6, 10))
    out = d.to_dict()
    assert out == {
        "status": "first",
        "lookback_days": 45,
        "since_date": "2026-04-26",
        "until_date": "2026-06-10",
        "last_check_date": None,
        "days_since_last": None,
        "message": None,
    }


def test_to_dict_too_soon():
    d = decide_window(date(2026, 6, 5), date(2026, 6, 10))
    out = d.to_dict()
    assert out["status"] == "too_soon"
    assert out["since_date"] is None
    assert out["until_date"] == "2026-06-10"
    assert out["last_check_date"] == "2026-06-05"
    assert out["days_since_last"] == 5
    assert isinstance(out["message"], str)


def test_to_dict_ok_dates_are_iso_strings():
    d = decide_window(date(2026, 5, 1), date(2026, 6, 10))
    out = d.to_dict()
    assert out["since_date"] == "2026-05-01"
    assert out["until_date"] == "2026-06-10"
    assert out["last_check_date"] == "2026-05-01"
    assert out["message"] is None


def test_windowdecision_is_dataclass_instance():
    d = decide_window(date(2026, 5, 1), date(2026, 6, 10))
    assert isinstance(d, WindowDecision)
