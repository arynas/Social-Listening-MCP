# Regression tests (S0-8b)

Every failure mode in the failure table (S0-8a) has a test that fails if the fix is undone: `tests/test_regressions.py`, one test per failure (`test_f1_…` to `test_f12_…`). A last test checks that all 12 are there. Failures that come from the model (not the server) are guarded on the scoring side, so the benchmark catches them when they come back with a new model or change.

| # | Failure | Fix | pytest | Benchmark items |
|---|---|---|---|---|
| 1 | Wrong sum (model adds counts itself) | `sentiment_total` computed by code; description says not to add up | `test_f1_wrong_sum_…` | `sentiment` (12 pairs) |
| 2 | Invented links | links only from the data; scorer needs every link in the tool output | `test_f2_invented_links_…` | `links` (8 pairs) |
| 3 | "Minggu lalu" wrong period | period words resolved in code (Mon–Sun, WIB) | `test_f3_minggu_lalu_…` | `count_relative`, `compare`, `sentiment` with `minggu lalu` |
| 4 | Tool not used | client/model side; the scorer reports `tool_used` and fails answerable items without it | `test_f4_tool_not_used_…` | every answerable item |
| 5 | Keyword too long | input check (> 4 words / 60 characters) with a short suggestion | `test_f5_keyword_too_long_…` | `odd_keyword` (long phrase) |
| 6 | Warnings not told | `must_tell_user` first; scorer checks `warnings_told` | `test_f6_warnings_…` | every item with data |
| 7 | Context overflow | output kept under about 2,000 tokens; runner records `max_prompt_tokens` | `test_f7_context_overflow_…` | all items (context fit in `compare`) |
| 8 | Shared-name confusion | not applicable to the public data (no person lists) | `test_f8_…` (guard only) | – |
| 9 | Real-clock date instead of frozen date | periods outside the data get no numbers | `test_f9_real_clock_…` | `abstain_relative`, `abstain_future` |
| 10 | Different period than asked | no other dates suggested; scorer fails answers that fetch another period | `test_f10_another_period_…` | `abstain_*`, `long_range`, `out_of_scope` |
| 11 | Data gaps not mentioned | partly covered periods refused, missing part named | `test_f11_partly_covered_…` | `abstain_relative` (`minggu ini`), `long_range` |
| 12 | Invented split of a tool number | period words in code; scorer flags numbers not in the tool output | `test_f12_invented_split_…` | `count_relative` (`minggu lalu`) |

Run: `pytest tests/test_regressions.py -v`.
