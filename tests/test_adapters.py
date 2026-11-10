from datetime import UTC, date, datetime

import pytest
from adapter_contract import AdapterContract

from slmcp.adapters.base import QueryError, check_query, utc_window
from slmcp.adapters.memory import MemoryAdapter


class TestMemoryAdapter(AdapterContract):
    def make(self, posts):
        return MemoryAdapter(posts)


def test_check_query_accepts_date_objects_and_strips_keyword():
    assert check_query("  sepeda ", date(2026, 11, 1), date(2026, 11, 9), today=date(2026, 11, 10)) == (
        "sepeda", date(2026, 11, 1), date(2026, 11, 9))


def test_check_query_refuses_datetime():
    with pytest.raises(QueryError, match="not a date and time"):
        check_query("sepeda", datetime(2026, 11, 1, 8), "2026-11-09", today=date(2026, 11, 10))


def test_utc_window_covers_the_wib_days():
    begin, stop = utc_window(date(2026, 11, 9), date(2026, 11, 9))
    assert begin == datetime(2026, 11, 8, 17, 0, tzinfo=UTC)
    assert stop == datetime(2026, 11, 9, 17, 0, tzinfo=UTC)


def test_max_days_is_configurable():
    adapter = MemoryAdapter([], max_days=7)
    assert adapter.search("x", "2026-11-03", "2026-11-09", today=date(2026, 11, 10)) == []
    with pytest.raises(QueryError, match="maximum is 7"):
        adapter.search("x", "2026-11-02", "2026-11-09", today=date(2026, 11, 10))
