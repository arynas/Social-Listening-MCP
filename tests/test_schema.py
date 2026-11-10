import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from slmcp.schema import WIB, Post, PostError, from_dict

GOOD = {"date": "2026-11-09T08:15:00+07:00", "text": "Berita contoh", "source": "Media Contoh",
        "platform": "news", "url": "https://example.com/a"}


def test_good_row_is_accepted_and_stored_in_utc():
    post = from_dict(GOOD)
    assert isinstance(post, Post)
    assert post.date == datetime(2026, 11, 9, 1, 15, tzinfo=UTC)
    assert post.date_wib.hour == 8
    assert post.date_wib.utcoffset().total_seconds() == 7 * 3600
    assert post.author is None and post.engagement is None and post.sentiment is None


def test_optional_fields_are_normalised():
    post = from_dict({**GOOD, "platform": "Twitter", "sentiment": "Negative", "engagement": "12",
                      "author": "  akun  "})
    assert (post.platform, post.sentiment, post.engagement, post.author) == ("twitter", "negative", 12,
                                                                             "akun")


def test_empty_optional_fields_become_none():
    post = from_dict({**GOOD, "sentiment": "", "engagement": "", "author": ""})
    assert post.sentiment is None and post.engagement is None and post.author is None


def test_utc_z_suffix_is_read():
    assert from_dict({**GOOD, "date": "2026-11-09T01:15:00Z"}).date_wib.hour == 8


@pytest.mark.parametrize("field", ["date", "text", "source", "platform", "url"])
def test_missing_required_field_is_refused(field):
    row = {k: v for k, v in GOOD.items() if k != field}
    with pytest.raises(PostError, match=field):
        from_dict(row)


@pytest.mark.parametrize("value", ["09/11/2026", "2026-13-01", "kemarin"])
def test_bad_date_format_is_refused(value):
    with pytest.raises(PostError, match="ISO 8601"):
        from_dict({**GOOD, "date": value})


def test_date_without_time_zone_is_refused_unless_assumed():
    row = {**GOOD, "date": "2026-11-09T08:15:00"}
    with pytest.raises(PostError, match="time zone"):
        from_dict(row)
    assert from_dict(row, assume_tz=WIB).date_wib.hour == 8


@pytest.mark.parametrize("row, match", [
    ({**GOOD, "platform": "myspace"}, "platform"),
    ({**GOOD, "url": "example.com/a"}, "url"),
    ({**GOOD, "sentiment": "angry"}, "sentiment"),
    ({**GOOD, "engagement": "banyak"}, "engagement"),
    ({**GOOD, "engagement": -1}, "engagement"),
    ({**GOOD, "text": "   "}, "text"),
    ({**GOOD, "source": "   "}, "source"),
])
def test_bad_values_are_refused_with_the_field_name(row, match):
    with pytest.raises(PostError, match=match):
        from_dict(row)


def test_to_dict_round_trip():
    post = from_dict({**GOOD, "sentiment": "positive", "engagement": 3})
    assert from_dict(post.to_dict()) == post


def test_example_rows_are_valid():
    path = Path(__file__).parent.parent / "examples" / "posts_sample.jsonl"
    posts = [from_dict(json.loads(line)) for line in path.read_text().splitlines() if line.strip()]
    assert len(posts) == 3
    assert {p.platform for p in posts} == {"news", "twitter", "youtube"}
