"""Benchmark v2 (S1-5 part 1): question file, gold reproducibility, tool-selection scoring (fake model)."""

import asyncio
import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest
import yaml

from slmcp.adapters.csv_json import FileAdapter
from slmcp.bench import build_v2
from slmcp.bench.check import load_items, problems
from slmcp.bench.run import V11_TOOLS, run_items
from slmcp.bench.score import score, score_item, summary_v2
from slmcp.compare import compare_keywords
from slmcp.recap import news_recap
from slmcp.trend import entity_trend

ROOT = Path(__file__).parent.parent
V11 = ROOT / "benchmark" / "questions-v1.1.jsonl"
V2 = ROOT / "benchmark" / "questions-v2.jsonl"
S1 = ROOT / "data" / "snapshot_s1" / "out"
SAMPLE = ROOT / "examples" / "posts_sample.csv"
HAVE_S1 = all((S1 / name).exists() for name in build_v2.DATA_SHA256)


def test_v11_file_is_unchanged():
    digest = hashlib.sha256(V11.read_bytes()).hexdigest()
    assert digest == "81eaa2990e74dac80944a77447187596f7521afc5af6ab1da89465634643f2b8"


def test_v2_file_has_all_v11_questions_and_new_ones():
    v11, v2 = load_items(V11), load_items(V2)
    assert len(v2) == 350 and sum(i["origin"] == "v1.1" for i in v2) == 226
    old = {i["id"]: i for i in v11}
    for item in v2:
        if item["origin"] == "v1.1":  # same question and calls; only the gold may follow the 44-day data
            assert (item["question"], item["expected"]) == (
                old[item["id"]]["question"],
                old[item["id"]]["expected"],
            )
    new = [i for i in v2 if i["origin"] == "v2"]
    pairs = Counter(i["pair_id"] for i in new)
    assert len(new) == 124 and set(pairs.values()) == {2}
    by_pair = {}
    for i in new:
        by_pair.setdefault(i["pair_id"], []).append(i)
    for a, b in by_pair.values():  # Indonesian and English share the calls and the gold
        assert (
            {a["lang"], b["lang"]} == {"id", "en"}
            and a["gold"] == b["gold"]
            and a["expected"] == b["expected"]
        )
    tools = Counter(i["tool"] for i in new if i["lang"] == "id")
    assert tools["detect_spikes"] == tools["compare_keywords"] == tools["entity_trend"] == 18
    kinds = Counter(i["kind"] for i in new if i["lang"] == "id")
    assert kinds["trap"] == 8 and kinds["abstain"] >= 7
    assert all(i["tools"]["best"] for i in new)


def test_v2_meta_records_the_data_release():
    meta = json.loads(V2.with_suffix(".meta.json").read_text())
    assert meta["data_release"] == "data-2026-10-v2" and meta["data_sha256"] == build_v2.DATA_SHA256
    assert meta["items"] == 350 and len(meta["v11_gold_changed"]) == 12


@pytest.mark.skipif(not HAVE_S1, reason="needs release data-2026-10-v2 in data/snapshot_s1/out")
def test_v2_rebuild_is_byte_identical():
    items, _ = build_v2.build(str(ROOT / "config.snapshot-s1.example.yaml"), str(V11))
    assert build_v2.dumps(items) == V2.read_text(encoding="utf-8")


@pytest.mark.skipif(not HAVE_S1, reason="needs release data-2026-10-v2 in data/snapshot_s1/out")
def test_v2_gold_is_reachable_through_the_mcp_tools():
    from slmcp.bench.check import check_all

    report = asyncio.run(check_all(str(ROOT / "config.snapshot-s1.example.yaml"), load_items(V2)))
    assert [r for r in report if r[1]] == []


# --- the scoring path, end to end, with a fake model on the small example data (no real model) ---


def config_file(tmp_path):
    cfg = {
        "data_source": {"adapter": "csv", "path": str(SAMPLE), "sentiment_labels": "model"},
        "model": {"endpoint": "http://localhost:8000/v1", "name": "m"},
        "today": "2026-11-10",
    }
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(cfg))
    return str(p)


