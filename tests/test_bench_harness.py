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


LINKS = {"id": "q4-id", "type": "links", "lang": "id", "rule": "links", "abstain": False,
         "gold": {"total": 4, "start": "2026-10-05", "end": "2026-10-11", "links_needed": 3,
                  "stories": {"https://a.com/x": 0, "https://b.com/x": 0, "https://c.com/y": 1,
                              "https://d.com/z": 2}}}
SAMPLES = dict(RESULT, total=4, samples=[
    {"url": "https://a.com/x", "text": "Banjir rendam rumah warga di Bekasi"},
    {"url": "https://b.com/x", "text": "Banjir rendam rumah warga di Bekasi : Media B"},
    {"url": "https://c.com/y", "text": "Tanggul jebol saat hujan deras semalam"},
    {"url": "https://d.com/z", "text": "Pemkot siapkan pompa air tambahan"}])


def test_links_need_three_stories_from_the_tool_each_with_its_headline():
    good = ("1. Banjir rendam rumah warga di Bekasi https://a.com/x\n"
            "2. Tanggul jebol saat hujan deras semalam https://c.com/y\n"
            "3. Pemkot siapkan pompa air tambahan https://d.com/z")
    s = score_item(LINKS, run(good, [SAMPLES]))
    assert s["pass"] and s["links_stories"] == 3 and s["links_with_headline"]


def test_links_fail_on_same_story_too_few_invented_or_missing_headline():
    same = ("Banjir rendam rumah warga di Bekasi https://a.com/x, Banjir rendam rumah warga di Bekasi "
            "https://b.com/x, Tanggul jebol saat hujan deras semalam https://c.com/y")
    assert score_item(LINKS, run(same, [SAMPLES]))["links_stories"] == 2
    assert not score_item(LINKS, run(same, [SAMPLES]))["pass"]
    two = "Banjir rendam rumah warga di Bekasi https://a.com/x; Tanggul jebol saat hujan deras semalam https://c.com/y"
    assert not score_item(LINKS, run(two, [SAMPLES]))["pass"]
    invented = ("Banjir rendam rumah warga di Bekasi https://a.com/x Tanggul jebol saat hujan deras semalam "
                "https://c.com/y Pemkot siapkan pompa air tambahan https://zzz.com/fake")
    assert not score_item(LINKS, run(invented, [SAMPLES]))["pass"]
    shortened = ("Rumah warga di Bekasi kebanjiran https://a.com/x Tanggul jebol saat hujan deras "
                 "https://c.com/y Pompa air tambahan disiapkan Pemkot https://d.com/z")
    assert score_item(LINKS, run(shortened, [SAMPLES]))["pass"]  # small rewording is fine
    bare = "https://a.com/x https://c.com/y https://d.com/z"
    s = score_item(LINKS, run(bare, [SAMPLES]))
    assert not s["pass"] and not s["links_with_headline"]


def test_links_with_fewer_stories_than_three_need_all_of_them():
    gold = dict(LINKS["gold"], links_needed=1, stories={"https://a.com/x": 0})
    one = dict(SAMPLES, samples=SAMPLES["samples"][:1])
    answer = "Banjir rendam rumah warga di Bekasi https://a.com/x"
    assert score_item(dict(LINKS, gold=gold), run(answer, [one]))["pass"]


def test_out_of_scope_passes_with_a_normal_answer_and_fails_when_empty():
    item = {"id": "q5-en", "type": "out_of_scope", "lang": "en", "rule": "no_tool", "abstain": True,
            "gold": {"expect_tool": False, "accept": "..."}}
    assert score_item(item, {"answer": "The sea is wide and blue ...", "tool_calls": []})["pass"]
    assert not score_item(item, {"answer": "", "tool_calls": [], "stopped": "max_rounds"})["pass"]


