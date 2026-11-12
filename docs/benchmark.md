# Benchmark (v1.1)

A public question set for the two tools (`news_recap`, `social_recap`) on the snapshot `data-2026-10-v1`, with gold answers computed by code, plus a harness to run any model behind an OpenAI-compatible endpoint (local or hosted) through the MCP tools and score it. It tests the **agent**: does it use the tool, ask for the right period, report the tool's numbers exactly, say "no data" when there is none, and give only real links? It does not test sentiment-label quality.

## What is in it
`benchmark/questions-v1.1.jsonl`: **226 items = 113 questions, each in Indonesian and English** (same `pair_id`, same gold), so the gap between Indonesian and English can be measured. Built from templates, a fixed list of 22 topics (`banjir`, `gempa`, `kesehatan`, …) and seed 2026, so it is the same on every run.

| Type | Pairs | Example (Indonesian) | Right answer |
|---|---|---|---|
| `count_fixed` | 16 | Berapa jumlah berita tentang cuaca dari 9 sampai 11 Oktober 2026? | a number |
| `count_relative` | 16 | Berapa jumlah berita tentang pajak kemarin? (8 `kemarin`, 8 `minggu lalu`) | a number |
| `sentiment` | 12 | Bagaimana sentimen berita tentang narkoba dari 4 sampai 14 Oktober 2026? | percentages / counts |
| `peak` | 10 | Hari apa berita tentang pajak paling banyak, dari 1 sampai 14 Oktober 2026? Berapa jumlahnya? | day + count |
| `compare` | 8 | Bandingkan jumlah berita tentang bbm dan ekspor minggu lalu. Mana yang lebih banyak? | two numbers |
| `links` | 8 | Berikan 3 contoh judul berita tentang pajak kemarin, lengkap dengan link-nya. | links from the tool output |
| `odd_keyword` | 6 | nonsense word, typo, UPPER case, a long phrase instead of a keyword | a number (0 for nonsense) |
| `abstain_relative` | 12 | … hari ini / minggu ini / bulan ini / bulan lalu / 7 or 30 hari terakhir | **no data** |
| `abstain_future` | 8 | … bulan Desember 2026 | **no data** |
| `abstain_past` | 6 | … bulan September 2026 | **no data** |
| `long_range` | 5 | … dari 1 Januari sampai 14 Oktober 2026 | **no data** (only partly covered) |
| `out_of_scope` | 6 | Siapa presiden Indonesia saat ini? · social media questions (the snapshot has no social posts) | no tool / **no data** |

37 of 113 pairs (33%) are abstain items. "Today" is frozen at Thursday 15 Oct 2026 and the data covers 1–14 Oct, so with the strict data range (`docs/tools.md`) `hari ini` and `minggu ini` (12–15 Oct) have no data either.

Relative-date items (84) carry a `context` in the gold, e.g. `today = 15 Oct 2026 (Thu); data = 1–14 Oct 2026; bulan lalu = 1–30 Sep 2026`, so a reader never assumes the real date.

One item per line:

```json
{"id": "q025-id", "pair_id": "q025", "lang": "id", "type": "count_relative",
 "question": "Berapa jumlah berita tentang pajak minggu lalu?",
 "expected": [{"tool": "news_recap", "keyword": "pajak", "period": "minggu lalu"}],
 "gold": {"total": 90, "start": "2026-10-05", "end": "2026-10-11"}, "abstain": false, "rule": "count"}
```

`expected` lists the tool calls that answer the question; `rule` says how it is scored.

## Gold answers
Computed by `slmcp/bench/build.py` from the snapshot through the same `news_recap` / `social_recap` functions the MCP tools use. Two tests guard them (they run in CI, which downloads the snapshot from the release):
- the file rebuilt from the snapshot is byte-identical to `benchmark/questions-v1.1.jsonl`;
- every gold answer is reachable by calling the MCP tools with the item's `expected` arguments (`python -m slmcp.bench.check`).

A person checks 30 items by hand (seeded sample, `python -m slmcp.bench.handcheck`): is the question clear, and is the gold right? The v1 check (12 Nov 2026): **30/30 clear, 25/30 gold right**; the 5 problems were fixed in v1.1 (see "Versions").

## How to run
```bash
# 0. snapshot (README, "Data snapshot"), then:
python -m slmcp.bench.check                      # no model: gold reachable through the tools (226/226)

# 1. a model over any OpenAI-compatible endpoint; the key stays in the environment
export SLMCP_BENCH_BASE_URL=http://localhost:8000/v1
export SLMCP_BENCH_MODEL=Qwen3.6-27B
export SLMCP_BENCH_API_KEY=...                   # optional
export SLMCP_BENCH_EXTRA='{"chat_template_kwargs": {"enable_thinking": false}}'   # optional
export SLMCP_BENCH_USER_AGENT=...                # optional, for gateways that block unknown clients
python -m slmcp.bench.run --out runs/qwen-run1.jsonl                # all 226 items
python -m slmcp.bench.run --out runs/smoke.jsonl --sample 10 --seed 1

# ablations (S0-7): change what the model sees, not the server
python -m slmcp.bench.run --out runs/qwen-desc.jsonl --ablate descriptions       # one-line tool descriptions
python -m slmcp.bench.run --out runs/qwen-st.jsonl --ablate sentiment_total      # no sentiment_total in results
python -m slmcp.bench.run --out runs/qwen-rd.jsonl --ablate relative_dates       # no period words, no relative_dates

# 2. score
python -m slmcp.bench.score --run runs/qwen-run1.jsonl --md runs/qwen-run1.md
```

