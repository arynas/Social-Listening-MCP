from datetime import date

from slmcp.snapshot import write_snapshot


def row(when, url, keywords=()):
    return {"date": when, "text": "Judul contoh", "source": "example.com", "platform": "news", "url": url,
            "language": "ind", "keywords": list(keywords)}


def test_month_files_and_days_follow_wib(tmp_path):
    # 17:30 UTC on 30 Sep is 00:30 WIB on 1 Oct, so it belongs to the October file
    m = write_snapshot(tmp_path, [row("2026-09-30T17:30:00+00:00", "https://example.com/1")],
                       date(2026, 10, 1), date(2026, 10, 1), {})
    assert list(m["files"]) == ["posts-2026-10.jsonl"] and m["rows_per_day"] == {"2026-10-01": 1}


def test_duplicates_merge_keywords_and_outside_period_is_counted(tmp_path):
    rows = [row("2026-10-01T01:00:00+00:00", "https://example.com/1", ["banjir"]),
            row("2026-10-01T02:00:00+00:00", "https://example.com/1", ["gempa"]),
            row("2026-10-02T01:00:00+00:00", "https://example.com/2"),
            {**row("2026-10-01T01:00:00+00:00", "https://example.com/3"), "text": ""}]
    m = write_snapshot(tmp_path, rows, date(2026, 10, 1), date(2026, 10, 1), {"source": "test"})
    assert (m["rows_unique"], m["duplicates_removed"], m["outside_period"], m["skipped"]) == (1, 1, 1, 1)
    assert m["rows_per_keyword_unique"] == {"banjir": 1, "gempa": 1} and m["source"] == "test"