def test_typo_passes_with_zero_or_with_a_said_correction_only():
    alt = {"keyword": "banjir", "total": 111, "typo": "banjjir"}
    gold = {"total": 0, "start": "2026-10-05", "end": "2026-10-11", "alt": alt}
    item = dict(ITEM, type="odd_keyword", gold=gold)
    fixed = dict(RESULT, total=111)
    assert score_item(item, run("Tidak ditemukan berita tentang banjjir.", [dict(RESULT, total=0)]))["pass"]
    said = score_item(item, run("Saya anggap 'banjjir' salah ketik dari banjir: ada 111 berita.", [fixed]))
    assert said["pass"] and said["typo_corrected"]
    silent = score_item(item, run("Jumlah berita tentang banjir adalah 111.", [fixed]))
    assert not silent["pass"]
    table = score_item(item, run("Banjir: 111 berita. | 10 Okt | 5 | 1 | 0 |", [fixed]))
    assert not table["pass"]  # a 0 in a table is not "nothing found"


def test_summary_table_has_groups():
    s = score_item(ITEM, run("143", [RESULT]))
    table = summary([s, dict(s, lang="en", id="q1-en")])
    assert "| all | 2 |" in table and "| lang en | 1 |" in table and "| type count_relative | 2 |" in table


def test_handcheck_sample_mixes_types_and_languages():
    from slmcp.bench.check import load_items
    from slmcp.bench.handcheck import gold_text, sample
    items = load_items(ROOT / "benchmark" / "questions-v1.1.jsonl")
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


def tools_for(tmp_path, ablate):
    from mcp import Client

    from slmcp.bench.run import openai_tools
    from slmcp.config import load_config
    from slmcp.server import build_server

    async def go():
        async with Client(build_server(load_config(write_config(tmp_path)))) as client:
            return openai_tools((await client.list_tools()).tools, ablate)
    return {t["function"]["name"]: t["function"] for t in asyncio.run(go())}


def test_ablation_descriptions_are_one_line(tmp_path):
    base, short = tools_for(tmp_path, None), tools_for(tmp_path, "descriptions")
    for name in ("news_recap", "social_recap"):
        assert "\n" not in short[name]["description"] and len(short[name]["description"]) < 100
        assert short[name]["parameters"] == base[name]["parameters"]


def test_ablation_relative_dates_hides_period_words(tmp_path):
    tools = tools_for(tmp_path, "relative_dates")
    for t in tools.values():
        assert "period" not in t["parameters"]["properties"]
        assert "period=" not in t["description"] and "minggu lalu" not in t["description"]
        assert "data_available false" in t["description"]  # the no-data rule stays


def test_ablation_sentiment_total_drops_the_field_and_its_mention(tmp_path):
    from slmcp.bench.run import ablate_result
    assert "sentiment_total" not in tools_for(tmp_path, "sentiment_total")["news_recap"]["description"]
    text = json.dumps({"total": 3, "sentiment_total": {"counts": {}}, "trend": {}})
    assert json.loads(ablate_result(text, "sentiment_total")) == {"total": 3, "trend": {}}
    assert ablate_result(text, None) == text
    assert ablate_result("not json", "sentiment_total") == "not json"


def test_ablation_relative_dates_refuses_period_words_and_drops_the_field(tmp_path):
    out = tmp_path / "run.jsonl"
    chat = stub_chat([("news_recap", {"keyword": "banjir", "period": "kemarin"}),
                      ("news_recap", {"keyword": "banjir", "start_date": "kemarin", "end_date": "kemarin"}),
                      ("news_recap", {"keyword": "banjir", "start_date": "2026-11-01",
                                      "end_date": "2026-11-09"})])
    asyncio.run(run_items(write_config(tmp_path), [{"id": "x", "question": "q"}], chat, out,
                          ablate="relative_dates"))
    [rec] = [json.loads(line) for line in out.read_text().splitlines()]
    first, second, third = (json.loads(c["result"]) for c in rec["tool_calls"])
    assert "period words are not accepted" in first["error"] and "error" in second
    assert third["total"] > 0 and "relative_dates" not in third
    assert rec["ablation"] == "relative_dates"


