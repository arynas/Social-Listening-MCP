"""Tests for detect_spikes (S1-1): pure scoring, hand-counted example, planted spikes, warnings, MCP tool."""
import asyncio
import io
import itertools
import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from mcp import Client

from slmcp.adapters.csv_json import FileAdapter
from slmcp.adapters.memory import MemoryAdapter
from slmcp.config import ConfigError, parse_config
from slmcp.recap import estimate_tokens
from slmcp.schema import from_dict
from slmcp.server import build_server, setup_logging
from slmcp.spikes import choose_unit, detect_spikes, median, robust_baseline, score_series, whole_word

ROOT = Path(__file__).parent.parent
SNAPSHOT_S1 = ROOT / "data" / "snapshot_s1" / "out"
WIB = timezone(timedelta(hours=7))
IDS = itertools.count(1)


def post(day, text="banjir di kota", hour=9, source="Media A", sentiment=None, platform="news"):
    when = datetime(day.year, day.month, day.day, hour, 0, tzinfo=WIB).isoformat()
    return from_dict({"date": when, "text": text, "source": source, "platform": platform,
                      "url": f"https://example.com/{next(IDS)}", "sentiment": sentiment})


def daily(start, counts, **kw):
    """Posts with the given number per day, starting at start."""
    out = []
    for i, n in enumerate(counts):
        out += [post(start + timedelta(days=i), **kw) for _ in range(n)]
    return out


# --- pure scoring -----------------------------------------------------------------------------------

def test_hand_counted_example():
    # Baseline (the 7 days before) = [4, 5, 6, 5, 4, 5, 6]: sorted 4,4,5,5,5,6,6 -> median 5.
    # Absolute deviations 1,0,1,0,1,0,1 -> sorted 0,0,0,1,1,1,1 -> MAD 1 -> spread 1.4826.
    # Today 20: z = (20 - 5) / 1.4826 = 10.117... -> spike. Ratio 20 / 5 = 4.0.
    counts = [4, 5, 6, 5, 4, 5, 6, 20]
    [s] = score_series(counts, window=7, z_threshold=3, min_count=3, first=7)
    assert s["median"] == 5.0
    assert s["spread"] == pytest.approx(1.4826)
    assert s["z"] == pytest.approx(15 / 1.4826)
    assert s["spike"] is True and s["baseline_points"] == 7


def test_mad_zero_uses_sqrt_median_with_minimum_one():
    assert robust_baseline([9, 9, 9, 9, 9]) == (9.0, 3.0)        # sqrt(9) = 3
    assert robust_baseline([0, 0, 0, 0]) == (0.0, 1.0)           # at least 1
    # all baseline days 4, today 10: spread sqrt(4) = 2, z = 3.0 -> spike at the threshold
    [s] = score_series([4, 4, 4, 4, 4, 4, 4, 10], 7, 3, 3, first=7)
    assert s["spread"] == 2.0 and s["z"] == 3.0 and s["spike"]


def test_min_count_blocks_tiny_spikes():
    # 0 0 0 ... then 2: z = 2 / 1 = 2 anyway, and 2 < min_count 3
    [s] = score_series([0] * 7 + [2], 7, 1, 3, first=7)
    assert s["z"] == 2.0 and not s["spike"]


def test_min_ratio_blocks_a_high_z_on_a_flat_baseline():
    # baseline 50,51,52,51,50,52,51: median 51, MAD 1, spread 1.4826; 59 -> z = 8 / 1.4826 = 5.4 but
    # ratio 59 / 51 = 1.16 < 2, so it is not a spike (it was one without the ratio rule)
    counts = [50, 51, 52, 51, 50, 52, 51, 59]
    [s] = score_series(counts, 7, 3, 3, first=7, min_ratio=2.0)
    assert s["median"] == 51.0 and s["z"] == pytest.approx(8 / 1.4826) and not s["spike"]
    assert score_series(counts, 7, 3, 3, first=7, min_ratio=0.0)[0]["spike"]
    # a zero median passes the ratio rule
    assert score_series([0, 0, 0, 0, 0, 0, 0, 5], 7, 3, 3, first=7, min_ratio=2.0)[0]["spike"]


