# Failure modes and V&V checklist

What has gone wrong when models use these tools, how each problem is detected, how it was fixed, and what is verified, validated or not yet checkable. The evidence comes from work on this public version only: the client trial (`docs/client-trial.md` and its trial notes: OpenCode with Qwen3.6-27B, and Torcons, 5 questions, 2 runs each) and the benchmark runs on v1.1 (`docs/benchmark.md`: 226 items, Qwen3.6-27B and Torcons, 3 runs, plus ablations).

The numbers match the regression tests in `docs/regressions.md` (`test_f1_…` to `test_f12_…`). Failure 8 (a short name shared by two people) can't happen on the public data, which has no person lists, so it isn't in the table below.

"Server side" means the tool gives the model what it needs, checked by pytest. "Answer side" means the model then uses it, checked by the benchmark scorer. Server-side tests can't prove the model will behave.

## 1. Failure table

| # | Failure | Example / evidence | How detected | Fix | Fixed? | Regression test |
|---|---|---|---|---|---|---|
| 1 | Wrong sum | Without `sentiment_total`, the model adds up the trend itself and gets it wrong: sentiment items fall to 33% (Qwen) and 17% (Torcons) in the ablation | Percentages in the answer differ from the computed ones (scorer `sentiment` rule) | `sentiment_total` (counts and percent, adds up to 100.0); the description says not to add up | Yes: with the field, sentiment items pass 98.6% (Qwen) and 100% (Torcons) | Server: `test_f1_…`. Answer: `sentiment` items |
| 2 | Invented links | Client trial: the open model gave 6 generic site links when it had not called the tool. Benchmark: Torcons once changed one word in a real link | Every URL in the answer is checked against the tool output (automatic) | Sample links come only from the data; the description says to give only links from the result; the scorer needs each link next to its headline | Yes on the server side. Model side: Qwen links 100%, Torcons 89.6% | Server: `test_f2_…`. Answer: `links` items |
| 3 | Wrong relative period ("minggu lalu") | Without period words resolved in code (ablation), Qwen passed 0% and Torcons 34% of relative-date count questions | Called dates differ from the phrase (log + scorer `right_period`) | The tools take a period word (`minggu lalu`, `kemarin`, `last week`, ...) and compute the dates from the frozen "today"; every result has `relative_dates` | Yes: relative-date count items pass 100% for both models | Server: `test_f3_…`. Answer: `count_relative`, `compare` |
| 4 | Tool not used | Client trial: the open model skipped the tool in 3 of 10 questions before the fixes and 2 of 10 after ("no news access"); Torcons 0 of 10 | No tool call in the log for that question; the scorer reports `tool_used` | Open: client and model side. Options: a client that lists MCP tools directly, a short system prompt naming the tool, network access off | No | Scorer fails answerable items without a tool call (`test_f4_…`) |
| 5 | Keyword too long | A whole sentence passed as the keyword gives an empty search | Argument check; empty result | The input check refuses more than 4 words or 60 characters and suggests a short keyword; the description says "1 or 2 words" | Guarded on the server. Model side: long-phrase items still often searched with another keyword (`odd_keyword` 66.7% / 63.9%) | Server: `test_f5_…` (`bad_keyword`). Answer: `odd_keyword` |
| 6 | Warnings not told | The tool said "this week is only partly covered", but the answer didn't mention it (client trial before the fix: told in 9 of 13 answers) | Scorer check `warnings_told` (text match); manual reading | `must_tell_user` placed first in the result | Partly. Benchmark: Qwen 88.1%, Torcons 29.4% | Server: `test_f6_…`. Answer: only a text match, so read manually |
| 7 | Context overflow | A long tool result pushes the prompt past a small context | Client error or empty answer; the runner records `max_prompt_tokens` | Result kept under about 2,000 tokens | Yes: the largest prompt was 14,947 tokens (n = 1,715), so every item fits 16k | Server: `test_f7_…` (output size). Fit is reported by `slmcp.bench.compare` |
| 9 | Real-clock date instead of frozen "today" | With "today" frozen at 15 Oct 2026, both models first used November dates (computer clock) | Log: first call dates after "today" | Period words resolved in code. Dates outside the data get a no-data result with no numbers and no other dates | Partly: Torcons uses period words; the open model still starts from November sometimes, but gets no numbers for it | Server: `test_f9_…`. Answer: `abstain_relative`, `abstain_future` |
| 10 | Answer for a different period than asked | Asked about December 2026, the model said December has no data, then answered with October numbers (Torcons, client trial) | Answer period ≠ asked period; a call that fetched data on an abstain item | Strict data range: a period not fully inside the data returns `data_available: false`, no numbers, no other dates, and a polite `must_tell_user` line ("There is no data for 1–31 Dec 2026. The data covers 1–14 Oct 2026 only.") | Server side yes. Benchmark abstain items: Qwen 100%, Torcons 92.8% (it fetched another period on 3 answers) | Server: `test_f10_…`. Answer: `abstain_*`, `long_range`, `out_of_scope` |
| 11 | Data gaps not mentioned | A range only partly inside the data is answered as if complete | Coverage check in the tool | Partly covered periods are refused too, and the missing part is named | Yes on the server; telling the user depends on 6 | Server: `test_f11_…`. Answer: `abstain_relative` (`minggu ini`), `long_range` |
| 12 | Invented split of a tool number | The tool returned 101 for 8–14 Oct; the answer said "about 74 of them were 5–11 Oct" (Torcons, before period words) | A number in the answer that is not in the tool output (scorer `numbers_not_in_tool`) | Came from 9 (wrong dates in the call); fixed by period words in code | Yes in the trial (0 of 2 runs) | Server: `test_f12_…`. Answer: `count_relative` (`minggu lalu`) |

