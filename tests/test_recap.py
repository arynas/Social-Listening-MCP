import importlib.util
import itertools
import json
import re
from datetime import date
from pathlib import Path

import pytest

from slmcp.adapters.csv_json import FileAdapter
from slmcp.adapters.memory import MemoryAdapter
from slmcp.config import load_config, parse_config
from slmcp.recap import (
    estimate_tokens,
    from_config,
    news_recap,
    percentages,
    recap,
    relative_dates,
    resolve_period,
    social_recap,
    trend,
)
from slmcp.schema import from_dict

ROOT = Path(__file__).parent.parent
SNAPSHOT = ROOT / "data" / "snapshot" / "posts-2026-10.labelled.jsonl"
TODAY = date(2026, 10, 15)


IDS = itertools.count(1)


def post(when, text, source="Media A", platform="news", sentiment=None, engagement=None):
    return from_dict({"date": when, "text": text, "source": source, "platform": platform,
                      "url": f"https://example.com/{next(IDS)}", "sentiment": sentiment,
                      "engagement": engagement})


# Tiny invented example, counted by hand:
# banjir posts: 1 Oct (2: positive, negative), 2 Oct (1: negative), 3 Oct (0), 4 Oct (1: no label).
# Sources: Media A 3, Media B 1. One social post (excluded by news_recap).
POSTS = [
    post("2026-10-01T08:00:00+07:00", "Banjir surut, warga kembali", sentiment="positive"),
    post("2026-10-01T23:30:00+07:00", "Banjir rendam jalan utama", "Media B", sentiment="negative"),
    post("2026-10-02T00:10:00+07:00", "Banjir susulan di hilir", sentiment="negative"),
    post("2026-10-04T12:00:00+07:00", "Banjir: daftar posko"),
    post("2026-10-02T09:00:00+07:00", "Lihat banjir di depan rumah", "@akun_a", "twitter", "negative", 50),
    post("2026-10-03T09:00:00+07:00", "Harga cabai naik", sentiment="negative"),
]
ADAPTER = MemoryAdapter(POSTS)


def test_news_recap_matches_hand_counts():
    r = news_recap(ADAPTER, "banjir", "2026-10-01", "2026-10-04", today=TODAY, sentiment_labels="model")
    assert r["total"] == 4
    assert r["sentiment_total"]["counts"] == {"positive": 1, "negative": 2, "neutral": 0, "unlabelled": 1}
    assert r["sentiment_total"]["pct"] == {"positive": 25.0, "negative": 50.0, "neutral": 0.0,
                                           "unlabelled": 25.0}
    assert r["trend"]["unit"] == "day"
    assert [(p["start"], p["total"]) for p in r["trend"]["points"]] == [
        ("2026-10-01", 2), ("2026-10-02", 1), ("2026-10-03", 0), ("2026-10-04", 1)]
    assert r["top_sources"] == [{"name": "Media A", "count": 3}, {"name": "Media B", "count": 1}]
    assert r["top_platforms"] == [{"name": "news", "count": 4}]
    assert len(r["samples"]) == 4 and all(s["url"].startswith("https://example.com/") for s in r["samples"])
    assert "top_posts" not in r
    warnings = " | ".join(r["data_warnings"])
    assert "rough model output" in warnings and "1 posts have no sentiment label" in warnings
    assert "news headlines only" in warnings
    assert r["today"] == "2026-10-15" and r["today_weekday"] == "Kamis"


def test_wib_day_boundary_in_trend():
    # 23:30 WIB on 1 Oct counts on 1 Oct; 00:10 WIB on 2 Oct counts on 2 Oct
    r = news_recap(ADAPTER, "banjir", "2026-10-01", "2026-10-02", today=TODAY)
    assert [p["total"] for p in r["trend"]["points"]] == [2, 1]


def test_social_recap_uses_social_posts_and_top_posts_by_engagement():
    r = social_recap(ADAPTER, "banjir", "2026-10-01", "2026-10-04", today=TODAY)
    assert r["total"] == 1 and r["top_posts"][0]["engagement"] == 50
    assert r["top_platforms"] == [{"name": "twitter", "count": 1}]


@pytest.mark.parametrize("counts, expected", [
    ({"positive": 1, "negative": 1, "neutral": 1}, {"positive": 33.4, "negative": 33.3, "neutral": 33.3,
                                                     "unlabelled": 0.0}),
    ({"positive": 2, "negative": 1}, {"positive": 66.7, "negative": 33.3, "neutral": 0.0, "unlabelled": 0.0}),
    ({}, {"positive": 0.0, "negative": 0.0, "neutral": 0.0, "unlabelled": 0.0}),
])
def test_percentages_add_up_to_100(counts, expected):
    pct = percentages(counts)
    assert pct == expected
    if counts:
        assert round(sum(pct.values()), 1) == 100.0


