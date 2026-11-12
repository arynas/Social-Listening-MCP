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
