from datetime import datetime, timezone

from backend.utils.clock import utcnow


def test_utcnow_returns_naive_datetime():
    value = utcnow()

    assert value.tzinfo is None


def test_utcnow_is_close_to_current_utc_time():
    before = datetime.now(timezone.utc).replace(tzinfo=None)
    value = utcnow()
    after = datetime.now(timezone.utc).replace(tzinfo=None)

    assert before <= value <= after
