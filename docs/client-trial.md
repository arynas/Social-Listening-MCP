# Client trial: 5 questions through an MCP client

**Date:** 12 Nov 2026 · **Client:** OpenCode v2.2.0 (`opencode run`, a fresh session per question) · **Model:** Qwen3.6-27B (open weights, 4-bit, served locally with llama.cpp), temperature 0, thinking off · **Server:** `slmcp-server --config config.snapshot.example.yaml` (snapshot `data-2026-10-v1`, frozen today 2026-10-15) · **Tool descriptions:** as in v0.0.1, not tuned for this trial.

Each answer was checked against the server's JSON log and the tool output.

| # | Question | Tool called? | Arguments right? | Numbers = tool's? | Links only from tool? | Warnings told? | Time |
|---|---|---|---|---|---|---|---|
| a | Bagaimana sentimen berita tentang banjir dari 1 sampai 14 Oktober 2026? | yes (after first fetching a web page itself) | yes (`banjir`, 1–14 Oct) | yes (248; 89/138/21; 35.9/55.6/8.5 %; all 14 trend points; top 5 sources) | no links given; all 5 quoted headlines are in the tool output | yes (model labels, headlines only) | 99 s |
| b | Berapa jumlah berita tentang gempa minggu lalu? | **no** | – | – (answer: "no access to news") | – | – | 19 s |
| c | Apa saja sumber berita teratas tentang kesehatan kemarin? Sertakan 3 contoh judul dengan link. | **no** | – | – | **no**: 6 generic site links, not from the data | – | 29 s |
| d | Bandingkan jumlah berita harga beras minggu ini dan minggu lalu. | yes, 4 calls | first 2 wrong (real-clock November dates → "future" error), then right (12–15 Oct and 5–11 Oct) | yes (16 vs 4; 87.5/12.5/0 and 75/25/0 %; trend; sources) | no links given; all 6 quoted headlines are in the tool output | **no** (none of the 3 warnings, incl. "this week is only partly covered") | 119 s |
| e | Bagaimana sentimen berita tentang pendidikan bulan Desember 2026? | yes | yes (`pendidikan`, 1–31 Dec) | – (tool refused: future date) | – | answered correctly that December 2026 has no data yet | 30 s |

Correct answers from the tools (same config): b = 143 news headlines about `gempa` on 5–11 Oct; c = 77 headlines about `kesehatan` on 14 Oct, top source jawapos.com (48).

**Score:** the tool was used in 3 of 5 questions. When it was used, every number and every quoted headline matched the tool output (0 invented numbers). Warnings were passed on in 1 of 2 answers that had them.

## Findings
1. **The model sometimes didn't use the tool (b, c).** It said it had no news access and, in c, gave generic links instead. The server log shows no call. In this OpenCode version, MCP tools are reached through a code-execution tool, so the model has to find `tools["social-listening"]` itself; in a, d and e it found it, in b and c it did not.
2. **The client let the model fetch the web.** In a, the model first fetched a Google News feed through the code-execution tool before using our tool. For a fair test or a demo, the client's network access should be off.
3. **"Today" came from the real clock first.** In d the model computed "this week" from the computer's date (November) and got a "future" error; it then used the frozen dates from `relative_dates` in the error and answered correctly. The tool descriptions don't say what "today" is.
4. **Warnings were dropped in a multi-call answer (d).** The tool returned 3 warnings; none reached the user.
5. **When the tool was used, the numbers were right.** No invented or wrong numbers, and no invented headlines, in a, d and e.

## Possible fixes (not applied in this step)
- Findings 1–2: try a client that lists MCP tools directly to the model (for example Open WebUI or MCP Inspector for a no-model check), or an OpenCode agent with a short system prompt that names the tool and turns off code execution and network access.
- Finding 3: put the frozen "today" into the tool descriptions when the server starts (e.g. "today is 2026-10-15").
- Finding 4: put the warnings first in the result, or add a short `must_tell_user` field.
- Re-run the same 5 questions after any fix, and later as part of the benchmark (S0-6).

## Re-run after two fixes (12 Nov 2026, same day)

**Fixes (server only):**
- **A.** Each tool description now starts with a "today" line from the config, with the exact dates of the common time words (`Today is Thursday 15 October 2026 (WIB). Use this date, not your own clock: kemarin = 2026-10-14, minggu ini = 2026-10-12..2026-10-15, minggu lalu = 2026-10-05..2026-10-11, bulan ini = 2026-10-01..2026-10-15.`).
- **B.** Each recap starts with `must_tell_user`, the warnings as plain sentences, and the descriptions say to tell the user every line of it.

