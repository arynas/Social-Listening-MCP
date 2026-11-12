# MCP tools

The server (`slmcp/server.py`) uses the official MCP Python SDK (`mcp` 2.x, `MCPServer`) and exposes two tools. Both take the same arguments and return the recap described in `docs/recap.md` as compact JSON text.

| Tool | Data | Extra |
|---|---|---|
| `news_recap(keyword, period, start_date, end_date)` | posts with platform `news` | — |
| `social_recap(keyword, period, start_date, end_date)` | every other platform | `top_posts` by engagement |

## Arguments
- `keyword` (required): the topic in 1 or 2 words (`banjir`, `harga beras`). Empty, or more than 4 words / 60 characters, is refused with a message that suggests a shorter keyword.
- `period`: a relative period word. The exact dates are computed **in code** from "today" (frozen in the config for a snapshot), in WIB, with Monday–Sunday weeks:

  | Word (Indonesian / English) | With today = Thu 15 Oct 2026 |
  |---|---|
  | `hari ini` / `today` | 15 Oct |
  | `kemarin` / `yesterday` | 14 Oct |
  | `minggu ini`, `pekan ini` / `this week` | 12–15 Oct |
  | `minggu lalu`, `pekan lalu` / `last week` | 5–11 Oct |
  | `bulan ini` / `this month` | 1–15 Oct |
  | `bulan lalu` / `last month` | 1–30 Sep |
  | `7 hari terakhir` / `last 7 days` | 9–15 Oct |
  | `30 hari terakhir` / `last 30 days` | 16 Sep – 15 Oct |

  A period word given in `start_date` is accepted too. An unknown word is refused with the list of known words.
- `start_date`, `end_date`: for fixed dates the user named, `YYYY-MM-DD`, WIB days, both included. Give either `period` or both dates. Also refused: start after end, more than 366 days.

## Strict data range: no numbers for periods without data
The server knows the first and last day of its data (taken from the data itself; 1–14 Oct 2026 for the snapshot). If the asked period is **not fully inside** that range, the tool gives **no numbers at all** and suggests **no other dates**. This includes future dates and periods that are only partly inside the data (for example `minggu ini` = 12–15 Oct when the data ends on 14 Oct). The result is a normal result, not an error:

```json
{"must_tell_user": ["There is no data for 1–31 Dec 2026. The data covers 1–14 Oct 2026 only."],
 "data_available": false, "keyword": "pendidikan",
 "asked_period": {"start": "2026-12-01", "end": "2026-12-31", "period": null},
 "data_covers": {"start": "2026-10-01", "end": "2026-10-14"},
 "missing": [["2026-12-01", "2026-12-31"]], "today": "2026-10-15", "today_weekday": "Kamis"}
```

For a partly covered period the sentence names the missing part: `"There is no data for 15 Oct 2026, so no numbers are given for 12–15 Oct 2026. The data covers 1–14 Oct 2026 only."` A source without posts of the asked kind says so (`"... this data source has no social media posts."`).

Why refuse a partly covered period instead of giving numbers for the covered days? Because numbers for "this week" that silently stop on Wednesday read like numbers for the whole week. The user can still ask for the covered days by name.

Days *inside* the range that happen to have no posts count as 0 (the range is first to last day of the whole data source, not per platform). Adapters that don't hold their posts in memory (for example a live feed) have no known range; for them only the future-date check applies.

Every result has `data_available` (`true` with numbers, `false` without).

## What the model is told (tool descriptions)
When to use each tool, short keywords, to pass a period word for relative periods, to report numbers exactly as returned (use `sentiment_total`, don't add up), to give only links from the result, to tell the user every line of `must_tell_user`, and:

> For relative periods, pass the word (e.g. period="minggu lalu"), not dates. If the result says data_available false, tell the user the data is not available for that period, in a polite way, and do not answer with another period.

The descriptions contain no computed dates. The whole tool list is about 670 tokens (characters ÷ 4).

## must_tell_user
The first field of every result. In a recap it repeats `data_warnings` as plain sentences (capital letter, full stop), for example `"Source is news headlines only (no article text, no social media)."`. In a no-data result it is the "no data" sentence above. `data_warnings` stays as it was.

## Errors
A request the tool can't read returns `{"error": "...", "today": ..., "today_weekday": ...}`: empty or long keyword, a date that isn't `YYYY-MM-DD`, an unknown period word, no period and no dates, start after end, more than 366 days. It is a normal result, not a protocol error, so the model reads the message and can retry. Errors carry no list of other dates.

## Log
One JSON line per call on stderr (stdout is the MCP channel in stdio mode):

```json
{"time": "2026-11-12T01:47:22+00:00", "tool": "news_recap", "args": {"keyword": "banjir", "period": null, "start_date": "2026-10-01", "end_date": "2026-10-14"}, "duration_ms": 52, "result_count": 248, "error": null}
```

`error` is `null`, `no_data` (period outside the data), `bad_keyword` or `bad_request`. Post text is never logged.

## Client trial
See `docs/client-trial.md`: 5 questions through OpenCode with Qwen3.6-27B, scored against the tool output.

## Run
```bash
pip install -e .
slmcp-server --config config.snapshot.example.yaml                              # stdio
slmcp-server --config config.snapshot.example.yaml --transport streamable-http  # http://127.0.0.1:8000/mcp
```

The config sets the data source, the frozen `today` and `sentiment_labels` (see `docs/config.md`). For the snapshot, download it first (README, "Data snapshot").
