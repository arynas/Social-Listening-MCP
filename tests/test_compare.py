"""Tests for compare_keywords (S1-2): hand-counted example, equality with news_recap, warnings, MCP tool."""
import asyncio
import io
import itertools
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from mcp import Client

from slmcp.adapters.csv_json import FileAdapter
from slmcp.adapters.memory import MemoryAdapter
from slmcp.compare import clean_keywords, compare_keywords, ratio
from slmcp.config import parse_config
from slmcp.recap import estimate_tokens, news_recap
from slmcp.schema import from_dict
from slmcp.server import build_server, setup_logging

ROOT = Path(__file__).parent.parent
SAMPLE = ROOT / "examples" / "posts_sample.csv"
SNAPSHOT_S1 = ROOT / "data" / "snapshot_s1" / "out"
WIB = timezone(timedelta(hours=7))
IDS = itertools.count(1)


def post(day, text, hour=9, sentiment=None, platform="news", source="Media A"):
    when = datetime(day.year, day.month, day.day, hour, 0, tzinfo=WIB).isoformat()
    return from_dict({"date": when, "text": text, "source": source, "platform": platform,
                      "url": f"https://example.com/{next(IDS)}", "sentiment": sentiment})


D1, D2, D3, D4 = (date(2026, 10, d) for d in (1, 2, 3, 4))


def small_example():
    """By hand, 1-4 Oct:
    banjir: 1 Oct 3 (neg, neg, pos), 2 Oct 1 (neg), 3 Oct 0, 4 Oct 2 (neg, neg) -> 6 total, 5 negative
    gempa:  1 Oct 1 (neg), 2 Oct 1 (neg), 3 Oct 0, 4 Oct 0                      -> 2 total, 2 negative
    one headline has both words ("banjir dan gempa", 2 Oct, negative), counted in both totals above."""
    return [
        post(D1, "banjir di kota", sentiment="negative"), post(D1, "banjir lagi", sentiment="negative"),
        post(D1, "banjir surut", sentiment="positive"), post(D1, "gempa kecil", sentiment="negative"),
        post(D2, "banjir dan gempa", sentiment="negative"), post(D2, "lain hal", sentiment="neutral"),
        post(D3, "cuaca cerah", sentiment="neutral"),
        post(D4, "banjir rob", sentiment="negative"), post(D4, "Banjir Bandang", sentiment="negative"),
        post(D4, "gempa", sentiment="positive", platform="twitter"),  # social, not counted for news
    ]


def test_hand_counted_example():
    out = compare_keywords(MemoryAdapter(small_example()), ["banjir", "gempa"], "2026-10-01", "2026-10-04",
                           today="2026-10-10", unit="day")
    assert out["data_available"] is True and list(out)[0] == "must_tell_user"
    banjir, gempa = out["per_keyword"]
    assert (banjir["total"], gempa["total"]) == (6, 2)
    counts = banjir["sentiment_total"]["counts"]
    assert counts == {"positive": 1, "negative": 5, "neutral": 0, "unlabelled": 0}
    assert banjir["negative_share"] == 83.3 and gempa["negative_share"] == 100.0  # 5/6, 2/2
    pair = out["pairs"][0]
    assert (pair["total_diff"], pair["total_ratio"]) == (4, 3.0)
    assert pair["negative_share_diff"] == -16.7 and pair["negative_share_ratio"] == 0.83  # 83.3/100
    assert out["trend"]["counts"] == {"banjir": [3, 1, 0, 2], "gempa": [1, 1, 0, 0]}
    assert (pair["days_a_higher"], pair["days_b_higher"], pair["days_tied"]) == (2, 0, 2)
    assert out["overlap"]["headlines_in_2_or_more"] == 1 and out["overlap"]["distinct_total"] == 7
    assert any("1 headlines match more than one keyword" in w for w in out["data_warnings"])
    assert any("only 2 headlines" in w for w in out["data_warnings"])  # gempa < 10