def test_zero_median_needs_ten_posts():
    # baseline all 0: 5 posts is not a spike (min_count_zero_median 10), 12 is
    assert not score_series([0] * 7 + [5], 7, 3, 3, first=7, min_ratio=2, min_count_zero=10)[0]["spike"]
    assert score_series([0] * 7 + [12], 7, 3, 3, first=7, min_ratio=2, min_count_zero=10)[0]["spike"]
    start = date(2026, 9, 1)
    other = daily(start, [1] * 16, text="gempa")  # other news, so the data covers every day
    adapter = MemoryAdapter(daily(start, [0] * 9 + [5] + [0] * 5 + [12]) + other)
    out = detect_spikes(adapter, "banjir", "2026-09-08", "2026-09-16", today="2026-10-01", unit="day")
    assert [s["peak"] for s in out["spikes"]] == ["2026-09-16"]
    assert out["method"]["min_count_zero_median"] == 10


def test_back_to_back_spike_days_become_one_event():
    start = date(2026, 9, 1)
    counts = [10, 11, 9, 10, 12, 10, 11, 10, 9, 10, 40, 90, 50, 10, 11, 10, 60, 10]
    out = detect_spikes(MemoryAdapter(daily(start, counts)), "banjir", "2026-09-08", "2026-09-18",
                        today="2026-10-01", unit="day")
    first, second = out["spikes"]
    assert (first["start"], first["peak"], first["end"]) == ("2026-09-11", "2026-09-12", "2026-09-13")
    assert first["points"] == 3 and first["total_in_run"] == 180 and first["count"] == 90
    assert first["baseline_median"] == 10.0 and first["ratio_to_median"] == 9.0
    assert (second["start"], second["peak"], second["end"]) == ("2026-09-17",) * 3
    assert out["spike_count"] == 2 and out["spike_points"] == 4


def test_keyword_matches_whole_words_only():
    m = whole_word("ott")
    assert m.search("ott kpk tangkap pejabat") and m.search("hasil ott, kpk")
    assert not m.search("elkan baggott cetak gol") and not m.search("otto hasibuan")
    assert whole_word("harga beras").search("harga  beras naik")
    start = date(2026, 9, 1)
    posts = daily(start, [5] * 10, text="OTT KPK di daerah") + daily(start + timedelta(days=9), [40],
                                                                       text="Elkan Baggott cetak gol")
    out = detect_spikes(MemoryAdapter(posts), "ott", "2026-09-08", "2026-09-10", today="2026-10-01",
                        unit="day")
    assert out["spikes"] == [] and out["counts"]["2026-09-10"] == 5


def test_median_even_and_odd():
    assert median([3, 1, 2]) == 2.0 and median([4, 1, 3, 2]) == 2.5 and median([]) == 0.0


def test_planted_spikes_precision_and_recall_on_clear_cases():
    start = date(2026, 9, 1)
    rng_counts = [10, 12, 9, 11, 10, 13, 9, 11, 10, 12, 11, 9, 10, 12, 11, 10, 9, 12, 11, 10,
                  13, 10, 9, 11, 12, 10, 11, 9, 10, 12]
    planted = {9: 60, 17: 45, 24: 80}
    counts = [planted.get(i, c) for i, c in enumerate(rng_counts)]
    adapter = MemoryAdapter(daily(start, counts))
    out = detect_spikes(adapter, "banjir", "2026-09-08", "2026-09-30", today="2026-10-01")
    found = {s["peak"] for s in out["spikes"]}
    truth = {(start + timedelta(days=i)).isoformat() for i in planted}
    tp = len(found & truth)
    precision, recall = tp / len(found), tp / len(truth)
    assert (precision, recall) == (1.0, 1.0)
    assert out["spikes"][0]["peak"] == "2026-09-25"  # highest score first
    top = out["spikes"][0]
    assert top["count"] == 80 and top["ratio_to_median"] == round(80 / top["baseline_median"], 1)
    assert top["samples"] and all(s["url"].startswith("https://example.com/") for s in top["samples"])
    assert top["top_sources"] == [{"name": "Media A", "count": 80}]


