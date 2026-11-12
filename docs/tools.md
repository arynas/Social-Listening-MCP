# MCP tools

The server (`slmcp/server.py`) uses the official MCP Python SDK (`mcp` 2.x, `MCPServer`) and exposes two tools. Both take the same three arguments and return the recap described in `docs/recap.md` as compact JSON text.

| Tool | Data | Extra |
|---|---|---|
| `news_recap(keyword, start_date, end_date)` | posts with platform `news` | — |
| `social_recap(keyword, start_date, end_date)` | every other platform | `top_posts` by engagement |

## Arguments
- `keyword`: the topic in 1 or 2 words (`banjir`, `harga beras`). Empty, or more than 4 words / 60 characters, is refused with a message that suggests a shorter keyword.
- `start_date`, `end_date`: `YYYY-MM-DD`, WIB days, both included. A word such as `kemarin` is refused; the error carries `relative_dates` with the exact dates, so the model can call again correctly. Also refused: start after end, end after "today", more than 366 days.

## What the model is told (tool descriptions)
Each description starts with a "today" line, made when the server starts from the config's frozen `today` (or the real WIB date when none is set):

> Today is Thursday 15 October 2026 (WIB). Use this date, not your own clock: kemarin = 2026-10-14, minggu ini = 2026-10-12..2026-10-15, minggu lalu = 2026-10-05..2026-10-11, bulan ini = 2026-10-01..2026-10-15.

Then: when to use each tool, the date format, short keywords, to use `relative_dates` for other time words, to report numbers exactly as returned (use `sentiment_total`, don't add up), to give only links from the result, and to tell the user every line of `must_tell_user`. The whole tool list is about 590 tokens (characters ÷ 4; it was about 460 before the today line and the `must_tell_user` sentence).

Note: without a frozen `today`, the date is taken once at server start. A server that runs past midnight keeps the old date in its descriptions until it restarts (the results themselves always use the current date).

## must_tell_user
The first field of every recap. It repeats `data_warnings` as plain sentences (capital letter, full stop), for example `"Source is news headlines only (no article text, no social media)."`. It is placed first so a model reads it before the numbers. `data_warnings` stays as it was.

## Errors
A refused call returns `{"error": "..."}` (plus `today`, `today_weekday` and `relative_dates` for date problems). It is a normal result, not a protocol error, so the model reads the message and can retry.

## Log
One JSON line per call on stderr (stdout is the MCP channel in stdio mode):

```json
{"time": "2026-11-12T01:47:22+00:00", "tool": "news_recap", "args": {"keyword": "banjir", "start_date": "2026-10-01", "end_date": "2026-10-14"}, "duration_ms": 52, "result_count": 248, "error": null}
```

`error` is `null`, `bad_keyword` or `bad_request`. Post text is never logged.

## Client trial
See `docs/client-trial.md`: 5 questions through OpenCode with Qwen3.6-27B, scored against the tool output.

## Run
```bash
pip install -e .
slmcp-server --config config.snapshot.example.yaml                              # stdio
slmcp-server --config config.snapshot.example.yaml --transport streamable-http  # http://127.0.0.1:8000/mcp
```

The config sets the data source, the frozen `today` and `sentiment_labels` (see `docs/config.md`). For the snapshot, download it first (README, "Data snapshot").
