# Recap output

`slmcp/recap.py` turns the posts an adapter returns into a small, sourced summary. Everything is computed by code; no model is used. `news_recap` and `social_recap` are plain Python functions with the shape of the future MCP tools.

```python
from slmcp.adapters.csv_json import FileAdapter
from slmcp.config import load_config
from slmcp.recap import from_config, news_recap

config = load_config("config.snapshot.example.yaml")   # snapshot, frozen today = 2026-10-15
adapter = FileAdapter(config.data_source.paths[0])
result = news_recap(adapter, "banjir", "2026-10-01", "2026-10-14", **from_config(config))
```

## Fields
| Field | Meaning |
|---|---|
| `must_tell_user` | First field: `data_warnings` as plain sentences, for the model to pass on to the user |
| `data_available` | `true` (tools only; `false` results have no numbers) |
| `keyword`, `start_date`, `end_date` | The request (days in WIB, both inclusive) |
| `today`, `today_weekday` | The "today" used (frozen in the config for a fixed snapshot) and its weekday in Indonesian |
| `relative_dates` | Exact dates for `hari ini`, `kemarin`, `minggu ini`, `minggu lalu`, `7 hari terakhir`, `30 hari terakhir`, `bulan ini`, `bulan lalu`, counted from `today` |
| `total` | Number of matching posts |
| `sentiment_total` | `counts` and `pct` for positive, negative, neutral and unlabelled |
| `trend` | `unit` (`day` or `week`) and `points`: one per day, or per Monday–Sunday week when the range is longer than 30 days (at most 30 points). Empty days are included |
| `top_sources`, `top_platforms` | Top 5 by count |
| `top_posts` | Social only: top 5 posts by engagement |
| `samples` | Up to 10 example posts (date, source, platform, text, url, sentiment, engagement if any). Links come only from the data |
| `data_warnings` | Plain-English warnings (see below) |
| `output_tokens_estimate` | Size of this output in tokens, estimated as characters ÷ 4 of the compact JSON |

`news_recap` and `social_recap` (the tool functions) also take `period`, a relative period word computed in code, and add `data_available: true` (and `period` when given). A period not fully inside the data returns a no-data result without numbers (see `docs/tools.md`, "Strict data range"). A request that can't be read (bad date, start after end, range over 366 days) returns `{"error": ..., "today": ..., "today_weekday": ...}`. Neither carries other dates to try.

## Rules
- **Days and weeks are WIB.** A week is Monday to Sunday; the first and last week are cut to the requested range.
- **Percentages add up to exactly 100.0.** One decimal, largest-remainder rounding (ties go to positive, then negative, neutral, unlabelled).
- **Posts without sentiment** are counted as `unlabelled`, included in the percentages, and named in a warning.
- **Ties in top lists** are broken by name, A to Z, so the output is the same on every run.
- **Samples:** social posts by engagement (highest first); news spread evenly over the range.
- **Size limit:** about 2,000 tokens. If the output is bigger, sample texts get shorter, then samples and top posts are dropped. Counts, sentiment and trend are never cut.
- **news_recap** uses posts with platform `news`; **social_recap** uses all other platforms.

## Warnings
- no posts found for this keyword and date range
- the range is outside the data, or only partly covered (with the data's first and last day)
- the range ends after today
- sentiment labels are rough model output (when the config says `sentiment_labels: model`)
- some posts have no sentiment label
- source is news headlines only (news_recap)
- this data may have no social media posts (social_recap with no results)

## Config
`today` (YYYY-MM-DD) freezes "today" for a fixed snapshot. `data_source.sentiment_labels` (`model` or `human`) says who labelled the sentiment. See `config.snapshot.example.yaml`: the published snapshot ends on 14 Oct 2026, so `today` is 2026-10-15.
