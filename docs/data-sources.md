# Public data sources (Indonesia)

Checked on **10 November 2026**. "Checked" means I opened the URL and looked at the response; it is not legal advice. Where the terms are unclear, the table says so.

| # | Source | URL | Licence / terms | Size | Date range | Platform | Redistribute? | What I checked |
|---|---|---|---|---|---|---|---|---|
| 1 | ANTARA News, latest | https://www.antaranews.com/rss/terkini.xml | Feed says "Copyright 2026, ANTARA News". No separate feed licence found. | 50 items | about the last 1–2 hours | news | **No** (article text is the publisher's) | HTTP 200, RSS 2.0, 50 items dated 10 Nov 14:22–15:35 WIB; our adapter read 50, skipped 0 |
| 2 | Tempo, nasional | https://rss.tempo.co/nasional | No licence in the feed; terms not checked in detail. Treat as publisher's copyright. | 50 items | 30 Aug – 8 Nov 2026 | news | **No** | HTTP 200, RSS 2.0 (ISO-8859-15), 50 items; adapter read 50, skipped 0 |
| 3 | CNN Indonesia, nasional | https://www.cnnindonesia.com/nasional/rss | No licence in the feed; terms not checked in detail. Publisher's copyright. | 100 items | about the last 24 hours | news | **No** | HTTP 200, RSS 2.0, 100 items dated 9 Nov 16:44 – 10 Nov 15:32 WIB; adapter read 100, skipped 0 |
| 4 | detikNews, berita | https://news.detik.com/berita/rss | No licence in the feed; terms not checked in detail. Publisher's copyright. | 100 items | about the last 10 hours | news | **No** | HTTP 200, RSS 2.0, 100 items dated 10 Nov 06:03–15:28 WIB; adapter read 100, skipped 0 |
| 5 | Republika | https://www.republika.co.id/rss | No licence in the feed; terms not checked in detail. Publisher's copyright. | 15 items | about the last hour | news | **No** | HTTP 200, RSS 2.0, 15 items; adapter read 15, skipped 0 |
| 6 | GDELT Project | https://www.gdeltproject.org/ (API: https://api.gdeltproject.org/api/v2/doc/doc) | "Unlimited and unrestricted use ... without fee"; may be redistributed, with a citation of GDELT (about page). | very large (global) | 2015 – now (DOC API: recent months) | news (links + metadata, not full text) | **Yes**, with citation | About page read (terms quoted above). A test API call returned **HTTP 429** (rate limit), so the API itself is not yet verified |
| 7 | IndoNLU (SmSA sentiment and others), Hugging Face | https://huggingface.co/datasets/indonlp/indonlu | Hugging Face card says **MIT**. The original papers' data terms were not checked. | 10K–100K rows (card) | **no dates** | reviews/comments (other) | Probably yes (MIT on the card) | HF API: not gated, license tag `mit`. No timestamps, so it can't serve date-range questions; useful for sentiment checks |
| 8 | id_newspapers_2018, Hugging Face | https://huggingface.co/datasets/indonesian-nlp/id_newspapers_2018 | Hugging Face card says **CC BY 4.0**, but the articles were crawled from news sites, so the real rights are unclear. | 100K–1M rows (card) | 2018 | news | **Unclear** | HF API: not gated, license tag `cc-by-4.0`. Content not downloaded |

## GDELT: terms and limits (re-checked 10 Nov 2026)
Terms, from https://www.gdeltproject.org/about.html ("Using GDELT" and "Redistributing GDELT"):
> "all datasets released by the GDELT Project are available for unlimited and unrestricted use for any academic, commercial, or governmental use of any kind without fee."
> "You may redistribute, rehost, republish, and mirror any of the GDELT datasets in any form. However, any use or redistribution of the data must include a citation to the GDELT Project and a link to this website (https://www.gdeltproject.org/)."