def test_relative_dates_from_the_frozen_today():
    rd = relative_dates(TODAY)  # Thursday 15 Oct 2026
    assert rd["kemarin"] == ["2026-10-14", "2026-10-14"]
    assert rd["minggu ini"] == ["2026-10-12", "2026-10-15"]
    assert rd["minggu lalu"] == ["2026-10-05", "2026-10-11"]
    assert rd["7 hari terakhir"] == ["2026-10-09", "2026-10-15"]
    assert rd["bulan lalu"] == ["2026-09-01", "2026-09-30"]


def test_trend_merges_into_monday_to_sunday_weeks_beyond_30_days():
    t = trend([], date(2026, 9, 1), date(2026, 10, 14))  # 44 days, 1 Sep is a Tuesday
    assert t["unit"] == "week" and len(t["points"]) <= 30
    assert t["points"][0] == {"start": "2026-09-01", "end": "2026-09-06", "total": 0, "positive": 0,
                              "negative": 0, "neutral": 0, "unlabelled": 0}
    assert t["points"][1]["start"] == "2026-09-07" and t["points"][1]["end"] == "2026-09-13"
    assert t["points"][-1]["end"] == "2026-10-14"


def test_top_list_ties_are_broken_by_name():
    posts = [post("2026-10-01T08:00:00+07:00", "banjir", s) for s in ("Zeta", "Alpha", "Mid")]
    r = recap(posts, "banjir", "2026-10-01", "2026-10-01", TODAY)
    assert [x["name"] for x in r["top_sources"]] == ["Alpha", "Mid", "Zeta"]


def test_warnings_for_empty_outside_partly_and_future_ranges():
    # recap() itself (pure, no range check) still warns; the tools refuse such ranges (tests below)
    r = news_recap(ADAPTER, "gempa", "2026-10-01", "2026-10-04", today=TODAY)
    assert r["total"] == 0 and any("no posts found" in w for w in r["data_warnings"])
    covers = (date(2026, 10, 1), date(2026, 10, 4))
    r = recap(POSTS[:1], "banjir", "2026-09-28", "2026-10-02", TODAY, coverage=covers)
    assert any("partly covered" in w for w in r["data_warnings"])
    r = recap(POSTS[:1], "banjir", "2026-10-01", "2026-10-20", TODAY)
    assert any("after today" in w for w in r["data_warnings"])


# Strict data range. ADAPTER data covers 1-4 Oct 2026; frozen today is Thursday 15 Oct 2026.
DATE_LIKE = re.compile(r"\d{4}-\d{2}-\d{2}")


def dates_in(result):
    return set(DATE_LIKE.findall(json.dumps(result)))


@pytest.mark.parametrize("start, end, line", [
    ("2026-12-01", "2026-12-07", "There is no data for 1–7 Dec 2026. The data covers 1–4 Oct 2026 only."),
    ("2026-11-01", "2026-11-30", "There is no data for 1–30 Nov 2026. The data covers 1–4 Oct 2026 only."),
    ("2026-09-01", "2026-09-10", "There is no data for 1–10 Sep 2026. The data covers 1–4 Oct 2026 only."),
])
def test_out_of_range_gives_no_numbers_and_no_other_dates(start, end, line):
    r = news_recap(ADAPTER, "banjir", start, end, today=TODAY)
    assert r["data_available"] is False and r["must_tell_user"] == [line]
    for key in ("total", "sentiment_total", "trend", "samples", "relative_dates", "error"):
        assert key not in r
    assert r["asked_period"] == {"start": start, "end": end, "period": None}
    assert r["data_covers"] == {"start": "2026-10-01", "end": "2026-10-04"}
    # only the asked period, the covered period and today: no replacement dates to try
    assert dates_in(r) <= {start, end, "2026-10-01", "2026-10-04", "2026-10-15"}


def test_partly_outside_is_refused_and_names_the_missing_part():
    r = news_recap(ADAPTER, "banjir", "2026-09-28", "2026-10-02", today=TODAY)
    assert r["data_available"] is False and "total" not in r
    assert r["missing"] == [["2026-09-28", "2026-09-30"]]
    assert r["must_tell_user"] == ["There is no data for 28–30 Sep 2026, so no numbers are given for "
                                   "28 Sep – 2 Oct 2026. The data covers 1–4 Oct 2026 only."]
    r = news_recap(ADAPTER, "banjir", "2026-09-30", "2026-10-06", today=TODAY)
    assert r["missing"] == [["2026-09-30", "2026-09-30"], ["2026-10-05", "2026-10-06"]]


def test_a_source_without_social_posts_says_so():
    news_only = MemoryAdapter([p for p in POSTS if p.platform == "news"])
    r = social_recap(news_only, "banjir", "2026-10-01", "2026-10-04", today=TODAY)
    assert r["data_available"] is False
    assert r["must_tell_user"] == [
        "There is no data for 1–4 Oct 2026: this data source has no social media posts."]