def test_totals_equal_news_recap_totals():
    adapter = FileAdapter(SAMPLE)
    for words in (["banjir", "jalur sepeda"], ["banjir", "harga", "lampu"]):
        out = compare_keywords(adapter, words, "2026-11-01", "2026-11-09", today="2026-11-10")
        for e in out["per_keyword"]:
            recap = news_recap(adapter, e["keyword"], "2026-11-01", "2026-11-09", today="2026-11-10")
            assert e["total"] == recap["total"] > 0
            assert e["sentiment_total"] == recap["sentiment_total"]


def test_substring_match_like_news_recap():
    posts = [post(D1, "kebanjiran di desa"), post(D1, "banjir besar"), post(D1, "gempa"), post(D2, "x")]
    out = compare_keywords(MemoryAdapter(posts), ["banjir", "gempa"], "2026-10-01", "2026-10-02",
                           today="2026-10-10")
    assert out["per_keyword"][0]["total"] == 2  # "kebanjiran" counts, as in news_recap


def test_empty_keyword_shows_zero_and_no_ratios_dividing_by_it():
    out = compare_keywords(MemoryAdapter(small_example()), ["banjir", "tsunami"], "2026-10-01",
                           "2026-10-04", today="2026-10-10")
    banjir, tsunami = out["per_keyword"]
    assert tsunami["total"] == 0 and tsunami["samples"] == []
    pair = out["pairs"][0]
    assert pair["total_diff"] == 6 and pair["total_ratio"] is None
    assert pair["negative_share_diff"] is None and pair["negative_share_ratio"] is None
    assert any("'tsunami' has 0 headlines" in w for w in out["data_warnings"])
    reverse = compare_keywords(MemoryAdapter(small_example()), ["tsunami", "banjir"], "2026-10-01",
                               "2026-10-04", today="2026-10-10")
    assert reverse["pairs"][0]["total_ratio"] == 0.0  # 0 / 6 is fine; only dividing by 0 is null


@pytest.mark.parametrize("words", [["banjir"], ["a", "b", "c", "d"], [], ["banjir", "Banjir"], "banjir"])
def test_one_or_four_or_repeated_keywords_are_refused(words):
    out = compare_keywords(MemoryAdapter(small_example()), words, "2026-10-01", "2026-10-04",
                           today="2026-10-10")
    assert "error" in out and "per_keyword" not in out


def test_clean_keywords_and_ratio():
    assert clean_keywords(" banjir ,  harga  beras ") == ["banjir", "harga beras"]
    assert ratio(5, 2) == 2.5 and ratio(1, 0) is None


def test_hourly_when_three_days_or_fewer():
    out = compare_keywords(MemoryAdapter(small_example()), ["banjir", "gempa"], "2026-10-01", "2026-10-03",
                           today="2026-10-10")
    assert out["trend"]["unit"] == "hour" and len(out["trend"]["labels"]) == 72
    assert out["trend"]["counts"]["banjir"][9] == 3  # 1 Oct 09:00 WIB
    assert "hours_a_higher" in out["pairs"][0]
    longer = compare_keywords(MemoryAdapter(small_example()), ["banjir", "gempa"], "2026-10-01",
                              "2026-10-04", today="2026-10-10")
    assert longer["trend"]["unit"] == "day"


def test_three_keywords_overlap_and_pairs():
    posts = [post(D1, "banjir gempa longsor"), post(D1, "banjir longsor"), post(D1, "gempa"),
             post(D2, "banjir")]
    out = compare_keywords(MemoryAdapter(posts), ["banjir", "gempa", "longsor"], "2026-10-01", "2026-10-02",
                           today="2026-10-10", unit="day")
    assert [e["total"] for e in out["per_keyword"]] == [3, 2, 2]
    assert [(p["a"], p["b"]) for p in out["pairs"]] == [("banjir", "gempa"), ("banjir", "longsor"),
                                                        ("gempa", "longsor")]
    ov = out["overlap"]
    assert ov["headlines_in_2_or_more"] == 2 and ov["in_all_three"] == 1 and ov["distinct_total"] == 4
    assert [p["count"] for p in ov["pairs"]] == [1, 2, 1]


