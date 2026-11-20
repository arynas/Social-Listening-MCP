"""Tests for entity_trend and top_posts (S1-3): hand counts, change, story grouping, warnings, MCP tool."""
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
from slmcp.config import ConfigError, parse_config
from slmcp.recap import estimate_tokens, news_recap
from slmcp.schema import from_dict
from slmcp.server import build_server, setup_logging
from slmcp.trend import entity_trend, group_stories, jaccard, normalise, top_posts

ROOT = Path(__file__).parent.parent
SAMPLE = ROOT / "examples" / "posts_sample.csv"
SNAPSHOT_S1 = ROOT / "data" / "snapshot_s1" / "out"
WIB = timezone(timedelta(hours=7))
IDS = itertools.count(1)


def post(day, text, hour=9, sentiment=None, platform="news", source="media-a.id", engagement=None):
    when = datetime(day.year, day.month, day.day, hour, 0, tzinfo=WIB).isoformat()
    row = {"date": when, "text": text, "source": source, "platform": platform,
           "url": f"https://example.com/{next(IDS)}", "sentiment": sentiment}
    if engagement is not None:
        row["engagement"] = engagement
    return from_dict(row)


def d(day):
    return date(2026, 10, day)


def example():
    """By hand. Previous period 1-3 Oct: 4 'banjir' posts, 1 negative (25.0%).
    Asked period 4-6 Oct: 4 Oct 3 (neg, neg, pos), 5 Oct 0, 6 Oct 3 (neg, neu, pos) -> 6 posts, 3 negative
    (50.0%). Change: +2 posts, +50.0%, negative share +25.0 points. Peak 4 Oct (3; tie with 6 Oct, the
    earlier day wins)."""
    return [
        post(d(1), "banjir A", sentiment="negative"), post(d(2), "banjir B", sentiment="positive"),
        post(d(2), "banjir C", sentiment="neutral"), post(d(3), "banjir D", sentiment="neutral"),
        post(d(4), "banjir E", sentiment="negative"), post(d(4), "banjir F", sentiment="negative"),
        post(d(4), "banjir G", sentiment="positive"), post(d(5), "lain hal", sentiment="neutral"),
        post(d(6), "banjir H", sentiment="negative"), post(d(6), "banjir I", sentiment="neutral"),
        post(d(6), "banjir J", sentiment="positive"),
    ]


def test_hand_counted_example():
    out = entity_trend(MemoryAdapter(example()), "banjir", "2026-10-04", "2026-10-06", today="2026-10-10",
                       unit="day")
    assert list(out)[:2] == ["must_tell_user", "data_available"] and out["data_available"]
    assert out["total"] == 6 and out["sentiment_total"]["pct"]["negative"] == 50.0
    assert out["trend"]["total"] == [3, 0, 3] and out["trend"]["negative"] == [2, 0, 1]
    assert out["peak"] == {"at": "2026-10-04", "count": 3}
    ch = out["change"]
    assert (ch["previous_start"], ch["previous_end"], ch["previous_total"]) == ("2026-10-01", "2026-10-03", 4)
    assert (ch["total_diff"], ch["pct_change"]) == (2, 50.0)
    assert ch["previous_negative_share"] == 25.0 and ch["negative_share_diff"] == 25.0
    assert any("small counts (6 now, 4 before" in w for w in out["data_warnings"])


def test_total_equals_news_recap():
    adapter = FileAdapter(SAMPLE)
    for kw in ("banjir", "jalur sepeda", "harga"):
        out = entity_trend(adapter, kw, "2026-11-05", "2026-11-09", today="2026-11-10")
        recap = news_recap(adapter, kw, "2026-11-05", "2026-11-09", today="2026-11-10")
        assert out["total"] == recap["total"] and out["sentiment_total"] == recap["sentiment_total"]


