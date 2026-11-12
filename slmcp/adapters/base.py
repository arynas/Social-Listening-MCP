"""The adapter interface. Every data source implements search(keyword, start, end) -> list[Post].

The contract (the same for every adapter, enforced by check_query and filter_posts):
- keyword: case-insensitive phrase match on the post text. A multi-word keyword must appear as that
  phrase ("jalur sepeda" matches "Jalur Sepeda baru", not "sepeda di jalur"). Spaces in the keyword are
  collapsed to one.
- start, end: dates (date objects or "YYYY-MM-DD"), both inclusive, read as days in Asia/Jakarta (WIB).
  A post at 23:30 WIB on the end day is included; one at 00:10 WIB the next day is not.
- Results are sorted by date, oldest first; posts with the same time keep the source's order.
- No match returns an empty list, not an error.
- Refused with QueryError: an empty keyword, start after end, a date that isn't YYYY-MM-DD, a range
  longer than max_days (366 by default), and an end date after today (WIB).
"""
from abc import ABC, abstractmethod
from datetime import UTC, date, datetime, time, timedelta

from slmcp.schema import WIB, Post

MAX_RANGE_DAYS = 366


class QueryError(ValueError):
    """A search request that can't be run. The message says what to change."""


def parse_day(value, name):
    """A date or an ISO date string (YYYY-MM-DD) -> date."""
    if isinstance(value, datetime):
        raise QueryError(f"{name}: give a date (YYYY-MM-DD), not a date and time")
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        raise QueryError(f"{name}: {value!r} is not a date; use YYYY-MM-DD, e.g. 2026-11-09") from None


def check_query(keyword, start, end, today=None, max_days=MAX_RANGE_DAYS):
    """Validate a search request. Returns (keyword, start, end) cleaned, or raises QueryError.
    today defaults to the current day in WIB."""
    keyword = " ".join(str(keyword or "").split())
    if not keyword:
        raise QueryError("keyword: empty; give a word or name to search for")
    start, end = parse_day(start, "start"), parse_day(end, "end")
    today = today or datetime.now(WIB).date()
    if start > end:
        raise QueryError(f"start {start} is after end {end}; swap them")
    if end > today:
        raise QueryError(f"end {end} is in the future (today is {today}); there is no data for future dates")
    days = (end - start).days + 1
    if days > max_days:
        raise QueryError(f"range is {days} days; the maximum is {max_days}. Split it into smaller ranges")
    return keyword, start, end


def utc_window(start, end):
    """The WIB days start..end (inclusive) as a UTC window [from, to): to is exclusive.
    Adapters that query a database can use it directly in their WHERE clause."""
    begin = datetime.combine(start, time.min, WIB)
    stop = datetime.combine(end + timedelta(days=1), time.min, WIB)
    return begin.astimezone(UTC), stop.astimezone(UTC)


def filter_posts(posts, keyword, start, end):
    """The shared rule: phrase match in the text (case-insensitive) within the WIB days start..end,
    sorted by date (stable on ties). Arguments must already be checked by check_query."""
    begin, stop = utc_window(start, end)
    phrase = keyword.lower()
    hits = [p for p in posts if begin <= p.date < stop and phrase in " ".join(p.text.lower().split())]
    return sorted(hits, key=lambda p: p.date)


class Adapter(ABC):
    """Base class for data sources. Subclasses implement _search; search() checks the request first
    and sorts the result."""

    max_days = MAX_RANGE_DAYS

    def search(self, keyword, start, end, today=None) -> list[Post]:
        keyword, start, end = check_query(keyword, start, end, today, self.max_days)
        return sorted(self._search(keyword, start, end), key=lambda p: p.date)

    @abstractmethod
    def _search(self, keyword, start, end):
        """Return the matching posts (see the module docstring). The arguments are already checked;
        simple adapters can return filter_posts(all_posts, keyword, start, end)."""