def small_items():
    """Items on examples/posts_sample.csv, gold from the same functions as the builder."""
    a, opts = FileAdapter(SAMPLE), {"today": "2026-11-10", "sentiment_labels": "model"}
    dates = {"start_date": "2026-11-03", "end_date": "2026-11-08"}
    recap = news_recap(a, "banjir", **dates, **opts)
    cmp_ = compare_keywords(a, ["banjir", "lampu"], **dates, **opts)
    later = {"start_date": "2026-11-06", "end_date": "2026-11-09"}  # previous period 2-5 Nov is in the data
    trend = entity_trend(a, "jalur sepeda", **later, **opts)

    def item(i, rule, expected, gold, tools, kind="normal", abstain=False):
        return {
            "id": i,
            "pair_id": i,
            "lang": "id",
            "type": i,
            "question": f"question {i}",
            "rule": rule,
            "expected": expected,
            "gold": gold,
            "abstain": abstain,
            "kind": kind,
            "origin": "v2",
            "tool": tools["best"][0],
            "tools": tools,
        }

    return [
        item(
            "count",
            "count",
            [{"tool": "news_recap", "keyword": "banjir", **dates}],
            {"total": recap["total"], "start": recap["start_date"], "end": recap["end_date"]},
            {"best": ["news_recap"], "ok": []},
            kind="trap",
        ),
        item(
            "compare",
            "compare",
            [{"tool": "compare_keywords", "keywords": ["banjir", "lampu"], **dates}],
            build_v2.gold_compare(cmp_),
            {"best": ["compare_keywords"], "ok": ["news_recap"]},
        ),
        item(
            "trend",
            "trend_change",
            [{"tool": "entity_trend", "keyword": "jalur sepeda", **later}],
            build_v2.gold_trend(trend),
            {"best": ["entity_trend"], "ok": ["news_recap"]},
        ),
    ]


def fake_chat(items, mode="good"):
    """good: calls the expected tools (if shown) and answers with the tool results;
    wrong: answers every question with entity_trend; none: answers without tools."""
    by_q = {i["question"]: i for i in items}

    def chat(messages, tools):
        shown = {t["function"]["name"] for t in tools}
        done = [m for m in messages if m["role"] == "tool"]
        if done or mode == "none":
            return {"role": "assistant", "content": " ".join(m["content"] for m in done) or "I don't know."}
        item = by_q[messages[-1]["content"]]
        calls = (
            item["expected"]
            if mode == "good"
            else [
                {
                    "tool": "entity_trend",
                    "keyword": "banjir",
                    "start_date": "2026-11-03",
                    "end_date": "2026-11-08",
                }
            ]
        )
        calls = [c for c in calls if c["tool"] in shown]
        if not calls:
            return {"role": "assistant", "content": "I have no tool for that."}
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": f"c{n}",
                    "type": "function",
                    "function": {
                        "name": c["tool"],
                        "arguments": json.dumps({k: v for k, v in c.items() if k != "tool"}),
                    },
                }
                for n, c in enumerate(calls)
            ],
        }

    return chat


def run(tmp_path, items, mode, tools):
    out = tmp_path / f"{mode}-{len(tools or [])}.jsonl"
    asyncio.run(run_items(config_file(tmp_path), items, fake_chat(items, mode), out, tools=tools))
    return {s["id"]: s for s in score(items, load_items(out))}


def test_fake_model_with_all_tools_passes_and_picks_the_right_tools(tmp_path):
    items = small_items()
    scores = run(tmp_path, items, "good", None)
    for s in scores.values():
        assert s["pass"] and s["tool_choice"] == "right" and s["args_ok"] and s["tools_shown"] == 5
        assert not s["unavailable"]
    table = summary_v2(list(scores.values()))
    assert "| 5 tools | yes | all | 3 | 3/3 | 3/3 |" in table