def test_previous_period_outside_the_data_gives_no_change():
    out = entity_trend(MemoryAdapter(example()), "banjir", "2026-10-02", "2026-10-05", today="2026-10-10")
    assert out["data_available"] is True and out["change"] is None
    assert any("previous period (28 Sep – 1 Oct 2026) is not fully inside the data" in w
               for w in out["must_tell_user"] + out["data_warnings"])


def test_zero_previous_posts_gives_null_percent():
    posts = [post(d(1), "lain"), post(d(2), "lain"), post(d(3), "gempa", sentiment="negative"),
             post(d(4), "gempa", sentiment="negative")]
    out = entity_trend(MemoryAdapter(posts), "gempa", "2026-10-03", "2026-10-04", today="2026-10-10")
    assert out["change"]["previous_total"] == 0 and out["change"]["pct_change"] is None
    assert out["change"]["negative_share_diff"] is None
    assert any("previous period has 0 posts" in w for w in out["data_warnings"])


def test_hourly_when_three_days_or_fewer():
    out = entity_trend(MemoryAdapter(example()), "banjir", "2026-10-04", "2026-10-06", today="2026-10-10")
    assert out["trend"]["unit"] == "hour" and len(out["trend"]["labels"]) == 72
    assert out["trend"]["total"][9] == 3 and out["peak"]["at"] == "2026-10-04 09:00"
    longer = entity_trend(MemoryAdapter(example()), "banjir", "2026-10-03", "2026-10-06", today="2026-10-10")
    assert longer["trend"]["unit"] == "day"


def test_strict_data_range_and_errors():
    adapter = MemoryAdapter(example())
    out = entity_trend(adapter, "banjir", "2026-12-01", "2026-12-07", today="2026-10-10")
    assert out["data_available"] is False and "total" not in out
    assert "error" in entity_trend(adapter, "banjir", "2026-10-06", "2026-10-04", today="2026-10-10")


def test_normalise_removes_site_names_and_punctuation():
    assert normalise("Harga Cabai Naik, Pedagang Mengeluh - Kompas") == ["harga", "cabai", "naik", "pedagang",
                                                                       "mengeluh"]
    assert normalise("Jadwal PSIM vs Persija : Okezone Bola") == ["jadwal", "psim", "vs", "persija"]
    assert normalise("Polisi: Tersangka Ditangkap - Pelaku Mengaku Sudah Tiga Kali Beraksi")[-1] == "beraksi"
    assert jaccard(["a", "b"], ["a", "b", "c", "d"]) == 0.5


def test_near_duplicates_from_three_sources_are_one_story():
    posts = [
        post(d(4), "Gempa M6,1 guncang Sumba Barat Daya - Kompas", source="kompas.com"),
        post(d(4), "Gempa M6,1 Guncang Sumba Barat Daya : Okezone News", source="okezone.com", hour=10),
        post(d(5), "Gempa M6,1 guncang Sumba Barat Daya, warga panik", source="detik.com"),
        post(d(4), "Harga cabai naik di pasar Kupang", source="kompas.com"),
        post(d(9), "Gempa M6,1 guncang Sumba Barat Daya", source="tempo.co"),  # 4 days later: new story
    ]
    groups = group_stories(posts, 0.6)
    assert sorted(len(g) for g in groups) == [1, 1, 3]
    ranking, stories = top_posts(posts, 5, 0.6)
    assert ranking == "distinct sources"
    assert stories[0]["sources"] == 3 and stories[0]["headlines"] == 3
    assert stories[0]["url"] == posts[0].url  # the first headline represents the story
    assert len(stories) == 3


def test_same_source_twice_counts_once():
    posts = [post(d(4), "Banjir rob di Jakarta Utara meluas", source="a.id"),
             post(d(4), "Banjir rob di Jakarta Utara meluas", source="a.id", hour=12),
             post(d(4), "Banjir rob di Jakarta Utara meluas", source="b.id", hour=13)]
    _, stories = top_posts(posts)
    assert stories[0]["sources"] == 2 and stories[0]["headlines"] == 3


