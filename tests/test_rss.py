from datetime import UTC, date, datetime
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

import pytest
from adapter_contract import AdapterContract

from slmcp.adapters.rss import FeedError, RssAdapter, parse_date, read_feed, strip_html

FIXTURES = Path(__file__).parent / "fixtures"


def rss_from_posts(posts, title="Sumber Contoh"):
    items = "".join(
        f"<item><title>{escape(p.text)}</title><link>{escape(p.url)}</link>"
        f"<pubDate>{format_datetime(p.date)}</pubDate></item>" for p in posts)
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>{title}</title>{items}</channel></rss>'


class TestRssAdapter(AdapterContract):
    def make(self, posts):
        return RssAdapter(rss_from_posts(posts))


def test_rss_fixture_maps_fields_strips_html_and_skips_bad_items():
    adapter = RssAdapter(FIXTURES / "feed_rss.xml")
    assert len(adapter.posts) == 2
    first = adapter.posts[0]
    assert first.text == "Jalur sepeda baru dibuka. Pemerintah kota membuka jalur sepeda sepanjang 12 km."
    assert (first.source, first.platform, first.url) == ("Kabar Kota Contoh", "news",
                                                          "https://example.com/berita/1")
    assert adapter.posts[1].text == "Harga cabai naik lagi. Pedagang & pembeli mengeluh di pasar pagi."
    assert adapter.skipped == 3
    assert [e.split(":")[0] for e in adapter.errors] == [
        "feed 1 (Kabar Kota Contoh) item 3", "feed 1 (Kabar Kota Contoh) item 4",
        "feed 1 (Kabar Kota Contoh) item 5"]


def test_rfc822_dates_with_offset_and_gmt():
    adapter = RssAdapter(FIXTURES / "feed_rss.xml")
    assert adapter.posts[0].date == datetime(2026, 11, 9, 1, 15, tzinfo=UTC)
    # 23:30 GMT on 8 Nov is 06:30 WIB on 9 Nov
    assert adapter.posts[1].date_wib.date() == date(2026, 11, 9)


def test_atom_fixture():
    adapter = RssAdapter(FIXTURES / "feed_atom.xml")
    assert adapter.skipped == 0
    a, b = adapter.posts
    assert a.text == "Saluran air dibersihkan. Warga bergotong royong membersihkan saluran."
    assert a.date == datetime(2026, 11, 9, 2, 0, tzinfo=UTC)  # published wins over updated
    assert (a.source, a.url) == ("Warta Contoh", "https://example.com/warta/10")
    assert b.text == "Relawan membagikan makanan" and b.url == "https://example.com/warta/11"


def test_several_feeds_in_one_adapter():
    adapter = RssAdapter([FIXTURES / "feed_rss.xml", FIXTURES / "feed_atom.xml"])
    assert len(adapter.posts) == 4
    assert {p.source for p in adapter.posts} == {"Kabar Kota Contoh", "Warta Contoh"}
    hits = adapter.search("saluran", "2026-11-09", "2026-11-09", today=date(2026, 11, 10))
    assert [p.url for p in hits] == ["https://example.com/warta/10"]


@pytest.mark.parametrize("value, expected", [
    ("Mon, 09 Nov 2026 08:15:00 +0700", datetime(2026, 11, 9, 1, 15, tzinfo=UTC)),
    ("Mon, 09 Nov 2026 01:15:00 GMT", datetime(2026, 11, 9, 1, 15, tzinfo=UTC)),
    ("Mon, 09 Nov 2026 01:15:00 -0000", datetime(2026, 11, 9, 1, 15, tzinfo=UTC)),
    ("2026-11-09T08:15:00+07:00", datetime(2026, 11, 9, 1, 15, tzinfo=UTC)),
    ("2026-11-09T01:15:00Z", datetime(2026, 11, 9, 1, 15, tzinfo=UTC)),
    ("kemarin", None),
    ("", None),
])
def test_parse_date(value, expected):
    assert parse_date(value) == expected


@pytest.mark.parametrize("value, expected", [
    ("<p>Satu <b>dua</b></p><br/>tiga", "Satu dua tiga"),
    ("Pedagang &amp; pembeli", "Pedagang & pembeli"),
    ("  banyak   spasi \n baris ", "banyak spasi baris"),
    (None, ""),
])
def test_strip_html(value, expected):
    assert strip_html(value) == expected


@pytest.mark.parametrize("content, match", [
    ('<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><rss><channel>'
     "<title>&x;</title></channel></rss>", "DOCTYPE"),
    ("<rss><channel><title>broken", "not valid XML"),
    ("<html><body>not a feed</body></html>", "not an RSS or Atom"),
])
def test_unsafe_or_wrong_feeds_are_refused(content, match):
    with pytest.raises(FeedError, match=match):
        read_feed(content)


def test_empty_channel_gives_no_posts():
    adapter = RssAdapter("<rss version='2.0'><channel><title>Kosong</title></channel></rss>")
    assert adapter.posts == [] and adapter.skipped == 0