def test_two_tool_condition_keeps_unavailable_items_apart(tmp_path):
    items = small_items()
    scores = run(tmp_path, items, "good", V11_TOOLS)
    assert scores["count"]["pass"] and scores["count"]["tool_choice"] == "right"
    for i in ("compare", "trend"):  # right tool not shown to the model
        assert (
            scores[i]["unavailable"] and scores[i]["tools_shown"] == 2 and scores[i]["tool_choice"] == "none"
        )
    table = summary_v2(list(scores.values()))
    assert "| 2 tools | no | all | 2 |" in table and "| 2 tools | yes | all | 1 |" in table


def test_wrong_tool_and_no_tool_are_counted(tmp_path):
    items = small_items()
    wrong = run(tmp_path, items, "wrong", None)
    assert wrong["count"]["tool_choice"] == "wrong"  # trap: a new tool for a plain count
    assert wrong["trend"]["tool_choice"] == "right" and not wrong["trend"]["args_ok"]  # wrong keyword
    none = run(tmp_path, items, "none", None)
    assert all(s["tool_choice"] == "none" and not s["pass"] for s in none.values())


def test_extra_and_ok_tool_choice():
    item = small_items()[1]
    base = {"answer": "x", "tools": list(V11_TOOLS) + ["compare_keywords"]}
    calls = [
        {"name": "news_recap", "args": {"keyword": "banjir"}, "result": "{}"},
        {"name": "news_recap", "args": {"keyword": "lampu"}, "result": "{}"},
    ]
    assert score_item(item, {**base, "tool_calls": calls})["tool_choice"] == "ok"
    extra = calls + [{"name": "detect_spikes", "args": {"keyword": "banjir"}, "result": "{}"}]
    assert score_item(item, {**base, "tool_calls": extra})["tool_choice"] == "extra"


def test_new_rules_on_answers():
    items = small_items()
    trend = items[2]
    g = trend["gold"]
    ans = f"Ada {g['total']} berita, naik {abs(g['pct_change'])}% dibanding periode sebelumnya."
    run_rec = {
        "answer": ans,
        "tool_calls": [
            {
                "name": "entity_trend",
                "args": trend["expected"][0] | {},
                "result": json.dumps(
                    {"data_available": True, "start_date": g["start"], "end_date": g["end"]}
                ),
            }
        ],
    }
    assert score_item(trend, run_rec)["rule_ok"]
    spike = {
        **items[0],
        "rule": "spike",
        "gold": {"spike_count": 1, "top_peak": "2026-10-14 01:00", "top_count": 12},
    }
    assert score_item(spike, {"answer": "Lonjakan pada pukul 01.00 WIB, 12 berita.", "tool_calls": []})[
        "rule_ok"
    ]
    none = {**spike, "gold": {"spike_count": 0}}
    assert score_item(none, {"answer": "Tidak ada lonjakan berita.", "tool_calls": []})["rule_ok"]


def test_check_recomputes_new_tool_gold():
    item = small_items()[1]
    a = FileAdapter(SAMPLE)
    r = compare_keywords(a, ["banjir", "lampu"], "2026-11-03", "2026-11-08", today="2026-11-10")
    assert problems(item, [r]) == []
    bad = {**item, "gold": {**item["gold"], "total_ratio": 99}}
    assert problems(bad, [r]) and "total_ratio" in problems(bad, [r])[0]


@pytest.mark.parametrize("answer", [
    "Based on the data, there were **no statistical spikes** detected during this period.",
    "There was no significant spike in news about kpk last week.",
    "No unusual spikes were found.",
    "Spikes were not detected in that range.",
    "Tidak ada lonjakan berita yang signifikan.",
    "Tidak terdeteksi adanya lonjakan pada periode itu.",
    "Lonjakan berita tidak terdeteksi minggu lalu.",
])
def test_no_spike_wordings_count_as_correct(answer):
    item = {**small_items()[0], "rule": "spike", "gold": {"spike_count": 0, "total": 621}}
    assert score_item(item, {"answer": answer, "tool_calls": []})["rule_ok"]


def test_a_spike_answer_is_not_read_as_no_spike():
    item = {**small_items()[0], "rule": "spike", "gold": {"spike_count": 0, "total": 621}}
    run = {"answer": "Ada lonjakan pada 15 September, 135 berita.", "tool_calls": []}
    assert not score_item(item, run)["rule_ok"]
