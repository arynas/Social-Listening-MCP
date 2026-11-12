"""Runner (with a stub model, no network) and scorer."""
import asyncio
import json
from pathlib import Path

from slmcp.bench.run import run_items
from slmcp.bench.score import numbers, score_item, summary

ROOT = Path(__file__).parent.parent


def write_config(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(f"data_source:\n  adapter: csv\n  path: {ROOT / 'examples' / 'posts_sample.csv'}\n"
                   "  sentiment_labels: model\nmodel:\n  endpoint: http://localhost:8000/v1\n  name: m\n"
                   "today: 2026-11-10\n")
    return cfg


def stub_chat(script):
    """A fake model: first asks for the tool calls in script, then answers with the last tool result."""
    def chat(messages, tools):
        assert {t["function"]["name"] for t in tools} == {"news_recap", "social_recap"}
        done = sum(1 for m in messages if m["role"] == "tool")
        if done < len(script):
            name, args = script[done]
            fn = {"name": name, "arguments": json.dumps(args)}
            return {"role": "assistant", "content": None,
                    "tool_calls": [{"id": f"c{done}", "type": "function", "function": fn}],
                    "_usage": {"prompt_tokens": 10, "completion_tokens": 2}}
        last = json.loads(messages[-1]["content"]) if done else {}
        return {"role": "assistant", "content": f"Total {last.get('total')}.", "_usage": {}}
    return chat


def test_runner_records_answer_and_real_tool_calls(tmp_path):
    out = tmp_path / "run.jsonl"
    items = [{"id": "x-id", "question": "Berapa berita banjir kemarin?"}]
    chat = stub_chat([("news_recap", {"keyword": "banjir", "period": "kemarin"})])
    asyncio.run(run_items(write_config(tmp_path), items, chat, out, model="stub"))
    [rec] = [json.loads(line) for line in out.read_text().splitlines()]
    assert rec["id"] == "x-id" and rec["stopped"] == "answer" and rec["model"] == "stub"
    [call] = rec["tool_calls"]
    result = json.loads(call["result"])
    assert call["args"] == {"keyword": "banjir", "period": "kemarin"} and result["start_date"] == "2026-11-09"
    assert rec["answer"] == f"Total {result['total']}."
    assert rec["usage"] == {"prompt_tokens": 10, "completion_tokens": 2}


def test_numbers_reads_indonesian_and_english_formats():
    assert numbers("1.346 berita, 55,6%, 35.9 % dan 248") == [1346.0, 55.6, 35.9, 248.0]


ITEM = {"id": "q1-id", "type": "count_relative", "lang": "id", "rule": "count", "abstain": False,
        "gold": {"total": 143, "start": "2026-10-05", "end": "2026-10-11"}}
RESULT = {"data_available": True, "start_date": "2026-10-05", "end_date": "2026-10-11", "total": 143,
          "samples": [{"url": "https://example.com/a"}]}


def run(answer, results):
    return {"answer": answer, "stopped": "answer",
            "tool_calls": [{"name": "news_recap", "args": {}, "result": json.dumps(r)} for r in results]}


def test_count_passes_with_the_right_period_and_number_and_warnings():
    s = score_item(ITEM, run("Ada 143 judul berita. Label sentimen adalah perkiraan model.", [RESULT]))
    assert s["pass"] and s["right_period"] and s["warnings_told"] and s["numbers_not_in_tool"] == []


def test_count_fails_on_wrong_period_or_number_and_flags_invented_numbers():
    wrong = dict(RESULT, start_date="2026-10-08", end_date="2026-10-14", total=101)
    s = score_item(ITEM, run("Sekitar 84 berita minggu lalu.", [wrong]))
    assert not s["pass"] and not s["right_period"] and s["numbers_not_in_tool"] == [84.0]
    assert not score_item(ITEM, run("Tidak tahu.", []))["tool_used"]


def test_abstain_needs_a_no_data_statement_and_no_other_period():
    item = {"id": "q2-en", "type": "abstain_future", "lang": "en", "rule": "abstain", "abstain": True,
            "gold": {"data_available": False, "asked": ["2026-12-01", "2026-12-31"],
                     "must_tell_user": ["..."]}}
    no_data = {"data_available": False, "asked_period": {"start": "2026-12-01", "end": "2026-12-31"}}
    good = score_item(item, run("Sorry, there is no data for December 2026.", [no_data]))
    assert good["pass"] and good["right_period"] and not good["answered_other_period"]
    answer = "No data for December, but in October there were 282 headlines."
    bad = score_item(item, run(answer, [no_data, RESULT]))
    assert not bad["pass"] and bad["answered_other_period"]


def test_links_must_come_from_tool_output():
    item = dict(ITEM, rule="links", type="links")
    assert score_item(item, run("Lihat https://example.com/a", [RESULT]))["pass"]
    s = score_item(item, run("Lihat https://example.com/zzz", [RESULT]))
    assert not s["pass"] and s["links_from_tool"] is False


def test_summary_table_has_groups():
    s = score_item(ITEM, run("143", [RESULT]))
    table = summary([s, dict(s, lang="en", id="q1-en")])
    assert "| all | 2 |" in table and "| lang en | 1 |" in table and "| type count_relative | 2 |" in table


def test_handcheck_sample_mixes_types_and_languages():
    from slmcp.bench.check import load_items
    from slmcp.bench.handcheck import gold_text, sample
    items = load_items(ROOT / "benchmark" / "questions-v1.jsonl")
    rows = sample(items, 30, 30)
    assert len(rows) == 30 and len({r["pair_id"] for r in rows}) == 30
    assert sum(r["lang"] == "id" for r in rows) == 15
    assert len({r["type"] for r in rows}) == 12
    assert sample(items, 30, 30) == rows  # same seed, same sample
    assert all(gold_text(r) for r in rows)


def test_an_empty_answer_never_passes():
    item = {"id": "q3-en", "type": "out_of_scope", "lang": "en", "rule": "no_tool", "abstain": True,
            "gold": {"expect_tool": False}}
    looped = {"answer": "", "stopped": "max_rounds",
              "tool_calls": [{"name": "news_recap", "args": {}, "result": '{"error": "x"}'}] * 5}
    assert not score_item(item, looped)["pass"]
    assert score_item(item, {"answer": "That is outside the news data.", "tool_calls": []})["pass"]