The tool list grew from about 460 to about 590 tokens; a recap grew by about 50 tokens.

**Setup:** the same as above (OpenCode v2.2.0, fresh session per question, Qwen3.6-27B, temperature 0, thinking off, same 5 questions, same wording). Two runs before (the first run is the table above) and two runs after.

| | Before (2 runs) | After (2 runs) |
|---|---|---|
| Tool used | 7 of 10 (b skipped twice, c once) | 8 of 10 (b skipped twice) |
| Dates right on the first call (questions where the tool was used) | 4 of 7 (November dates from the real clock in c and d) | 6 of 8 (d run 1 still started with November dates; e run 2, see below) |
| Numbers equal to the tool output | all, in every answer with tool data | all, in every answer with tool data |
| Links only from the tool | 1 of 2 answers with links (run 1 c gave 6 generic links without calling the tool) | 2 of 2 answers with links (c, both runs; 3 links each, all from the tool) |
| Warnings told (answers a–d with tool data) | 3 of 5 (d dropped them in both runs) | **6 of 6** (incl. "this week is only partly covered" in d, both runs) |
| Answered a different period than asked | 0 | **1** (e run 2: said December has no data, then gave October numbers) |
| Time per question, median (range) | 50 s (18–119) | 65 s (18–123) |

**What changed:**
1. **Warnings (fix B) worked for this model:** every answer with tool data passed on the warnings, including the partly covered week in d, which was dropped in both runs before.
2. **Dates (fix A) helped but did not solve it:** fewer November first calls (c is now right in both runs). In d run 1 the model still began with November dates and then recovered from the error.
3. **A new wrong-period answer (e run 2):** knowing today is 15 October, the model did not ask the tool for December at all. It called October instead and answered with October numbers, after saying December has no data. Before the fix it asked for December, got the "future" error and stopped there.
4. **Tool not used in b is unchanged** (both runs): this is the client and model side (finding 1), not the server.
5. Numbers and links stayed exact.

A second, hosted model was also run with the same setup. On that model fix A made relative dates **worse** (it computed "minggu lalu" itself and got the wrong week, with no error to correct it). Those runs are kept as private notes. So the today line is not a fix for every model; a possible next step is to let the tools accept a phrase such as `minggu lalu` and resolve it in code.

Small sample: 5 questions, 2 runs before and 2 after, one model.

## Re-run 2: strict data range and period words (12 Nov 2026, same day)

**Change (replaces fix A, keeps fix B):** the tools accept a period word (`period="minggu lalu"`) and compute the dates in code. A period not fully inside the data (1–14 Oct for the snapshot) returns `data_available: false`, no numbers, no other dates, and a polite `must_tell_user` line such as "There is no data for 1–31 Dec 2026. The data covers 1–14 Oct 2026 only." The descriptions no longer list computed dates. See `docs/tools.md`, "Strict data range".

With this rule the right answer to d changes: "minggu ini" is 12–15 Oct and 15 Oct has no data, so this week is "not available"; last week is 16.

Same setup, same 5 questions, 2 runs (Qwen3.6-27B):

| | Before (2 runs) | After fix A + B | After strict range + B |
|---|---|---|---|
| Tool used | 7/10 | 8/10 | 8/10 (b skipped twice, unchanged) |
| Passed a period word | – | – | 0 of 8 (it always passed dates) |
| Dates right on the first call | 4/7 | 6/8 | 4/8 (November dates first in c and d; the tool now answers "no data" and the model then moved to October dates) |
| Answer about the asked period | 7/7 | 7/8 | 7/8 (d run 2 compared 1–7 and 8–14 Oct instead of this week and last week) |
| Numbers equal to the tool output | all | all | all |
| Warnings told (a–d with tool data) | 3/5 | 6/6 | 6/6 |
| December question: said "no data" politely, gave no other period | 2/2 | 1/2 | **2/2** (offered October only as a question) |
| Time per question, median | 50 s | 65 s | 72 s |

**What changed:** the December question is now handled well in both runs, and no answer gave numbers for a period without data. This model still ignores the `period` argument and starts from its own clock, so the first call often lands on November; the "no data" result stops it from reporting anything for November, and it then finds the October dates. In d run 1 it passed 12–14 Oct itself for "this week" and said the data ends on 14 Oct.

On the second (hosted) model this change worked better than fix A: it passed period words in most calls, and "last week" was right in both runs again. It still answered October once for the December question. Details are private notes.

Small sample: 5 questions, 2 runs per setup, one model shown.