Not seen so far: invented headlines. Apart from failure 12, every number in a checked answer matched the tool output.

## 2. V&V checklist

**Verified automatically (by code, against a specification):**
- [x] Numbers: totals reconcile with the trend; percentages add up to 100.0; hand-count tests on small examples.
- [x] Dates: period words are turned into dates by code from a fixed "today" (tested for each word, Indonesian and English). Periods not fully inside the data, future dates and reversed ranges get no numbers.
- [x] Links: sample links come only from the data (tool side). Every URL in an answer is checked against the tool output (benchmark scorer).
- [x] Tool used: the per-call JSON log shows whether a tool was called, with which arguments, how long it took and whether it failed.
- [x] Abstain: periods without data return `data_available: false` with a polite sentence and no numbers (tested). Whether the model then says "no data" is checked by the benchmark's abstain items (33% of the items).
- [x] Output size: under about 2,000 tokens, with a test.
- [~] Warnings told: only a text match in the scorer; needs manual reading.

**Validated with people (against what users need):**
- [x] Sentiment labels: a blind hand check of 100 headlines by one person. The model labels match 63 of 100 (Cohen's kappa 0.44, moderate). The labels are treated as rough model output, and every result says so.
- [x] Benchmark: one person checked 30 of the 226 items. All 30 were clear and 25 gold answers were right; the five problems were fixed in v1.1.
- [~] Usefulness and framing of answers: manual reading of failed benchmark answers with fixed rules (`docs/benchmark.md`).

**Not yet checkable:**
- Whether free-text summaries are faithful to the underlying posts (the snapshot has headlines only).
- Sentiment-label quality beyond the 100-headline check (one labeller, headlines only).
- Whether a model passes on every warning (no reliable automatic check yet).
- Results on other clients and other models (one runner, one client trial, two models).

**Status words:** everything above is **tested** on samples, not **proven**. New data or models can show new failures. Each new failure gets a row here and a regression test in `tests/test_regressions.py`.
