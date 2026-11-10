# Datasheet: public news snapshot (DRAFT, pilot done)

Status: **one-day pilot collected (1 October 2026, WIB)** from GDELT raw files on 10 Nov 2026. The full period is not collected yet.

## Why raw files and not the API
The GDELT DOC 2.0 API answered HTTP 429 (rate limit) on 10 Nov 2026 to every test request, from two different networks (this Mac in the afternoon and evening, and a separate Linux server), even with 60 s–4 min gaps. The raw files on data.gdeltproject.org are a different host with no such limit, have the same licence, and go back further than the API's 3-month window. `scripts/build_snapshot.py` (API) stays in the repo for when the API works again.

| Item | Value |
|---|---|
| Source | GDELT 2.0 translingual GKG raw files: `https://data.gdeltproject.org/gdeltv2/YYYYMMDDHHMMSS.translation.gkg.csv.zip`, one per 15 minutes (https://www.gdeltproject.org/) |
| Tool | `scripts/build_snapshot_gkg.py` |
| Pilot period | 1 Oct 2026 (WIB day = 30 Sep 17:00 – 1 Oct 17:00 UTC), 96 files, none missing |
| Filter | Source language Indonesian (`srclc:ind` in TranslationInfo) and a page title present (`<PAGE_TITLE>` in Extras) |
| What is stored | Only what GDELT publishes: page title (`text`), URL, domain (`source`), GDELT timestamp (`date`, UTC, 15-minute steps), `language` = `ind`, and which of the 10 keywords appear in the title (`keywords`). `platform` is always `news`. No article bodies |
| Topics tagged | banjir, harga beras, harga cabai, gempa, kemacetan, sampah, pendidikan, kesehatan, pariwisata, transportasi. All Indonesian rows are kept, not only the ones with a keyword, so other questions can be asked later |
| Format | JSONL, one file per WIB month (`posts-YYYY-MM.jsonl`), Post schema, deduplicated by URL |
| Checks | `manifest.json`: sha256 and rows per file, rows per day, rows per keyword, duplicates removed, rows skipped, missing files, bytes downloaded |
| Licence | GDELT: free use and redistribution, with a citation to the GDELT Project and a link to https://www.gdeltproject.org/ |
| Citation | "Data from the GDELT Project, https://www.gdeltproject.org/" |
| Sentiment | None yet (needs a decision on the model and a hand check) |

## Pilot numbers (1 Oct 2026)
- Download: 96 files, **887 MB**, in **732 s (12.2 min)** with a 2 s pause between files. Zips are deleted after parsing; the kept output is **2.7 MB**.
- Rows: 3,756 Indonesian rows read, **2 skipped** (no title; both image-gallery pages), **0 duplicates**, **3,754 unique** posts. Loaded through `FileAdapter`: 3,754 posts, 0 skipped.
- Rows per WIB hour: 24 to 286.
- Domains: 27, led by antaranews.com (651), liputan6.com (450), jawapos.com (361), viva.co.id (324), koran-jakarta.com (307), jpnn.com (269), okezone.com (221), republika.co.id (216), rri.co.id (168), sindonews.com (148).
- Keyword matches in titles (unique posts): kesehatan 43, banjir 18, pendidikan 14, sampah 12, gempa 11, harga beras 5, harga cabai 4, pariwisata 4, kemacetan 2, transportasi 0. In total 104 posts (2.8%) match at least one keyword.
- Output sha256 (`posts-2026-10.jsonl`, pilot): `98cac4c6fa7120889f01a359d28ec537f0cb3e312012839a30d51544360822cc`.

## Estimate for 1 Sep – 31 Oct 2026 (61 days)
- Download: about **54 GB** (887 MB × 61), parsed one file at a time and deleted, so disk use stays small.
- Time: about **12.5 hours** at the pilot speed (one file at a time, 2 s pause).
- Output: about 230,000 posts, about 165 MB of JSONL; about 6,300 keyword matches if the pilot rate holds.

## Limits
- Coverage is what GDELT monitors: 27 Indonesian domains on the pilot day, not every outlet. Large national sites dominate.
- Headlines only: short text, so keyword search misses articles whose title uses other words. Some keywords (kemacetan, transportasi) are rare in titles.
- The timestamp is the 15-minute GDELT file time (when GDELT processed the page), not the exact publication time.
- The translingual files are GDELT's machine-translation stream; we keep the original Indonesian title, not the translation.
- The language label comes from GDELT (`srclc:ind`); we didn't check it row by row.

## Personal data
Titles can name public people (officials, celebrities) and sometimes private people involved in news events. We store only what GDELT already publishes, keep no profiles, and don't link names across sources. Benchmark questions should be about topics, not private people.