DOC 2.0 API (https://api.gdeltproject.org/api/v2/doc/doc; documented at https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/):
- Article list mode (`mode=artlist`, `format=json`) gives per article: `url`, `title`, `seendate` (UTC), `domain`, `language`, `sourcecountry`. No article body. That is all we store.
- **History: a rolling window of the last 3 months only.** Older periods need GDELT's raw files or BigQuery. So a 3-month snapshot must be collected while the period is still inside the window, then frozen.
- **Records per call: 75 by default, at most 250** (`maxrecords`). `scripts/build_snapshot.py` splits a window in half when it gets 250 back, down to 1 hour.
- **Rate limit: "one every 5 seconds"** (the API's own 429 message). In practice on 10 Nov it answered 429 even with 7–60 s pauses between single requests, so the real limit for us is stricter or shared per network.
- Language filter: `sourcelang:indonesian` was accepted (HTTP 200) but returned no articles for `banjir` on 1 Oct 2026 in my one successful test. Not yet clear whether the filter value, the coverage or soft throttling caused this.

## GDELT raw files (used for the snapshot)
- Published snapshot: GitHub Release `data-2026-10-v1` (1–14 Oct 2026, 43,422 headlines). Anyone who uses or shares it must cite: "Data from the GDELT Project, https://www.gdeltproject.org/".
- Index: https://data.gdeltproject.org/gdeltv2/lastupdate-translation.txt (and `masterfilelist-translation.txt` for the full list). Files: `https://data.gdeltproject.org/gdeltv2/YYYYMMDDHHMMSS.translation.gkg.csv.zip`, one every 15 minutes.
- Checked on 10 Nov 2026: the host answers normally (no rate limit seen), files for 1 Oct 2026 exist; each is 5–12 MB zipped, tab-separated, 27 columns (GKG 2.1). Column 2 is the date, 4 the domain, 5 the URL, 26 TranslationInfo (`srclc:ind` = Indonesian source), 27 Extras with `<PAGE_TITLE>`. Indonesian rows are about 3% of a file (about 40 rows per 15 minutes) and all had a title.
- Same terms as all GDELT datasets (quoted above): use and redistribution allowed with a citation and a link.
- Unlike the API, the raw files are not limited to the last 3 months.

## Choice for the second adapter
**RSS news** (decided 10 Nov 2026). Reasons: no account or key, works with the standard library, many Indonesian outlets publish feeds, and it can show live data in a demo. GDELT stays the candidate for the frozen snapshot (F4).

## What this means for us
- **RSS feeds hold only recent items** (from about an hour up to a few weeks). They suit a **live demo**, where the adapter reads the feed at the moment of the question. They do **not** suit the frozen benchmark snapshot (F4), which needs a fixed, repeatable period.
- **Don't commit or publish article text from news feeds.** The tests use synthetic feeds that I wrote (`tests/fixtures/`). A live demo may read feeds on the fly and show titles with links back to the publisher.
- **For F4 (frozen snapshot)**, the best candidate is **GDELT** (clear redistribution terms; links and metadata rather than full text), once the API works without the rate limit. The Hugging Face news set (8) has unclear rights; IndoNLU (7) has no dates.
- The RSS adapter's optional `fetch_feed()` uses a generic User-Agent (`social-listening-mcp/<version> (feed reader)`), a 10-second timeout and a 5 MB limit. Fetch feeds rarely (minutes, not seconds) and respect each site's terms.

## XML safety
Feeds that contain a `DOCTYPE` or `ENTITY` declaration are refused before parsing (`FeedError`). So no external entity is ever resolved and entity-expansion ("billion laughs") input can't run. Python's `xml.etree` (expat) also does not fetch external DTDs. This keeps the adapter on the standard library, without `defusedxml`.

## Not verified
- The detailed terms of use of each news site (only the feeds were checked).
- The GDELT API response (rate-limited during the check).
- The original data licences behind the two Hugging Face datasets (only the dataset cards).