def test_runner_records_the_largest_prompt_of_one_round(tmp_path):
    out = tmp_path / "run.jsonl"
    chat = stub_chat([("news_recap", {"keyword": "banjir", "period": "kemarin"})])
    asyncio.run(run_items(write_config(tmp_path), [{"id": "x", "question": "q"}], chat, out))
    [rec] = [json.loads(line) for line in out.read_text().splitlines()]
    assert rec["max_prompt_tokens"] == 10 and rec["ablation"] is None


def test_thinking_subset_is_stratified_paired_and_fixed():
    from slmcp.bench.check import load_items
    from slmcp.bench.subset import subset_ids
    items = load_items(ROOT / "benchmark" / "questions-v1.1.jsonl")
    ids = subset_ids(items, 30, 60)
    assert ids == (ROOT / "benchmark" / "subset-thinking-60.txt").read_text().split()
    by_id = {i["id"]: i for i in items}
    assert len(ids) == 60 and sum(i.endswith("-id") for i in ids) == 30
    assert {by_id[i]["pair_id"] for i in ids if i.endswith("-id")} == {by_id[i]["pair_id"] for i in ids}
    assert {by_id[i]["type"] for i in ids} == {i["type"] for i in items}


def test_mcnemar_and_bootstrap():
    from slmcp.bench.compare import bootstrap_diff, mcnemar_exact
    assert mcnemar_exact(0, 0) == 1.0
    assert abs(mcnemar_exact(0, 6) - 2 / 64) < 1e-12  # 6 discordant pairs all one way
    assert mcnemar_exact(3, 3) == 1.0
    lo, hi = bootstrap_diff([1] * 50 + [0] * 50, [0] * 100)
    assert 0.38 < lo < 0.5 < hi < 0.62


def test_compare_summarises_runs_and_pairs(tmp_path):
    from slmcp.bench.compare import compare, load_config, summary
    q = ROOT / "benchmark" / "questions-v1.1.jsonl"
    ids = ["q025-id", "q025-en"]  # "pajak minggu lalu", gold 90
    result = json.dumps({"data_available": True, "start_date": "2026-10-05", "end_date": "2026-10-11"})
    good = {"answer": "Ada 90 judul berita. Label sentimen perkiraan model, hanya judul.",
            "stopped": "answer", "seconds": 10, "max_prompt_tokens": 20000,
            "tool_calls": [{"name": "news_recap", "args": {}, "result": result}]}
    bad = dict(good, answer="Tidak tahu.", tool_calls=[])
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    a.write_text("".join(json.dumps(dict(good, id=i)) + "\n" for i in ids))
    b.write_text("".join(json.dumps(dict(bad, id=i)) + "\n" for i in ids))
    items, ra = load_config(q, [str(a), str(a)], ids)
    _, rb = load_config(q, [str(b)], ids)
    s = summary("a", items, ra)
    assert s["totals"] == [2, 2] and s["mean"] == 2 and s["fit"][16384] == 0.0 and s["fit"][32768] == 1.0
    c = compare("a", ra, "b", rb)
    assert c["a_only"] == 2 and c["b_only"] == 0 and c["a_rate"] == 1.0


def test_runner_records_a_dropped_connection_and_goes_on(tmp_path):
    import http.client
    out = tmp_path / "run.jsonl"

    def chat(messages, tools):
        raise http.client.RemoteDisconnected("Remote end closed connection without response")
    items = [{"id": "a", "question": "q"}, {"id": "b", "question": "q"}]
    asyncio.run(run_items(write_config(tmp_path), items, chat, out))
    recs = [json.loads(line) for line in out.read_text().splitlines()]
    assert [r["stopped"] for r in recs] == ["http_error: RemoteDisconnected"] * 2


def test_zero_answers_with_markdown_or_no_posts_pass():
    item = dict(ITEM, type="odd_keyword", gold={"total": 0, "start": "2026-10-05", "end": "2026-10-11"})
    zero = dict(RESULT, total=0)
    assert score_item(item, run("There were **0** news headlines about it.", [zero]))["pass"]
    assert score_item(item, run("No posts found for this keyword and date range.", [zero]))["pass"]
