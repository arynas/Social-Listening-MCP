"""The benchmark file: shape (always) and gold answers recomputed from the snapshot (when downloaded)."""
import asyncio
import collections
from pathlib import Path

import pytest

from slmcp.bench.build import build, dumps
from slmcp.bench.check import check_all, load_items

ROOT = Path(__file__).parent.parent
QUESTIONS = ROOT / "benchmark" / "questions-v1.1.jsonl"
CONFIG = ROOT / "config.snapshot.example.yaml"
SNAPSHOT = ROOT / "data" / "snapshot" / "posts-2026-10.labelled.jsonl"
ITEMS = load_items(QUESTIONS)
TYPES = {"count_fixed", "count_relative", "sentiment", "peak", "compare", "links", "abstain_relative",
         "abstain_future", "abstain_past", "long_range", "out_of_scope", "odd_keyword"}
needs_snapshot = pytest.mark.skipif(not SNAPSHOT.exists(), reason="snapshot not downloaded (see README)")


def test_size_ids_and_types():
    assert 150 <= len(ITEMS) <= 300
    assert len({i["id"] for i in ITEMS}) == len(ITEMS)
    assert {i["type"] for i in ITEMS} == TYPES
    rules = {"count", "sentiment", "peak", "compare", "links", "abstain", "no_tool"}
    assert {i["rule"] for i in ITEMS} == rules
    for i in ITEMS:
        assert set(i) == {"id", "pair_id", "lang", "type", "question", "expected", "gold", "abstain", "rule"}


def test_every_question_has_an_english_twin_with_the_same_gold():
    pairs = collections.defaultdict(dict)
    for i in ITEMS:
        pairs[i["pair_id"]][i["lang"]] = i
    for pid, p in pairs.items():
        assert set(p) == {"id", "en"}, pid
        for key in ("type", "expected", "gold", "abstain", "rule"):
            assert p["id"][key] == p["en"][key], (pid, key)
        assert p["id"]["question"] != p["en"]["question"]


def test_abstain_items_have_no_numbers_in_the_gold():
    abstain = [i for i in ITEMS if i["abstain"]]
    assert 0.2 <= len(abstain) / len(ITEMS) <= 0.4
    for i in abstain:
        assert i["rule"] in ("abstain", "no_tool")
        assert "total" not in i["gold"]
        if i["rule"] == "abstain":
            assert i["gold"]["data_available"] is False and i["gold"]["must_tell_user"]
    # with PR #8, today (15 Oct) has no data, so "hari ini" and "minggu ini" are abstain items
    words = {c.get("period") for i in abstain for c in i["expected"]}
    assert {"hari ini", "minggu ini", "bulan lalu"} <= words


@needs_snapshot
def test_gold_answers_recomputed_from_the_snapshot_match_the_file():
    assert dumps(build(CONFIG)) == QUESTIONS.read_text(encoding="utf-8")


@needs_snapshot
def test_every_gold_answer_is_reachable_through_the_mcp_tools():
    report = asyncio.run(check_all(CONFIG, ITEMS))
    assert [r for r in report if r[1]] == []


def test_v11_links_gold_has_distinct_stories_with_headlines():
    links = [i for i in ITEMS if i["type"] == "links"]
    assert len(links) == 16
    for i in links:
        g = i["gold"]
        assert len(g["examples"]) == g["links_needed"] == min(3, len(set(g["stories"].values())))
        assert len({g["stories"][e["url"]] for e in g["examples"]}) == len(g["examples"])
        assert all(e["headline"] and e["url"] in g["sample_urls"] for e in g["examples"])


def test_v11_out_of_scope_accepts_a_normal_answer():
    no_tool = [i for i in ITEMS if i["rule"] == "no_tool"]
    assert len(no_tool) == 8 and all("A normal answer passes" in i["gold"]["accept"] for i in no_tool)


def test_v11_relative_date_items_state_today_and_the_data():
    rel = [i for i in ITEMS if any(c.get("period") for c in i["expected"])]
    assert len(rel) == 84
    head = "today = 15 Oct 2026 (Thu); data = 1–14 Oct 2026; "
    assert all(i["gold"]["context"].startswith(head) for i in rel)
    lalu = [i for i in rel if i["expected"][0]["period"] == "bulan lalu"]
    assert lalu and all(i["abstain"] and "bulan lalu = 1–30 Sep 2026" in i["gold"]["context"] for i in lalu)


def test_v11_typo_item_has_the_decided_alternative():
    [typo] = [i for i in ITEMS if i["lang"] == "id" and i["gold"].get("alt")]
    assert typo["gold"]["total"] == 0 and typo["gold"]["alt"]["keyword"] == "banjir"
    assert "decided by Ari 12 Nov 2026" in typo["gold"]["alt"]["rule"]