**Ablations** are done in the runner, so the server code stays the same:
- `descriptions`: each tool description is replaced by one line ("Count Indonesian news headlines that contain a keyword in a date range."); the parameters are unchanged.
- `sentiment_total`: the `sentiment_total` field is removed from every tool result, and the description no longer mentions it; the model has to read the trend counts itself.
- `relative_dates`: the `period` parameter and the period-word text are removed from the descriptions, a period word sent anyway gets an error ("start_date and end_date must be real dates … period words are not accepted"), and the `relative_dates` field is removed from results. The model has to work out the dates itself.

Each run line also records `ablation` and `max_prompt_tokens`, the largest prompt of any single round, so a smaller context (for example 16k) can be checked afterwards without rerunning.

The runner gives the model the real MCP tools (in-process MCP client on the same server code as `slmcp-server`), starts a fresh conversation per question, sends no system prompt (like a plain chat client), uses temperature 0 and allows up to 5 tool rounds. Each line of the run file has the answer, every tool call with its arguments and result, the stop reason, time and token use.

## Scoring (automatic)
Per item (`slmcp/bench/score.py`):

| Check | Meaning |
|---|---|
| tool used | at least one tool call |
| right period | a call covered exactly the expected dates (from the tool result, so a period word counts) |
| rule | `count`: the gold total appears in the answer (for a total of 0: the answer says nothing was found) · `sentiment`: every non-zero category appears as a percentage (±0.05, or rounded to a whole number) or a count · `peak`: the peak count and the peak day number appear · `compare`: both totals appear · `links`: 3 links to 3 different stories (or all, if the tool has fewer than 3 different stories), every link in the tool output, and each link next to its headline (at least 60% of the headline's first 8 words within 400 characters before or 200 after the link). The same story from two sections or outlets counts once. On the typo item, `count` also passes if the answer *says* it read the typo as the corrected keyword and gives that keyword's total (typo rule decided by Ari 12 Nov 2026) · `abstain`: the answer says there is no data and the model made no call that returned data · `no_tool`: no call that returned data |
| links from tool | every URL in the answer appears in a tool result |
| warnings told | answers with data mention both "rough model labels" and "headlines only" |
| other period on abstain | an abstain item where the model fetched data for another period |
| numbers not in tool | numbers ≥ 10 in the answer that appear in no tool result (years and day numbers ignored), to flag invented numbers |

An item **passes** when the rule passes and, for answerable items, the tool was used with the right period. An empty final answer (for example after the maximum of 5 tool rounds) never passes. Number formats `1.346`, `1,346` (thousands) and `55,6` / `55.6` are read correctly.

## Manual reading (rule)
Read every failed item and every item with "numbers not in tool", with these rules:
1. **Rounding and sums are fine**: 55.6% written as 56%, or two tool numbers added correctly, still pass.
2. **Wrong numbers fail**: a number that is not in the tool output and not a correct sum or rounding of it.
3. **Invented links fail**, even one.
4. **Another period fails**: an answer about a different period than asked (e.g. October numbers for a December question) fails, even if it says "no data" first. Offering another period *as a question* ("Do you want October instead?") is fine.
5. **An honest "nothing found / no data" passes** for abstain items and for keywords with 0 results, if it is said politely and clearly.
6. **Out of scope**: no tool is needed. A normal answer passes (a poem, general knowledge), and so does saying it is outside the news data. It fails only if it makes up news data, or states a future price or forecast ("BBCA will be 9,500 tomorrow", "it will rain tomorrow") as fact. An empty answer always fails.
7. A longer keyword (for `odd_keyword`) that gives a different total is read by hand; it may still be a fair answer.

## Limits
- **Two weeks of news headlines only**: 43,422 Indonesian headlines, 1–14 Oct 2026 (GDELT). No article text, no social media posts, no engagement. Long ranges and "last month" can only be abstain items.
- **Sentiment labels are rough**: model labels (Qwen3.6-27B) matched a person's labels on 63 of 100 headlines (Cohen's kappa 0.44). The benchmark scores whether the agent reports the tool's percentages, not whether they are true.
- **`social_recap`** is only tested as "no data" here (the snapshot has no social posts); social behaviour is tested on synthetic data in the unit tests.
- **Templates**: questions are generated from templates, so wording is less varied than real users; the English version keeps the Indonesian keyword in quotes (the data is Indonesian).
- **Automatic scoring is a filter, not a judge**: regex checks for "no data" and warnings can miss or over-count; failed items need the manual reading above.
- The hand check covered 30 of 226 items, by one person.

## Versions
- **v1.1** (12 Nov 2026, after the hand check): same 226 questions and ids; only gold and scoring changed.
  - `links` (16 items): the gold lists 3 example headlines with links from 3 different stories (duplicates across sections/outlets grouped as one story); the scorer needs 3 links to different stories, all from the tool output, each next to its headline. Before, one link was enough.
  - `out_of_scope` without a tool (8 items): a normal answer passes (gold `accept` text and manual rule 6); empty answers still fail.
  - Relative-date items (84): gold `context` states the frozen today and the data range.
  - Typo item (`banjjir`, 2 items): a *said* correction to `banjir` with its total (111) also passes; a silent correction fails. **Typo rule decided by Ari 12 Nov 2026.**
  - The no-data phrase list also accepts "tidak ditemukan" / "not found".
  - A gold total of 0 now needs the answer to *say* nothing was found (a "0" inside a table no longer counts).
  - The headline check accepts small rewording: 60% of the headline's first 8 words near the link.
- **v1** (12 Nov 2026): first version, kept as `benchmark/questions-v1.jsonl` for history; the code now builds v1.1.
