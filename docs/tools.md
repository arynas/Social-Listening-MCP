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
When to use each tool, the date format, short keywords, to use `relative_dates` for words like "minggu lalu", to report numbers exactly as returned (use `sentiment_total`, don't add up), to give only links from the result, and to tell the user every item in `data_warnings`. The whole tool list is about 460 tokens (characters ÷ 4).

## Errors
A refused call returns `{"error": "..."}` (plus `today`, `today_weekday` and `relative_dates` for date problems). It is a normal result, not a protocol error, so the model reads the message and can retry.

## Log
One JSON line per call on stderr (stdout is the MCP channel in stdio mode):

```json
{"time": "2026-11-12T01:47:22+00:00", "tool": "news_recap", "args": {"keyword": "banjir", "start_date": "2026-10-01", "end_date": "2026-10-14"}, "duration_ms": 52, "result_count": 248, "error": null}
```

`error` is `null`, `bad_keyword` or `bad_request`. Post text is never logged.

## Run
```bash
pip install -e .
slmcp-server --config config.snapshot.example.yaml                              # stdio
slmcp-server --config config.snapshot.example.yaml --transport streamable-http  # http://127.0.0.1:8000/mcp
```

The config sets the data source, the frozen `today` and `sentiment_labels` (see `docs/config.md`). For the snapshot, download it first (README, "Data snapshot").