def test_flat_series_has_no_spikes():
    start = date(2026, 9, 1)
    adapter = MemoryAdapter(daily(start, [10] * 20))
    out = detect_spikes(adapter, "banjir", "2026-09-08", "2026-09-20", today="2026-10-01")
    assert out["spike_count"] == 0 and out["spikes"] == []
    assert out["data_available"] is True


def test_baseline_uses_days_before_the_range_when_inside_the_data():
    start = date(2026, 9, 1)
    adapter = MemoryAdapter(daily(start, [5] * 10 + [40]))
    out = detect_spikes(adapter, "banjir", "2026-09-11", "2026-09-11", today="2026-10-01", unit="day")
    assert out["points"] == 1 and out["unscored_points"] == 0
    assert [s["peak"] for s in out["spikes"]] == ["2026-09-11"]
    assert out["spikes"][0]["baseline_median"] == 5.0


def test_short_baseline_warning_and_unscored_points():
    start = date(2026, 9, 1)
    adapter = MemoryAdapter(daily(start, [5, 5, 5, 5, 30, 5]))
    out = detect_spikes(adapter, "banjir", "2026-09-01", "2026-09-06", today="2026-10-01", unit="day")
    assert out["unscored_points"] == 3      # the first 3 days have fewer than 3 earlier days
    assert any("baseline is short" in w for w in out["data_warnings"])
    assert out["must_tell_user"][0] == ("3 day(s) could not be scored (fewer than 3 earlier days of data): "
                                        "2026-09-01, 2026-09-02, 2026-09-03.")


def test_empty_data_warning():
    adapter = MemoryAdapter(daily(date(2026, 9, 1), [5] * 20, text="gempa bumi"))
    out = detect_spikes(adapter, "banjir", "2026-09-08", "2026-09-20", today="2026-10-01")
    assert out["total"] == 0 and out["spikes"] == []
    assert any("no posts found" in w for w in out["data_warnings"])


def test_hourly_when_three_days_or_fewer():
    assert choose_unit(date(2026, 9, 1), date(2026, 9, 3)) == "hour"
    assert choose_unit(date(2026, 9, 1), date(2026, 9, 4)) == "day"
    day = date(2026, 9, 2)
    posts = [post(day - timedelta(days=1), hour=h) for h in range(24)]           # 1 per hour the day before
    posts += [post(day, hour=h) for h in range(24) for _ in range(1 if h != 14 else 25)]
    out = detect_spikes(MemoryAdapter(posts), "banjir", "2026-09-02", "2026-09-02", today="2026-10-01")
    assert out["unit"] == "hour" and out["points"] == 24
    assert [s["peak"] for s in out["spikes"]] == ["2026-09-02 14:00"]


def test_negative_spikes_are_separate_and_warn_about_rough_labels():
    start = date(2026, 9, 1)
    posts = daily(start, [10] * 14, sentiment="neutral")
    posts += daily(start + timedelta(days=10), [15], sentiment="negative")
    posts += daily(start, [1] * 14, sentiment="negative")
    adapter = MemoryAdapter(posts)
    out = detect_spikes(adapter, "banjir", "2026-09-08", "2026-09-14", today="2026-10-01",
                        sentiment_labels="model")
    assert [s["peak"] for s in out["negative_spikes"]] == ["2026-09-11"]
    assert out["negative_spikes"][0]["count"] == 16 and out["negative_spikes"][0]["total_posts_at_peak"] == 26
    assert all(s["sentiment"] == "negative" for s in out["negative_spikes"][0]["samples"])
    assert any("kappa 0.44" in w for w in out["data_warnings"])


