# Datasheet: public news snapshot (DRAFT)

Status: **two-week snapshot collected (1–14 October 2026, WIB days)** from GDELT raw files on 11 Nov 2026, after a one-day pilot on 10 Nov. Sentiment labels were added on 11 Nov 2026 (see below); they are model-assigned; a blind hand check of 100 rows found 63% agreement (see below).

## Why raw files and not the API
The GDELT DOC 2.0 API answered HTTP 429 (rate limit) on 10 Nov 2026 to every test request, from two different networks (this Mac in the afternoon and evening, and a separate Linux server), even with 60 s–4 min gaps. The raw files on data.gdeltproject.org are a different host with no such limit, have the same licence, and go back further than the API's 3-month window. `scripts/build_snapshot.py` (API) stays in the repo for when the API works again.

| Item | Value |
|---|---|
| Source | GDELT 2.0 translingual GKG raw files: `https://data.gdeltproject.org/gdeltv2/YYYYMMDDHHMMSS.translation.gkg.csv.zip`, one per 15 minutes (https://www.gdeltproject.org/) |
| Tool | `scripts/build_snapshot_gkg.py` |
| Period | 1–14 Oct 2026 (WIB days = 30 Sep 17:00 – 14 Oct 17:00 UTC), 1,344 files, none missing. Pilot: 1 Oct only |
| Filter | Source language Indonesian (`srclc:ind` in TranslationInfo) and a page title present (`<PAGE_TITLE>` in Extras) |
| What is stored | Only what GDELT publishes: page title (`text`), URL, domain (`source`), GDELT timestamp (`date`, UTC, 15-minute steps), `language` = `ind`, and which of the 10 keywords appear in the title (`keywords`). `platform` is always `news`. No article bodies |
| Topics tagged | banjir, harga beras, harga cabai, gempa, kemacetan, sampah, pendidikan, kesehatan, pariwisata, transportasi. All Indonesian rows are kept, not only the ones with a keyword, so other questions can be asked later |
| Format | JSONL, one file per WIB month (`posts-YYYY-MM.jsonl`), Post schema, deduplicated by URL |
| Checks | `manifest.json`: sha256 and rows per file, rows per day, rows per keyword, duplicates removed, rows skipped, missing files, bytes downloaded |
| Licence | GDELT: free use and redistribution, with a citation to the GDELT Project and a link to https://www.gdeltproject.org/ |
| Citation | "Data from the GDELT Project, https://www.gdeltproject.org/" |
| Sentiment | Qwen3.6-27B (open weights, Q4, llama.cpp), prompt v3, in a separate labelled file; model-assigned; blind hand check of 100 rows: 63/100 agree, Cohen's kappa 0.44 |

## Pilot numbers (1 Oct 2026)
- Download: 96 files, **887 MB**, in **732 s (12.2 min)** with a 2 s pause between files. Zips are deleted after parsing; the kept output is **2.7 MB**.
- Rows: 3,756 Indonesian rows read, **2 skipped** (no title; both image-gallery pages), **0 duplicates**, **3,754 unique** posts. Loaded through `FileAdapter`: 3,754 posts, 0 skipped.
- Rows per WIB hour: 24 to 286.
- Domains: 27, led by antaranews.com (651), liputan6.com (450), jawapos.com (361), viva.co.id (324), koran-jakarta.com (307), jpnn.com (269), okezone.com (221), republika.co.id (216), rri.co.id (168), sindonews.com (148).
- Keyword matches in titles (unique posts): kesehatan 43, banjir 18, pendidikan 14, sampah 12, gempa 11, harga beras 5, harga cabai 4, pariwisata 4, kemacetan 2, transportasi 0. In total 104 posts (2.8%) match at least one keyword.
- Output sha256 (`posts-2026-10.jsonl`, pilot): `98cac4c6fa7120889f01a359d28ec537f0cb3e312012839a30d51544360822cc`.

## Snapshot numbers (1–14 Oct 2026)
- Collected on a Linux server (one file at a time, 2 s pause), 11 Nov 2026 00:00–01:38 WIB: **1,344 files, 0 missing, 10.8 GB downloaded, 5,898 s (98 min)**. Zips deleted after parsing. Kept output: `posts-2026-10.jsonl`, 43,422 rows, 16.7 MB.
- Rows: 43,460 Indonesian rows read, **38 skipped** (no title; image-gallery pages), **0 duplicates**, **43,422 unique** posts. Loaded through `FileAdapter`: 43,422 posts, 0 skipped.
- Posts per WIB day: 1 Oct 3,754 · 2 Oct 3,419 · 3 Oct 2,077 · 4 Oct 2,066 · 5 Oct 3,473 · 6 Oct 3,691 · 7 Oct 3,666 · 8 Oct 3,384 · 9 Oct 3,436 · 10 Oct 2,023 · 11 Oct 2,187 · 12 Oct 3,189 · 13 Oct 3,451 · 14 Oct 3,606. Weekends (3–4 and 10–11 Oct) have about 40% fewer.
- Domains: 46. Top 10: antaranews.com 6,603, jawapos.com 5,167, liputan6.com 5,006, viva.co.id 4,304, koran-jakarta.com 3,681, jpnn.com 3,230, republika.co.id 2,276, okezone.com 2,227, sindonews.com 1,781, harianjogja.com 1,371.
- Keyword matches in titles (unique posts): kesehatan 807, pendidikan 282, banjir 248, gempa 245, sampah 152, pariwisata 73, harga cabai 57, harga beras 29, kemacetan 21, transportasi 0. In total 1,894 posts (4.4%) match at least one keyword.
- sha256 of `posts-2026-10.jsonl`: `4b67c80e8350059bce03229eb11b8ff0c2aa7cc43864194f026d0d4e57410ea1` (checked on the Mac against `manifest.json`: match).

## Sentiment labels (11 Nov 2026)
- Model: Qwen3.6-27B (open weights, 4-bit GGUF) on llama.cpp, temperature 0, thinking off, 20 headlines per call. Labels positif / negatif / netral, stored as `positive` / `negative` / `neutral` in `posts-2026-10.labelled.jsonl`. The unlabelled `posts-2026-10.jsonl` is unchanged (sha256 checked); labels are also kept in `labels.jsonl` keyed by URL, with model and prompt version. The labelled file has its own sha256 in `manifest.json` (`labelled_files`).
- Prompt v3: numbered JSON answer, forced into compact form by a llama.cpp grammar. Pilot on 200 headlines: run twice, 0 labels changed; 97% (194/200) agree with the free-form v1 prompt; 1.6× faster than v1.
- Full run: 43,422 headlines, 2,172 calls, **0 invalid answers, 0 retries, 0 unlabelled**, 31,884 s (8.9 h) on one GPU server.
- Label mix: positive 15,879 (36.6%), neutral 14,558 (33.5%), negative 12,985 (29.9%). Per day, positive 34–42%, neutral 29–40%, negative 24–37%.
- Hand check: on 11 Nov 2026 one human annotator labelled 100 rows blind (seed 7, about 33 per model label, spread over the 14 days). **63 of 100 agree with the model (Cohen's kappa 0.44; rough 95% range 53–72%).** The main differences: the model labels a response to a bad event (an arrest, aid, a sanction) as negative because of the event, where the annotator judges the response; and routine service or business news is positive for the model but neutral for the annotator. Treat the labels as rough model output, not ground truth. Anyone can repeat the check: `scripts/handcheck.py make` produces a blind sheet and `scripts/handcheck.py compare` scores it. Headline-only labels miss context in the article body.

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
