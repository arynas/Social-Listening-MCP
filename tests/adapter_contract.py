"""The adapter contract: one set of checks that every adapter must pass.

To test a new adapter, subclass AdapterContract and implement make(posts), which returns your adapter
loaded with exactly these posts (e.g. write them to a temp file in your format and point the adapter at it).
"""
from datetime import date

import pytest

from slmcp.adapters.base import QueryError
from slmcp.schema import from_dict

TODAY = date(2026, 11, 10)


def _post(when, text, platform="news"):
    return from_dict({"date": when, "text": text, "source": "Sumber Contoh", "platform": platform,
                      "url": f"https://example.com/{abs(hash((when, text)))}"})


POSTS = [
    _post("2026-11-09T20:00:00+07:00", "Jalur Sepeda baru dibuka"),
    _post("2026-11-08T09:00:00+07:00", "Warga senang dengan jalur sepeda", "twitter"),
    _post("2026-11-08T23:30:00+07:00", "Harga cabai naik lagi"),
    # 17:30 UTC on 7 Nov is 00:30 WIB on 8 Nov: the day is decided in WIB
    _post("2026-11-07T17:30:00+00:00", "Jalur sepeda ramai tengah malam", "twitter"),
    _post("2026-11-01T10:00:00+07:00", "Jalur sepeda direncanakan"),
    # end-day boundary for a search ending on 9 Nov: 23:30 WIB is in, 00:10 WIB on 10 Nov is out
    _post("2026-11-09T23:30:00+07:00", "Batas hari: lampu jalan mati"),
    _post("2026-11-10T00:10:00+07:00", "Batas hari: lampu jalan menyala"),
    # same time as POSTS[0]: ties keep the source's order
    _post("2026-11-09T20:00:00+07:00", "Jalur sepeda kedua di jam yang sama"),
    _post("2026-11-05T10:00:00+07:00", "Sepeda di jalur lambat"),
]


class AdapterContract:
    def make(self, posts):
        raise NotImplementedError

    def search(self, keyword, start, end):
        return self.make(POSTS).search(keyword, start, end, today=TODAY)

    def test_keyword_is_case_insensitive_and_searched_in_text(self):
        hits = self.search("JALUR SEPEDA", "2026-11-01", "2026-11-10")
        assert len(hits) == 5
        assert all("jalur sepeda" in p.text.lower() for p in hits)

    def test_start_and_end_are_inclusive(self):
        hits = self.search("jalur sepeda", "2026-11-08", "2026-11-09")
        assert {p.date_wib.date() for p in hits} == {date(2026, 11, 8), date(2026, 11, 9)}

    def test_day_boundaries_use_wib(self):
        hits = self.search("tengah malam", "2026-11-08", "2026-11-08")
        assert len(hits) == 1
        assert self.search("tengah malam", "2026-11-07", "2026-11-07") == []

    def test_multi_word_keyword_is_a_phrase_match(self):
        texts = [p.text for p in self.search("jalur   sepeda", "2026-11-05", "2026-11-05")]
        assert texts == []  # "Sepeda di jalur lambat" has both words but not the phrase
        assert len(self.search("sepeda di jalur", "2026-11-05", "2026-11-05")) == 1

    def test_end_day_includes_2330_wib_and_excludes_next_day_0010(self):
        texts = [p.text for p in self.search("batas hari", "2026-11-09", "2026-11-09")]
        assert texts == ["Batas hari: lampu jalan mati"]
        texts = [p.text for p in self.search("batas hari", "2026-11-10", "2026-11-10")]
        assert texts == ["Batas hari: lampu jalan menyala"]

    def test_ties_keep_the_source_order(self):
        hits = self.search("jalur sepeda", "2026-11-09", "2026-11-09")
        assert [p.text for p in hits] == ["Jalur Sepeda baru dibuka", "Jalur sepeda kedua di jam yang sama"]

    def test_results_are_sorted_by_date(self):
        hits = self.search("jalur sepeda", "2026-11-01", "2026-11-10")
        assert [p.date for p in hits] == sorted(p.date for p in hits)

    def test_no_match_returns_empty_list(self):
        assert self.search("tidak ada kata ini", "2026-11-01", "2026-11-10") == []

    def test_results_are_posts_with_the_same_fields(self):
        hits = self.search("cabai", "2026-11-08", "2026-11-08")
        assert [p.to_dict() for p in hits] == [POSTS[2].to_dict()]

    @pytest.mark.parametrize("keyword, start, end, match", [
        ("", "2026-11-01", "2026-11-09", "keyword"),
        ("   ", "2026-11-01", "2026-11-09", "keyword"),
        ("sepeda", "2026-11-09", "2026-11-01", "after"),
        ("sepeda", "2025-11-01", "2026-11-09", "maximum is 366"),
        ("sepeda", "2026-11-01", "2026-11-11", "future"),
        ("sepeda", "09/11/2026", "2026-11-09", "YYYY-MM-DD"),
        ("sepeda", "minggu lalu", "2026-11-09", "YYYY-MM-DD"),
    ])
    def test_bad_requests_are_refused_with_a_clear_error(self, keyword, start, end, match):
        with pytest.raises(QueryError, match=match):
            self.search(keyword, start, end)

    def test_366_days_is_allowed(self):
        assert isinstance(self.search("sepeda", "2025-11-10", "2026-11-10"), list)