def test_strict_data_range_and_period_words():
    adapter = MemoryAdapter(daily(date(2026, 10, 1), [5] * 14))
    out = detect_spikes(adapter, "banjir", "2026-12-01", "2026-12-31", today="2026-10-15")
    assert out["data_available"] is False and "spikes" not in out
    out = detect_spikes(adapter, "banjir", period="minggu lalu", today="2026-10-15")
    assert out["data_available"] is True and out["period"] == "minggu lalu"
    assert (out["start_date"], out["end_date"]) == ("2026-10-05", "2026-10-11")
    assert "error" in detect_spikes(adapter, "banjir", "2026-10-10", "2026-10-01", today="2026-10-15")


def test_output_stays_under_2000_tokens_with_many_spikes():
    start = date(2026, 9, 1)
    counts = [5, 6, 5, 4, 6, 5, 6] + [5, 90, 5, 6, 80, 5, 5, 70, 6, 5, 85, 5, 75, 6, 5, 95, 5, 60, 5, 88,
                                      6, 77, 5, 66, 5, 99, 6, 55]
    posts = daily(start, counts, text="banjir " + "x" * 150, sentiment="negative")
    out = detect_spikes(MemoryAdapter(posts), "banjir", "2026-09-08", "2026-10-05", today="2026-10-10",
                        sentiment_labels="model")
    assert len(out["spikes"]) <= 10
    assert out["output_tokens_estimate"] <= 2000 and estimate_tokens(out) <= 2000


def test_config_spikes_section():
    base = {"data_source": {"adapter": "csv", "path": str(ROOT / "examples" / "posts_sample.csv")},
            "model": {"endpoint": "http://localhost:8000/v1", "name": "m"}}
    c = parse_config({**base, "spikes": {"window_days": 14, "z_threshold": 2.5}})
    assert (c.spikes.window_days, c.spikes.z_threshold, c.spikes.min_count) == (14, 2.5, 3)
    assert parse_config(base).spikes.window_days == 7 and parse_config(base).spikes.min_ratio == 2.0
    assert parse_config({**base, "spikes": {"min_ratio": 1.5}}).spikes.min_ratio == 1.5
    assert parse_config(base).spikes.min_count_zero_median == 10
    with pytest.raises(ConfigError):
        parse_config({**base, "spikes": {"min_count": 8, "min_count_zero_median": 5}})
    for bad in ({"window_days": 1}, {"z_threshold": "x"}, {"color": 1}, {"min_count": 2.5},
                {"min_ratio": 0.5}):
        with pytest.raises(ConfigError):
            parse_config({**base, "spikes": bad})


def test_detect_spikes_through_mcp_with_one_log_line():
    source = {"adapter": "csv", "path": str(ROOT / "examples" / "posts_sample.csv"),
              "sentiment_labels": "model"}
    config = parse_config({"data_source": source, "today": "2026-11-10",
                           "model": {"endpoint": "http://localhost:8000/v1", "name": "m"}})
    stream = io.StringIO()
    setup_logging(stream)
    server = build_server(config)

    async def go():
        async with Client(server) as client:
            r = await client.call_tool("detect_spikes", {"keyword": "banjir", "period": "minggu lalu"})
            return json.loads(r.content[0].text)
    out = asyncio.run(go())
    assert "must_tell_user" == next(iter(out))
    lines = [json.loads(x) for x in stream.getvalue().splitlines() if x.strip()]
    assert len(lines) == 1 and lines[0]["tool"] == "detect_spikes" and "spike_count" in lines[0]
    assert "text" not in json.dumps(lines[0]["args"])


@pytest.mark.skipif(not (SNAPSHOT_S1 / "posts-2026-09.labelled.jsonl").exists(),
                    reason="needs the local S1 snapshot (data/snapshot_s1, not published)")
def test_snapshot_s1_anak_krakatau_is_the_top_spike():
    adapter = FileAdapter(SNAPSHOT_S1 / "posts-2026-09.labelled.jsonl")
    out = detect_spikes(adapter, "krakatau", "2026-09-01", "2026-09-30", today="2026-10-15",
                        sentiment_labels="model")
    assert out["spikes"][0]["peak"] == "2026-09-06"
    assert math.isfinite(out["spikes"][0]["z"])
