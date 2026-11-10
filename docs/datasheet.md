# Datasheet: public news snapshot (DRAFT)

Status: **draft, no data collected yet.** The 7-day pilot (1–7 October 2026) could not run on 10 Nov 2026 because the GDELT API kept answering HTTP 429 (rate limit). The tool to collect it is ready: `scripts/build_snapshot.py`.

| Item | Plan |
|---|---|
| Source | GDELT DOC 2.0 API, article list mode (https://www.gdeltproject.org/) |
| Period | Pilot: 1–7 Oct 2026 (WIB days). Full snapshot: 2–3 months, to be decided, collected while still inside GDELT's 3-month window |
| Topics | About 10 neutral Indonesian keywords: banjir, harga beras, harga cabai, gempa, kemacetan, sampah, pendidikan, kesehatan, pariwisata, transportasi |
| What is stored | Only what GDELT publishes: headline (`text`), URL, domain (`source`), time (`date`, UTC), language, the keyword(s) that found it. `platform` is always `news`. No article bodies |
| Format | JSONL, one file per month (`posts-YYYY-MM.jsonl`), in the Post schema, deduplicated by URL |
| Checks | `manifest.json`: sha256 and row count per file, rows per day, rows fetched per keyword, duplicates removed, rows skipped, windows that may be cut off at 250 results |
| Licence | GDELT: free use and redistribution, with a citation to the GDELT Project and a link to https://www.gdeltproject.org/ |
| Citation | "Data from the GDELT Project, https://www.gdeltproject.org/" |
| Sentiment | None yet. Labelling with a free model needs a decision first |

## Limits
- GDELT finds what it monitors; it is not every Indonesian outlet, and coverage differs by site.
- Headlines only: the text is short, so keyword search misses articles whose headline uses other words.
- `seendate` is when GDELT saw the article, which can be later than publication.
- The 250-per-call limit is handled by splitting windows; a 1-hour window that still returns 250 is listed as possibly cut off.
- The language filter result is not yet confirmed (see `docs/data-sources.md`).

## Personal data
Headlines can name public people (officials, celebrities) and sometimes private people involved in news events. We store only what GDELT already publishes, keep no profiles, and don't link names across sources. Benchmark questions should be about topics, not private people.

## Known gaps (to fill after the pilot)
- Rows per day and per keyword, duplicates, skipped rows.
- Time needed per day of data, and an estimate for 3 months.
