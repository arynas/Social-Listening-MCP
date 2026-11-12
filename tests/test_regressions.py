"""S0-8b: one regression test per failure mode in the failure table (docs/regressions.md).

Each test is named after the failure number and fails if the fix is undone. Failures that depend on the
model (not the server) are guarded on the scoring side: the scorer must catch them.
"""
import asyncio
import json
from datetime import date
from pathlib import Path

from mcp import Client

from slmcp.adapters.memory import MemoryAdapter
from slmcp.bench.check import load_items
from slmcp.bench.score import score_item
from slmcp.config import parse_config
from slmcp.recap import news_recap, recap
from slmcp.schema import from_dict
from slmcp.server import NEWS_DESCRIPTION, build_server, check_keyword

ROOT = Path(__file__).parent.parent
TODAY = date(2026, 10, 15)
ITEMS = {i["id"]: i for i in load_items(ROOT / "benchmark" / "questions-v1.1.jsonl")}


def post(day, text, sentiment, n):
    return from_dict({"date": f"2026-10-{day:02d}T{8 + n % 10:02d}:00:00+07:00", "text": text,
                      "source": f"Media {n % 3}", "platform": "news", "url": f"https://example.com/{day}/{n}",
                      "sentiment": sentiment})


# 1-14 Oct, "banjir" every day with a known mix
POSTS = [post(d, f"Banjir {d} {n}", ("positive", "negative", "neutral")[(d + n) % 3], n)
         for d in range(1, 15) for n in range(d % 4 + 1)]
ADAPTER = MemoryAdapter(POSTS)


def run(answer, results):
    return {"answer": answer, "stopped": "answer",
            "tool_calls": [{"name": "news_recap", "args": {}, "result": json.dumps(r)} for r in results]}


def test_f1_wrong_sum_totals_come_from_code_and_add_up():
    r = news_recap(ADAPTER, "banjir", "2026-10-01", "2026-10-14", today=TODAY)
    counts = r["sentiment_total"]["counts"]
    assert sum(counts.values()) == r["total"] == sum(p["total"] for p in r["trend"]["points"])
    assert round(sum(r["sentiment_total"]["pct"].values()), 1) == 100.0
    assert "use sentiment_total" in NEWS_DESCRIPTION  # the model is told not to add up itself


def test_f2_invented_links_fail_and_sample_links_come_from_the_data():
    r = news_recap(ADAPTER, "banjir", "2026-10-01", "2026-10-14", today=TODAY)
    assert {s["url"] for s in r["samples"]} <= {p.url for p in POSTS}
    item = ITEMS["q068-id"]
    tool = {"data_available": True, "start_date": "2026-10-04", "end_date": "2026-10-04",
            "samples": [{"url": e["url"], "text": e["headline"]} for e in item["gold"]["examples"]]}
    invented = " ".join(f"{e['headline']} {e['url']}x" for e in item["gold"]["examples"])
    assert not score_item(item, run(invented, [tool]))["pass"]


def test_f3_minggu_lalu_is_resolved_in_code():
    r = news_recap(ADAPTER, "banjir", today=TODAY, period="minggu lalu")
    assert (r["start_date"], r["end_date"]) == ("2026-10-05", "2026-10-11")
    assert ITEMS["q025-id"]["gold"]["start"] == "2026-10-05"


def test_f4_tool_not_used_is_caught_by_the_scorer():
    s = score_item(ITEMS["q025-id"], {"answer": "Saya tidak punya akses berita.", "tool_calls": []})
    assert not s["tool_used"] and not s["pass"]


def test_f5_keyword_too_long_is_refused_with_a_short_suggestion():
    assert "too long" in check_keyword("bagaimana sentimen berita banjir minggu lalu")
    assert check_keyword("harga beras") is None


def test_f6_warnings_come_first_as_sentences():
    r = news_recap(ADAPTER, "banjir", "2026-10-01", "2026-10-14", today=TODAY, sentiment_labels="model")
    assert list(r)[0] == "must_tell_user" and any("rough model output" in w for w in r["must_tell_user"])
    s = score_item(ITEMS["q025-id"], run("Ada 90 judul berita.", [{"data_available": True,
                                         "start_date": "2026-10-05", "end_date": "2026-10-11"}]))
    assert s["warnings_told"] is False  # the scorer notices dropped warnings


def test_f7_context_overflow_output_stays_small():
    many = [post(1 + i % 14, "Banjir " + "panjang sekali " * 30, "negative", i) for i in range(400)]
    r = recap(many, "banjir", "2026-10-01", "2026-10-14", TODAY)
    assert r["output_tokens_estimate"] <= 2000 and r["total"] == 400


def test_f8_shared_names_not_applicable_to_the_public_data():
    # The public snapshot has no person lists, so a shared-name warning cannot be tested here.
    # Guard: the benchmark has no person-name keywords that could hide this failure.
    assert not any(i["type"] == "shared_name" for i in ITEMS.values())


def test_f9_real_clock_dates_get_no_numbers():
    r = news_recap(ADAPTER, "banjir", "2026-11-09", "2026-11-15", today=TODAY)
    assert r["data_available"] is False and "total" not in r and "relative_dates" not in r


def test_f10_another_period_is_never_suggested_and_answering_one_fails():
    r = news_recap(ADAPTER, "banjir", "2026-12-01", "2026-12-31", today=TODAY)
    assert r["must_tell_user"] == ["There is no data for 1–31 Dec 2026. The data covers 1–14 Oct 2026 only."]
    item = next(i for i in ITEMS.values() if i["type"] == "abstain_future")
    other = {"data_available": True, "start_date": "2026-10-01", "end_date": "2026-10-14", "total": 282}
    s = score_item(item, run("Tidak ada data Desember, tapi Oktober ada 282.", [other]))
    assert not s["pass"] and s["answered_other_period"]


def test_f11_partly_covered_ranges_are_refused_and_named():
    r = news_recap(ADAPTER, "banjir", today=TODAY, period="minggu ini")
    assert r["data_available"] is False and r["missing"] == [["2026-10-15", "2026-10-15"]]


def test_f12_invented_split_is_flagged():
    item = ITEMS["q025-id"]
    wrong = {"data_available": True, "start_date": "2026-10-08", "end_date": "2026-10-14", "total": 101}
    s = score_item(item, run("Sekitar 74 dari 101 berita minggu lalu.", [wrong]))
    assert not s["pass"] and s["numbers_not_in_tool"] == [74.0]


def test_every_failure_in_the_table_has_a_test():
    names = [n for n in globals() if n.startswith("test_f")]
    assert sorted({int(n.split("_")[1][1:]) for n in names}) == list(range(1, 13))


def test_the_server_tells_about_period_words_and_no_data():
    source = {"adapter": "csv", "path": str(ROOT / "examples" / "posts_sample.csv")}
    config = parse_config({"data_source": source, "today": "2026-11-10",
                           "model": {"endpoint": "http://localhost:8000/v1", "name": "m"}})

    async def go():
        async with Client(build_server(config)) as client:
            return (await client.list_tools()).tools
    for t in asyncio.run(go()):
        assert "pass the word" in t.description and "data_available false" in t.description