def test_engagement_ranking_on_synthetic_social_posts():
    posts = [post(d(4), "video banjir", platform="tiktok", engagement=50),
             post(d(4), "foto banjir", platform="instagram", engagement=900),
             post(d(5), "cuitan banjir", platform="twitter", engagement=300)]
    ranking, stories = top_posts(posts, 2)
    assert ranking == "engagement" and [s["engagement"] for s in stories] == [900, 300]
    out = entity_trend(MemoryAdapter(posts + [post(d(1), "x", platform="twitter", engagement=1)]), "banjir",
                       "2026-10-04", "2026-10-05", today="2026-10-10", source="social")
    assert out["top_stories_ranked_by"] == "engagement" and out["top_stories"][0]["engagement"] == 900
    assert not any("grouping is approximate" in w for w in out["data_warnings"])


def test_output_stays_under_2000_tokens():
    start = date(2026, 1, 1)
    text = "banjir besar hari ke {} " + "kata " * 40
    posts = [post(start + timedelta(days=i), text.format(i), sentiment="negative", source=f"s{i % 7}.id",
                  hour=h) for i in range(300) for h in (8, 12)]
    out = entity_trend(MemoryAdapter(posts), "banjir", "2026-07-01", "2026-10-27", today="2026-11-01")
    assert out["output_tokens_estimate"] <= 2000 and estimate_tokens(out) <= 2000
    assert out["total"] == 238 and out["change"]["previous_total"] == 238


def test_config_trend_section():
    base = {"data_source": {"adapter": "csv", "path": str(SAMPLE)},
            "model": {"endpoint": "http://localhost:8000/v1", "name": "m"}}
    assert parse_config(base).trend.story_similarity == 0.6
    assert parse_config({**base, "trend": {"story_similarity": 0.5}}).trend.story_similarity == 0.5
    for bad in ({"story_similarity": 0}, {"story_similarity": 1.5}, {"story_similarity": "x"}, {"other": 1}):
        with pytest.raises(ConfigError):
            parse_config({**base, "trend": bad})


def test_entity_trend_through_mcp_with_one_log_line():
    source = {"adapter": "csv", "path": str(SAMPLE), "sentiment_labels": "model"}
    config = parse_config({"data_source": source, "today": "2026-11-10",
                           "model": {"endpoint": "http://localhost:8000/v1", "name": "m"}})
    stream = io.StringIO()
    setup_logging(stream)
    server = build_server(config)

    async def go():
        async with Client(server) as client:
            r = await client.call_tool("entity_trend", {"keyword": "banjir", "start_date": "2026-11-06",
                                                        "end_date": "2026-11-09"})
            return json.loads(r.content[0].text)
    out = asyncio.run(go())
    assert next(iter(out)) == "must_tell_user" and out["data_available"] is True
    assert out["change"] is not None and "top_stories" in out
    lines = [json.loads(x) for x in stream.getvalue().splitlines() if x.strip()]
    assert len(lines) == 1 and lines[0]["tool"] == "entity_trend" and lines[0]["result_count"] == out["total"]
    assert "text" not in json.dumps(lines[0]["args"])


@pytest.mark.skipif(not (SNAPSHOT_S1 / "posts-2026-09.labelled.jsonl").exists(),
                    reason="needs the local S1 snapshot (data/snapshot_s1, not published)")
def test_snapshot_s1_previous_period_warning_and_total():
    adapter = FileAdapter(SNAPSHOT_S1 / "posts-2026-09.labelled.jsonl")
    out = entity_trend(adapter, "krakatau", "2026-09-01", "2026-09-14", today="2026-10-15")
    assert out["change"] is None and out["peak"]["at"] == "2026-09-06"
    assert out["total"] == news_recap(adapter, "krakatau", "2026-09-01", "2026-09-14",
                                      today="2026-10-15")["total"]