def test_strict_data_range_and_period_words():
    adapter = MemoryAdapter(small_example())
    out = compare_keywords(adapter, ["banjir", "gempa"], "2026-12-01", "2026-12-31", today="2026-10-10")
    assert out["data_available"] is False and "per_keyword" not in out
    assert out["keywords"] == ["banjir", "gempa"] and out["must_tell_user"][0].startswith("There is no data")
    future = compare_keywords(adapter, ["banjir", "gempa"], period="minggu ini", today="2026-10-10")
    assert future["data_available"] is False  # 5-10 Oct is not inside 1-4 Oct
    assert "error" in compare_keywords(adapter, ["banjir", "gempa"], "2026-10-04", "2026-10-01",
                                       today="2026-10-10")


def test_output_stays_under_2000_tokens_and_trims_the_table_with_a_warning():
    start = date(2026, 1, 1)
    posts = [post(start + timedelta(days=i), f"{w} " + "x" * 200, sentiment="negative")
             for i in range(300) for w in ("banjir", "gempa", "longsor")]
    out = compare_keywords(MemoryAdapter(posts), ["banjir", "gempa", "longsor"], "2026-01-01", "2026-10-27",
                           today="2026-11-01")
    assert out["output_tokens_estimate"] <= 2000 and estimate_tokens(out) <= 2000
    assert out["trend"] == {"unit": "day", "removed": True}
    assert any("was left out to keep the result short" in w for w in out["must_tell_user"])
    assert [e["total"] for e in out["per_keyword"]] == [300, 300, 300]  # totals are never cut


def server_and_log():
    source = {"adapter": "csv", "path": str(SAMPLE), "sentiment_labels": "model"}
    config = parse_config({"data_source": source, "today": "2026-11-10",
                           "model": {"endpoint": "http://localhost:8000/v1", "name": "m"}})
    stream = io.StringIO()
    setup_logging(stream)
    return build_server(config), stream


def call(server, args):
    async def go():
        async with Client(server) as client:
            r = await client.call_tool("compare_keywords", args)
            return json.loads(r.content[0].text)
    return asyncio.run(go())


def test_compare_keywords_through_mcp_with_one_log_line():
    server, stream = server_and_log()
    out = call(server, {"keywords": ["banjir", "lampu"], "period": "minggu lalu"})
    assert next(iter(out)) == "must_tell_user" and out["data_available"] is True
    assert any("kappa 0.44" in w for w in out["data_warnings"])
    lines = [json.loads(x) for x in stream.getvalue().splitlines() if x.strip()]
    assert len(lines) == 1 and lines[0]["tool"] == "compare_keywords"
    assert lines[0]["args"]["keywords"] == ["banjir", "lampu"] and lines[0]["error"] is None
    assert set(lines[0]["result_count"]) == {"banjir", "lampu"}
    assert "samples" not in json.dumps(lines[0])


def test_long_keyword_is_refused_through_mcp():
    server, stream = server_and_log()
    out = call(server, {"keywords": ["banjir", "berapa banyak berita tentang lampu di jakarta minggu ini"],
                        "period": "minggu lalu"})
    assert "error" in out and "too long" in out["error"]
    assert json.loads(stream.getvalue().splitlines()[0])["error"] == "bad_keyword"


@pytest.mark.skipif(not (SNAPSHOT_S1 / "posts-2026-09.labelled.jsonl").exists(),
                    reason="needs the local S1 snapshot (data/snapshot_s1, not published)")
def test_snapshot_s1_totals_match_news_recap():
    adapter = FileAdapter(SNAPSHOT_S1 / "posts-2026-09.labelled.jsonl")
    out = compare_keywords(adapter, ["banjir", "gempa"], "2026-09-01", "2026-09-30", today="2026-10-15")
    for e in out["per_keyword"]:
        assert e["total"] == news_recap(adapter, e["keyword"], "2026-09-01", "2026-09-30",
                                        today="2026-10-15")["total"]