@pytest.mark.parametrize("word, start, end", [
    ("hari ini", "2026-10-15", "2026-10-15"), ("today", "2026-10-15", "2026-10-15"),
    ("kemarin", "2026-10-14", "2026-10-14"), ("yesterday", "2026-10-14", "2026-10-14"),
    ("minggu ini", "2026-10-12", "2026-10-15"), ("this week", "2026-10-12", "2026-10-15"),
    ("minggu lalu", "2026-10-05", "2026-10-11"), ("Last Week", "2026-10-05", "2026-10-11"),
    ("pekan lalu", "2026-10-05", "2026-10-11"),
    ("bulan ini", "2026-10-01", "2026-10-15"), ("this month", "2026-10-01", "2026-10-15"),
    ("bulan lalu", "2026-09-01", "2026-09-30"), ("last month", "2026-09-01", "2026-09-30"),
])
def test_each_period_word_is_resolved_in_code(word, start, end):
    assert resolve_period(word, None, None, TODAY)[:2] == (start, end)
    assert resolve_period(None, word, None, TODAY)[:2] == (start, end)  # also accepted in start_date


def test_period_word_inside_the_data_gives_numbers():
    r = news_recap(ADAPTER, "banjir", today=date(2026, 10, 3), period="kemarin")
    assert r["data_available"] and r["period"] == "kemarin" and r["total"] == 1
    assert (r["start_date"], r["end_date"]) == ("2026-10-02", "2026-10-02")
    r = news_recap(ADAPTER, "banjir", today=TODAY, period="minggu lalu")  # 5-11 Oct: no data
    assert r["must_tell_user"] == ["There is no data for 5–11 Oct 2026. The data covers 1–4 Oct 2026 only."]
    assert r["asked_period"]["period"] == "minggu lalu"


def test_bad_request_returns_a_short_error_without_other_dates():
    r = news_recap(ADAPTER, "banjir", "minggu kemarin", "2026-10-04", today=TODAY)
    assert "YYYY-MM-DD" in r["error"] and "relative_dates" not in r
    r = news_recap(ADAPTER, "banjir", today=TODAY, period="lusa")
    assert "not known" in r["error"]


def test_output_stays_under_the_token_limit_and_samples_are_capped(tmp_path):
    many = [post(f"2026-10-{1 + i % 14:02d}T08:00:00+07:00", "Banjir " + "panjang sekali " * 20,
                 f"Media {i % 40}", sentiment="negative") for i in range(500)]
    r = recap(many, "banjir", "2026-10-01", "2026-10-14", TODAY)
    assert len(r["samples"]) <= 10
    # the reported estimate is measured on the output itself (chars / 4)
    assert abs(r["output_tokens_estimate"] - estimate_tokens(r)) <= 1
    assert r["output_tokens_estimate"] <= 2000
    tight = recap(many, "banjir", "2026-10-01", "2026-10-14", TODAY, max_tokens=900)
    assert tight["output_tokens_estimate"] <= 900 and tight["total"] == 500  # counts are never cut


def test_synthetic_dataset_spike_and_social_engagement(tmp_path):
    spec = importlib.util.spec_from_file_location("ms", ROOT / "scripts" / "make_synthetic.py")
    ms = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ms)
    path, _ = ms.make_dataset(tmp_path / "posts.jsonl")
    adapter = FileAdapter(path)
    r = news_recap(adapter, "banjir", "2026-08-10", "2026-08-16", today=date(2026, 10, 1))
    by_day = {p["start"]: p["total"] for p in r["trend"]["points"]}
    assert max(by_day, key=by_day.get) == "2026-08-13"  # planted spike: day 12 after 1 Aug
    s = social_recap(adapter, "banjir", "2026-08-01", "2026-09-29", today=date(2026, 10, 1))
    assert s["trend"]["unit"] == "week" and len(s["trend"]["points"]) <= 30
    engagement = [p["engagement"] for p in s["top_posts"]]
    assert engagement == sorted(engagement, reverse=True)
    assert s["output_tokens_estimate"] <= 2000


def test_config_today_and_sentiment_labels(tmp_path):
    source = {"adapter": "csv", "path": str(ROOT / "examples" / "posts_sample.csv"),
              "sentiment_labels": "model"}
    model = {"endpoint": "http://localhost:8000/v1", "name": "m"}
    config = parse_config({"data_source": source, "model": model, "today": "2026-10-15"})
    assert from_config(config) == {"today": "2026-10-15", "sentiment_labels": "model"}


@pytest.mark.skipif(not SNAPSHOT.exists(), reason="snapshot not downloaded (see README)")
def test_snapshot_recap_with_frozen_today():
    config = load_config(ROOT / "config.snapshot.example.yaml")
    adapter = FileAdapter(config.data_source.paths[0])
    r = news_recap(adapter, "banjir", "2026-10-01", "2026-10-14", **from_config(config))
    assert r["total"] == 248 and len(r["trend"]["points"]) == 14
    assert round(sum(r["sentiment_total"]["pct"].values()), 1) == 100.0
    assert r["output_tokens_estimate"] <= 2000


def test_must_tell_user_sentences():
    from slmcp.recap import must_tell_user
    lines = must_tell_user(["no posts found", "already a sentence."])
    assert lines == ["No posts found.", "Already a sentence."]
